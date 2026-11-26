"""Client minimal de l'API Docker Engine (lecture seule) pour l'écran « DOCKER » du HUD G19.

Surveille quelques conteneurs précis (ou tous) : état, santé, CPU, mémoire.

Configuration (config.toml, section [docker]) — les variables d'environnement l'emportent :

    [docker]
    host = "unix:///var/run/docker.sock"   # DOCKER_HOST  (ou "tcp://192.168.0.6:2375")
    containers = ["nextcloud", "jellyfin"]  # G19_DOCKER_CONTAINERS="nextcloud,jellyfin"  (vide = tous)
    interval_sec = 5

⚠️ SÉCURITÉ : l'accès au socket Docker équivaut à root sur l'hôte. N'ajoutez pas l'utilisateur du HUD au groupe
docker sans y réfléchir. Préférez un proxy en lecture seule (tecnativa/docker-socket-proxy avec CONTAINERS=1,
tout le reste à 0) et pointez `host = "tcp://127.0.0.1:2375"` dessus. Jamais de port 2375 non filtré sur le réseau.

Mode démo sans Docker : DOCKER_DEMO=1.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import re
import socket
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_SOCKET = "/var/run/docker.sock"


@dataclass
class Container:
    name: str
    state: str                 # running | exited | paused | restarting | created | dead | missing
    health: str = ""           # healthy | unhealthy | starting | ""
    status_text: str = ""      # « Up 3 hours (healthy) »
    image: str = ""
    cpu_pct: Optional[float] = None
    mem_bytes: Optional[int] = None
    mem_limit: Optional[int] = None


@dataclass
class Settings:
    host: str = ""
    containers: list = field(default_factory=list)
    interval_sec: float = 5.0
    demo: bool = False

    @property
    def configured(self) -> bool:
        return self.demo or bool(self.host)


def load_settings(config_file: Optional[Path] = None) -> Settings:
    s = Settings()
    explicit = False
    if config_file and config_file.exists():
        try:
            sect = tomllib.loads(config_file.read_text(encoding="utf-8")).get("docker")
            if sect is not None:
                explicit = True
                s.host = str(sect.get("host", "")) or f"unix://{DEFAULT_SOCKET}"
                s.containers = [str(c) for c in sect.get("containers", [])]
                s.interval_sec = max(2.0, float(sect.get("interval_sec", s.interval_sec)))
        except Exception as e:
            print(f"[DOCKER] config.toml illisible : {e}")
    env = os.environ
    if env.get("DOCKER_HOST"):
        s.host, explicit = env["DOCKER_HOST"], True
    if "G19_DOCKER_CONTAINERS" in env:
        s.containers = [c.strip() for c in env["G19_DOCKER_CONTAINERS"].split(",") if c.strip()]
    s.demo = env.get("DOCKER_DEMO", "0").strip().lower() in ("1", "true", "yes", "on")
    if not explicit and not s.host and os.path.exists(DEFAULT_SOCKET) and os.access(DEFAULT_SOCKET, os.R_OK | os.W_OK):
        s.host = f"unix://{DEFAULT_SOCKET}"   # détection automatique du socket local accessible
    return s


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 5):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def _connection(host: str, timeout: float = 5) -> http.client.HTTPConnection:
    if host.startswith("unix://"):
        return _UnixHTTPConnection(host[len("unix://"):], timeout)
    m = re.match(r"^(?:tcp|http)://([^:/]+)(?::(\d+))?/?$", host)
    if not m:
        raise ValueError(f"DOCKER_HOST non supporté : {host!r} (unix://… ou tcp://hôte:port)")
    return http.client.HTTPConnection(m.group(1), int(m.group(2) or 2375), timeout=timeout)


def _get(host: str, path: str, timeout: float = 5):
    conn = _connection(host, timeout)
    try:
        conn.request("GET", path, headers={"Accept": "application/json"})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status == 403:
            raise RuntimeError("accès refusé par le proxy Docker (403)")
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return json.loads(body)
    finally:
        conn.close()


def parse_list_entry(e: dict) -> Container:
    names = e.get("Names") or ["/?"]
    status = e.get("Status", "")
    m = re.search(r"\((healthy|unhealthy|health: starting)\)", status)
    health = {"health: starting": "starting"}.get(m.group(1), m.group(1)) if m else ""
    return Container(names[0].lstrip("/"), e.get("State", "?"), health, status, e.get("Image", ""))


def cpu_percent(st: dict) -> Optional[float]:
    try:
        cpu, pre = st["cpu_stats"], st["precpu_stats"]
        cd = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        sd = cpu["system_cpu_usage"] - pre["system_cpu_usage"]
        if sd <= 0 or cd < 0:
            return None
        ncpu = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
        return 100.0 * cd / sd * ncpu
    except (KeyError, TypeError):
        return None


def mem_usage(st: dict) -> tuple[Optional[int], Optional[int]]:
    """Mémoire utilisée comme `docker stats` (usage - cache inactif), et la limite."""
    try:
        ms = st["memory_stats"]
        stats = ms.get("stats", {})
        cache = stats.get("inactive_file", stats.get("total_inactive_file", 0))
        return max(0, ms["usage"] - cache), ms.get("limit")
    except (KeyError, TypeError):
        return None, None


class DockerClient:
    """Interroge Docker en arrière-plan ; snapshot() est sans blocage."""

    MAX_STATS = 12

    def __init__(self, settings: Settings):
        self.s = settings
        self._lock = threading.Lock()
        self._containers: list[Container] = []
        self._error: Optional[str] = None
        self._last_ok = 0.0
        self.running = True
        if settings.configured:
            threading.Thread(target=self._loop, daemon=True).start()

    def snapshot(self) -> tuple[list[Container], Optional[str], float]:
        with self._lock:
            age = time.monotonic() - self._last_ok if self._last_ok else -1.0
            return list(self._containers), self._error, age

    def close(self):
        self.running = False

    def _loop(self):
        while self.running:
            try:
                cs = self._demo() if self.s.demo else self._fetch()
                with self._lock:
                    self._containers, self._error, self._last_ok = cs, None, time.monotonic()
            except Exception as e:
                with self._lock:
                    self._error = str(e)[:80]
            time.sleep(1.0 if self.s.demo else self.s.interval_sec)

    def _fetch(self) -> list[Container]:
        entries = _get(self.s.host, "/containers/json?all=1")
        found = {c.name: c for c in map(parse_list_entry, entries)}
        if self.s.containers:
            result = [found.get(n) or Container(n, "missing") for n in self.s.containers]
        else:
            result = sorted(found.values(), key=lambda c: c.name)
        running = [c for c in result if c.state in ("running", "restarting")][: self.MAX_STATS]

        def stats(c: Container):
            try:
                st = _get(self.s.host, f"/containers/{c.name}/stats?stream=false", timeout=6)
                c.cpu_pct = cpu_percent(st)
                c.mem_bytes, c.mem_limit = mem_usage(st)
            except Exception:
                pass

        if running:
            with ThreadPoolExecutor(max_workers=min(6, len(running))) as ex:
                list(ex.map(stats, running))
        return result

    # --- démo (DOCKER_DEMO=1 / captures d'écran) ---
    def _demo(self) -> list[Container]:
        t = time.time()
        MB = 1024 ** 2
        spec = [  # nom, état, santé, texte, cpu_base, mem_Mo, limite_Mo
            ("nextcloud", "running", "healthy", "Up 12 days (healthy)", 6.0, 640, 2048),
            ("jellyfin", "running", "healthy", "Up 3 days (healthy)", 18.0, 1450, 4096),
            ("grafana", "running", "", "Up 3 days", 1.5, 310, 1024),
            ("paperless", "running", "unhealthy", "Up 2 hours (unhealthy)", 2.2, 280, 1024),
            ("web-app", "restarting", "", "Restarting (1) 5 seconds ago", 0.0, 0, 0),
            ("backup-job", "exited", "", "Exited (0) 8 hours ago", 0.0, 0, 0),
        ]
        out = []
        for i, (n, st, h, txt, cpu, mem, lim) in enumerate(spec):
            c = Container(n, st, h, txt, f"lscr.io/linuxserver/{n}")
            if st == "running":
                c.cpu_pct = max(0.0, cpu * (1 + 0.5 * math.sin(t / 3 + i)))
                c.mem_bytes, c.mem_limit = int(mem * MB * (1 + 0.02 * math.sin(t / 8 + i))), lim * MB
            out.append(c)
        return out
