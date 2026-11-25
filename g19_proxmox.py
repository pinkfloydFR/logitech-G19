"""Client minimal de l'API Proxmox VE pour l'écran « PROXMOX » du HUD G19 (lecture seule).

Configuration (config.toml, section [proxmox]) — les variables d'environnement PVE_* l'emportent
(pratique pour le service systemd : /etc/default/g19-hud, lisible par root uniquement) :

    [proxmox]
    host = "127.0.0.1"           # PVE_HOST
    port = 8006                  # PVE_PORT
    token_id = "g19hud@pve!hud"  # PVE_TOKEN_ID   (utilisateur@realm!nom_du_jeton)
    token_secret = "xxxxxxxx-..."  # PVE_TOKEN_SECRET
    verify_ssl = false           # PVE_VERIFY_SSL   (certificat auto-signé par défaut sur Proxmox)
    fingerprint = ""             # PVE_FINGERPRINT  (SHA-256 du certificat : épingle le serveur si verify_ssl=false)
    interval_sec = 5

Le jeton n'a besoin que du rôle PVEAuditor (voir linux/proxmox-token.sh). Mode démo sans serveur : PVE_DEMO=1.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import ssl
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Guest:
    vmid: int
    name: str
    kind: str          # "qemu" | "lxc"
    node: str
    status: str        # running | stopped | paused ...
    cpu: float         # 0..1 (fraction des vCPU alloués)
    maxcpu: int
    mem: int           # octets
    maxmem: int
    uptime: int


@dataclass
class NodeInfo:
    name: str
    status: str
    cpu: float
    mem: int
    maxmem: int
    maxcpu: int = 0
    uptime: int = 0


@dataclass
class Settings:
    host: str = ""
    port: int = 8006
    token_id: str = ""
    token_secret: str = ""
    verify_ssl: bool = False
    fingerprint: str = ""
    interval_sec: float = 5.0
    demo: bool = False

    @property
    def configured(self) -> bool:
        return self.demo or bool(self.host and self.token_id and self.token_secret)


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on", "oui")


def load_settings(config_file: Optional[Path] = None) -> Settings:
    s = Settings()
    if config_file and config_file.exists():
        try:
            sect = tomllib.loads(config_file.read_text(encoding="utf-8")).get("proxmox", {})
            s.host = str(sect.get("host", s.host))
            s.port = int(sect.get("port", s.port))
            s.token_id = str(sect.get("token_id", s.token_id))
            s.token_secret = str(sect.get("token_secret", s.token_secret))
            s.verify_ssl = bool(sect.get("verify_ssl", s.verify_ssl))
            s.fingerprint = str(sect.get("fingerprint", s.fingerprint))
            s.interval_sec = max(2.0, float(sect.get("interval_sec", s.interval_sec)))
        except Exception as e:
            print(f"[PVE] config.toml illisible : {e}")
    env = os.environ
    s.host = env.get("PVE_HOST", s.host)
    s.port = int(env.get("PVE_PORT", s.port))
    s.token_id = env.get("PVE_TOKEN_ID", s.token_id)
    s.token_secret = env.get("PVE_TOKEN_SECRET", s.token_secret)
    if "PVE_VERIFY_SSL" in env:
        s.verify_ssl = _truthy(env["PVE_VERIFY_SSL"])
    s.fingerprint = env.get("PVE_FINGERPRINT", s.fingerprint)
    s.demo = _truthy(env.get("PVE_DEMO", "0"))
    return s


def _norm_fp(fp: str) -> str:
    return fp.replace(":", "").replace(" ", "").lower()


def parse_resources(data: list[dict]) -> tuple[list[NodeInfo], list[Guest]]:
    nodes, guests = [], []
    for r in data:
        t = r.get("type")
        if t == "node":
            nodes.append(NodeInfo(r.get("node", "?"), r.get("status", "?"), float(r.get("cpu", 0) or 0),
                                  int(r.get("mem", 0) or 0), int(r.get("maxmem", 0) or 0),
                                  int(r.get("maxcpu", 0) or 0), int(r.get("uptime", 0) or 0)))
        elif t in ("qemu", "lxc") and not r.get("template"):
            guests.append(Guest(int(r.get("vmid", 0)), r.get("name") or f"{t}-{r.get('vmid')}", t,
                                r.get("node", "?"), r.get("status", "?"), float(r.get("cpu", 0) or 0),
                                int(r.get("maxcpu", 1) or 1), int(r.get("mem", 0) or 0),
                                int(r.get("maxmem", 0) or 0), int(r.get("uptime", 0) or 0)))
    nodes.sort(key=lambda n: n.name)
    guests.sort(key=lambda g: g.vmid)
    return nodes, guests


class ProxmoxClient:
    """Interroge /cluster/resources en arrière-plan ; snapshot() est sans blocage."""

    def __init__(self, settings: Settings):
        self.s = settings
        self._lock = threading.Lock()
        self._nodes: list[NodeInfo] = []
        self._guests: list[Guest] = []
        self._error: Optional[str] = None
        self._last_ok = 0.0
        self.running = True
        if settings.configured:
            threading.Thread(target=self._loop, daemon=True).start()

    # --- API ---
    def snapshot(self) -> tuple[list[NodeInfo], list[Guest], Optional[str], float]:
        with self._lock:
            age = time.monotonic() - self._last_ok if self._last_ok else -1.0
            return list(self._nodes), list(self._guests), self._error, age

    def close(self):
        self.running = False

    # --- interne ---
    def _loop(self):
        while self.running:
            try:
                nodes, guests = self._demo() if self.s.demo else self._fetch()
                with self._lock:
                    self._nodes, self._guests, self._error, self._last_ok = nodes, guests, None, time.monotonic()
            except Exception as e:  # réseau, jeton, TLS...
                with self._lock:
                    self._error = str(e)[:80]
            time.sleep(self.s.interval_sec if not self.s.demo else 1.0)

    def _fetch(self) -> tuple[list[NodeInfo], list[Guest]]:
        ctx = ssl.create_default_context() if self.s.verify_ssl else ssl._create_unverified_context()
        conn = http.client.HTTPSConnection(self.s.host, self.s.port, timeout=5, context=ctx)
        try:
            conn.connect()
            if self.s.fingerprint:
                der = conn.sock.getpeercert(binary_form=True)
                if hashlib.sha256(der).hexdigest() != _norm_fp(self.s.fingerprint):
                    raise RuntimeError("empreinte TLS inattendue")
            conn.request("GET", "/api2/json/cluster/resources", headers={
                "Authorization": f"PVEAPIToken={self.s.token_id}={self.s.token_secret}",
                "Accept": "application/json",
            })
            resp = conn.getresponse()
            body = resp.read()
            if resp.status == 401:
                raise RuntimeError("jeton refusé (401)")
            if resp.status == 403:
                raise RuntimeError("droits insuffisants (403)")
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            return parse_resources(json.loads(body)["data"])
        finally:
            conn.close()

    # --- démo (PVE_DEMO=1 / captures d'écran / développement sans serveur) ---
    def _demo(self) -> tuple[list[NodeInfo], list[Guest]]:
        t = time.time()
        GB = 1024 ** 3
        spec = [  # vmid, nom, type, running, vcpu, ram_max_go, ram_utilisée_go, base_cpu
            (100, "homeassistant", "qemu", True, 4, 8, 3.1, 0.12),
            (101, "nextcloud", "qemu", True, 4, 8, 5.6, 0.25),
            (102, "jellyfin", "lxc", True, 4, 4, 1.4, 0.35),
            (103, "pihole", "lxc", True, 1, 1, 0.2, 0.03),
            (104, "docker-arr", "qemu", True, 6, 16, 11.2, 0.40),
            (105, "play-tp", "qemu", True, 4, 8, 6.9, 0.18),
            (106, "windows-test", "qemu", False, 4, 8, 0.0, 0.0),
            (107, "backup-pbs", "lxc", True, 2, 4, 1.1, 0.05),
            (108, "dev-sandbox", "lxc", False, 2, 2, 0.0, 0.0),
        ]
        guests = []
        for i, (vmid, name, kind, run, vcpu, rmax, rused, base) in enumerate(spec):
            cpu = max(0.0, min(1.0, base + 0.10 * math.sin(t / 3 + i) + 0.04 * math.sin(t * 1.7 + i * 2))) if run else 0.0
            guests.append(Guest(vmid, name, kind, "pve", "running" if run else "stopped", cpu, vcpu,
                                int(rused * GB * (1 + 0.02 * math.sin(t / 9 + i))) if run else 0, rmax * GB,
                                int(t % 86400 * 3) if run else 0))
        node = NodeInfo("pve", "online", 0.31 + 0.08 * math.sin(t / 4), int(44.5 * GB), 64 * GB, 16, 86400 * 12)
        return [node], guests
