"""Raccourcis des touches G (sans matériel).

    python -m unittest tests.test_gkeys -v
"""
import queue
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import g19_dashboard as g


class GKeyTest(unittest.TestCase):
    def app(self, keys=("monitoring", "reseau", "serveurs", "proxmox", "docker"), native=True, gmap=None):
        a = object.__new__(g.HudApp)
        a.native = native
        a.screens = [(k.upper(), SimpleNamespace(key=k)) for k in keys]
        a.gkey_map = g.parse_gkey_map(gmap or g.DEFAULT_GMAP)
        return a

    def target(self, a, n):
        i = a._gkey_target(n)
        return None if i is None else a.screens[i][1].key

    def test_default_layout(self):
        a = self.app()
        got = {n: self.target(a, n) for n in range(1, 13)}
        self.assertEqual({n: v for n, v in got.items() if v},
                         {1: "reseau", 7: "serveurs", 2: "proxmox", 8: "docker", 3: "monitoring"})

    def test_follows_screen_name_not_position(self):
        a = self.app(keys=("docker", "monitoring"))
        self.assertEqual(self.target(a, 3), "monitoring")
        self.assertEqual(self.target(a, 8), "docker")
        self.assertIsNone(a._gkey_target(2))  # Proxmox non configuré : touche sans effet

    def test_custom_map_ignores_bad_entries(self):
        self.assertEqual(g.parse_gkey_map("g12 = Docker, G13=reseau, F1=x, G4="), {12: "docker"})

    def test_hardware_mask_emits_gkey_events(self):
        c = object.__new__(g.G19Controller)
        c.event_queue = queue.Queue()
        for mask in (1 << 6, 1 << 6, 0, 1 << 2):  # G7 maintenue (un seul évènement), relâchée, puis G3
            c._handle_gkeys(mask)
        self.assertEqual([c.event_queue.get_nowait() for _ in range(c.event_queue.qsize())], ["GKEY:7", "GKEY:3"])


if __name__ == "__main__":
    unittest.main()
