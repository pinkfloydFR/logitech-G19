"""Historique CPU/RAM 6 h et préférence IPv4 de l'IP publique.

    python -m unittest tests.test_history -v
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import g19_dashboard as g
import g19_history as gh

T0 = 1_000_000 * gh.BUCKET_SEC  # début exact d'une tranche


class HistoryTest(unittest.TestCase):
    def test_bucket_average_and_max(self):
        h = gh.MetricsHistory(None)
        for i, cpu in enumerate((10, 20, 90)):
            h.add(cpu, 50, now=T0 + i)
        (_, avg, mx, ram, _), = h.points(now=T0 + 3)
        self.assertEqual((avg, mx, ram), (40, 90, 50))

    def test_only_six_hours_are_kept(self):
        h = gh.MetricsHistory(None)
        for k in range(gh.BUCKETS + 50):
            h.add(1, 1, now=T0 + k * gh.BUCKET_SEC)
        pts = h.points(now=T0 + (gh.BUCKETS + 49) * gh.BUCKET_SEC)
        self.assertEqual(len(pts), gh.BUCKETS)
        self.assertEqual(gh.BUCKETS * gh.BUCKET_SEC, 6 * 3600)

    def test_survives_restart_and_drops_old_points(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "history.json"
            now = time.time()
            h = gh.MetricsHistory(f)
            h.add(10, 20, now=now - 7 * 3600)  # trop vieux
            h.add(30, 40, now=now - 3600)
            h.add(50, 60, now=now - 600)
            h.add(70, 80, now=now)             # tranche en cours : pas encore sauvée
            again = gh.MetricsHistory(f)
            self.assertEqual([p[1] for p in again.points(now=now)], [30, 50])

    def test_corrupted_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "history.json"
            f.write_text("{pas du json", encoding="utf-8")
            self.assertEqual(gh.MetricsHistory(f).points(), [])


class PublicIpTest(unittest.TestCase):
    V4, V6 = "203.0.113.7", "2001:db8::1"

    def resolve(self, who_ip, v4_answer):
        tel = object.__new__(g.TelemetryHub)
        tel.public_ip, tel.vpn_active, calls = "---", False, []

        def fake(url, timeout=0):
            calls.append(url)
            if "ipwho" in url:
                return {"success": True, "ip": who_ip, "city": "X", "latitude": 0, "longitude": 0}
            if v4_answer is None:
                raise OSError("pas d'IPv4")
            return {"ip": v4_answer}

        tel._http_json = fake
        tel._resolve_wan_and_location(g.AppConfig())
        return tel.public_ip, len(calls)

    def test_ipv4_preferred_over_ipv6(self):
        self.assertEqual(self.resolve(self.V6, self.V4), (self.V4, 2))

    def test_ipv6_kept_when_no_ipv4(self):
        self.assertEqual(self.resolve(self.V6, None), (self.V6, 2))

    def test_no_extra_call_when_already_ipv4(self):
        self.assertEqual(self.resolve(self.V4, None), (self.V4, 1))


if __name__ == "__main__":
    unittest.main()
