"""Historique CPU / RAM sur plusieurs heures pour l'écran Monitoring.

Les mesures (1/s) sont moyennées par tranche de 72 s : 300 tranches = 6 h, soit 1 tranche par pixel de courbe.
L'historique est enregistré dans history.json à chaque tranche terminée, pour survivre à un redémarrage du service.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

SPAN_SEC = 6 * 3600
BUCKETS = 300
BUCKET_SEC = SPAN_SEC // BUCKETS  # 72 s


class MetricsHistory:
    """points() -> liste triée de (tranche, cpu_moy, cpu_max, ram_moy, ram_max), tranche en cours comprise."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self._lock = threading.Lock()
        self._done: list[tuple] = []
        self._cur: Optional[list] = None  # [tranche, n, somme_cpu, max_cpu, somme_ram, max_ram]
        self.running = True
        self._load(time.time())

    # --- écriture ---
    def add(self, cpu: float, ram: float, now: Optional[float] = None):
        now = time.time() if now is None else now
        idx = int(now // BUCKET_SEC)
        closed = False
        with self._lock:
            if self._cur is not None and self._cur[0] != idx:
                self._done.append(self._summary(self._cur))
                self._cur, closed = None, True
            if self._cur is None:
                self._cur = [idx, 0, 0.0, 0.0, 0.0, 0.0]
            c = self._cur
            c[1] += 1
            c[2] += cpu
            c[3] = max(c[3], cpu)
            c[4] += ram
            c[5] = max(c[5], ram)
            self._trim(idx)
        if closed:
            self.save()

    def points(self, now: Optional[float] = None) -> list[tuple]:
        idx = int((time.time() if now is None else now) // BUCKET_SEC)
        with self._lock:
            pts = self._done + ([self._summary(self._cur)] if self._cur else [])
        return [p for p in pts if idx - BUCKETS < p[0] <= idx]

    def start(self, source):
        """Échantillonne source() -> (cpu, ram) chaque seconde en arrière-plan."""
        def loop():
            time.sleep(1.5)  # laisse la première mesure CPU se faire
            while self.running:
                self.add(*source())
                time.sleep(1.0)
        threading.Thread(target=loop, daemon=True).start()
        return self

    # --- interne ---
    @staticmethod
    def _summary(c) -> tuple:
        return (c[0], c[2] / c[1], c[3], c[4] / c[1], c[5])

    def _trim(self, idx: int):
        while self._done and self._done[0][0] <= idx - BUCKETS:
            self._done.pop(0)

    def save(self):
        if not self.path:
            return
        with self._lock:
            data = {"bucket_sec": BUCKET_SEC, "points": [[round(v, 1) for v in p] for p in self._done]}
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # dossier en lecture seule : l'historique reste en mémoire

    def _load(self, now: float):
        if not self.path or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("bucket_sec") != BUCKET_SEC:
                return
            idx = int(now // BUCKET_SEC)
            pts = [(int(p[0]), *map(float, p[1:5])) for p in data["points"]]
            self._done = sorted(p for p in pts if len(p) == 5 and idx - BUCKETS < p[0] < idx)
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            print("[HIST] history.json illisible, historique repris à zéro")
