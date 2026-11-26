"""Tests du client Docker contre un faux démon (HTTP/TCP, et socket Unix sous Linux).

    python -m unittest tests.test_docker -v
"""
import http.server
import json
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import g19_docker as dk

LIST = [
    {"Names": ["/nextcloud"], "State": "running", "Status": "Up 3 days (healthy)", "Image": "nextcloud"},
    {"Names": ["/paperless"], "State": "running", "Status": "Up 2 hours (unhealthy)", "Image": "paperless"},
    {"Names": ["/web"], "State": "restarting", "Status": "Restarting (1) 5 seconds ago", "Image": "nginx"},
    {"Names": ["/job"], "State": "exited", "Status": "Exited (0) 8 hours ago", "Image": "alpine"},
]
STATS = {
    "cpu_stats": {"cpu_usage": {"total_usage": 2_000_000_000}, "system_cpu_usage": 20_000_000_000, "online_cpus": 4},
    "precpu_stats": {"cpu_usage": {"total_usage": 1_000_000_000}, "system_cpu_usage": 10_000_000_000},
    "memory_stats": {"usage": 600 * 1024 ** 2, "limit": 2048 * 1024 ** 2, "stats": {"inactive_file": 100 * 1024 ** 2}},
}


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/containers/json"):
            body, code = json.dumps(LIST).encode(), 200
        elif self.path.endswith("/stats?stream=false") and "/containers/" in self.path:
            body, code = json.dumps(STATS).encode(), 200
        else:
            body, code = b"{}", 404
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class UnixHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    address_family = getattr(socket, "AF_UNIX", None)  # absent sous Windows

    def server_bind(self):
        self.socket.bind(self.server_address)
        self.server_name, self.server_port = "localhost", 0

    def get_request(self):
        r, _ = self.socket.accept()
        return r, ("unix", 0)


class ParsingTest(unittest.TestCase):
    def test_cpu_and_mem(self):
        self.assertAlmostEqual(dk.cpu_percent(STATS), 40.0)   # 1e9/1e10 * 4 cpus * 100
        used, limit = dk.mem_usage(STATS)
        self.assertEqual((used, limit), (500 * 1024 ** 2, 2048 * 1024 ** 2))   # usage - cache inactif
        self.assertIsNone(dk.cpu_percent({}))

    def test_health_parsing(self):
        self.assertEqual([dk.parse_list_entry(e).health for e in LIST], ["healthy", "unhealthy", "", ""])

    def test_bad_host_rejected(self):
        with self.assertRaises(ValueError):
            dk._connection("ssh://x")


class TcpClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.host = f"tcp://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def fetch(self, containers):
        c = dk.DockerClient(dk.Settings(host=self.host, containers=containers))
        c.running = False
        return c._fetch()

    def test_all_containers_sorted_with_stats(self):
        cs = self.fetch([])
        self.assertEqual([c.name for c in cs], ["job", "nextcloud", "paperless", "web"])
        nc = next(c for c in cs if c.name == "nextcloud")
        self.assertAlmostEqual(nc.cpu_pct, 40.0)
        self.assertIsNone(next(c for c in cs if c.name == "job").cpu_pct)   # arrêté : pas de stats

    def test_watched_list_keeps_order_and_reports_missing(self):
        cs = self.fetch(["paperless", "ghost", "nextcloud"])
        self.assertEqual([(c.name, c.state) for c in cs], [("paperless", "running"), ("ghost", "missing"), ("nextcloud", "running")])

    def test_background_loop(self):
        c = dk.DockerClient(dk.Settings(host=self.host, interval_sec=2))
        for _ in range(50):
            cs, err, age = c.snapshot()
            if cs:
                break
            time.sleep(0.1)
        c.close()
        self.assertEqual(len(cs), 4)
        self.assertIsNone(err)

    def test_unreachable_reports_error(self):
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0)); closed = sk.getsockname()[1]
        c = dk.DockerClient(dk.Settings(host=f"tcp://127.0.0.1:{closed}"))
        for _ in range(80):
            cs, err, _ = c.snapshot()
            if err:
                break
            time.sleep(0.1)
        c.close()
        self.assertEqual(cs, [])
        self.assertTrue(err)


@unittest.skipUnless(hasattr(socket, "AF_UNIX") and sys.platform != "win32", "socket Unix : Linux uniquement")
class UnixClientTest(unittest.TestCase):
    def test_unix_socket(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "docker.sock")
        srv = UnixHTTPServer(path, H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = dk.DockerClient(dk.Settings(host=f"unix://{path}"))
            c.running = False
            self.assertEqual(len(c._fetch()), 4)
        finally:
            srv.shutdown()


class SettingsTest(unittest.TestCase):
    def test_env_and_file(self):
        d = tempfile.mkdtemp()
        f = Path(d) / "config.toml"
        f.write_text('[docker]\nhost = "tcp://h:1"\ncontainers = ["a", "b"]\n', encoding="utf-8")
        old = dict(os.environ)
        try:
            for k in ("DOCKER_HOST", "G19_DOCKER_CONTAINERS", "DOCKER_DEMO"):
                os.environ.pop(k, None)
            s = dk.load_settings(f)
            self.assertEqual((s.host, s.containers, s.configured), ("tcp://h:1", ["a", "b"], True))
            os.environ["DOCKER_HOST"] = "tcp://z:2"; os.environ["G19_DOCKER_CONTAINERS"] = "x, y"
            s = dk.load_settings(f)
            self.assertEqual((s.host, s.containers), ("tcp://z:2", ["x", "y"]))
        finally:
            os.environ.clear(); os.environ.update(old)

    def test_demo_counts_as_configured(self):
        self.assertTrue(dk.Settings(demo=True).configured)
        self.assertFalse(dk.Settings().configured)


if __name__ == "__main__":
    unittest.main()
