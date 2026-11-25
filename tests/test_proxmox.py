"""Tests du client API Proxmox contre un faux serveur HTTPS local (certificat auto-signé généré avec openssl).

    python -m unittest tests.test_proxmox -v
"""
import hashlib
import http.server
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import g19_proxmox as pve

TOKEN_ID, SECRET = "g19hud@pve!hud", "11111111-2222-3333-4444-555555555555"
RESOURCES = {"data": [
    {"type": "node", "node": "pve", "status": "online", "cpu": 0.25, "mem": 8 * 1024 ** 3, "maxmem": 32 * 1024 ** 3, "maxcpu": 8, "uptime": 1000},
    {"type": "qemu", "vmid": 100, "name": "ha", "node": "pve", "status": "running", "cpu": 0.1, "maxcpu": 2, "mem": 2 * 1024 ** 3, "maxmem": 4 * 1024 ** 3, "uptime": 50},
    {"type": "lxc", "vmid": 101, "name": "dns", "node": "pve", "status": "stopped", "cpu": 0, "maxcpu": 1, "mem": 0, "maxmem": 512 * 1024 ** 2, "uptime": 0},
    {"type": "qemu", "vmid": 900, "name": "tmpl", "node": "pve", "status": "stopped", "template": 1, "maxcpu": 1, "mem": 0, "maxmem": 1},
    {"type": "storage", "id": "storage/pve/local", "status": "available"},
]}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path != "/api2/json/cluster/resources":
            self.send_response(404); self.end_headers(); return
        auth = self.headers.get("Authorization", "")
        if auth == f"PVEAPIToken={TOKEN_ID}={SECRET}":
            body = json.dumps(RESOURCES).encode()
            self.send_response(200)
        elif auth == "PVEAPIToken=readonly=nope":
            body = b"{}"; self.send_response(403)
        else:
            body = b"{}"; self.send_response(401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@unittest.skipUnless(shutil.which("openssl"), "openssl absent")
class ProxmoxClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        key, crt = f"{cls.tmp}/k.pem", f"{cls.tmp}/c.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt,
                        "-days", "2", "-subj", "/CN=127.0.0.1"], check=True, capture_output=True)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.srv.socket = ctx.wrap_socket(cls.srv.socket, server_side=True)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        der = ssl.PEM_cert_to_DER_cert(Path(crt).read_text())
        cls.fp = hashlib.sha256(der.encode("latin1") if isinstance(der, str) else der).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def settings(self, **kw):
        s = pve.Settings(host="127.0.0.1", port=self.port, token_id=TOKEN_ID, token_secret=SECRET, interval_sec=2)
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    def fetch(self, **kw):
        c = pve.ProxmoxClient(self.settings(**kw))
        c.running = False
        return c._fetch()

    def test_ok_filters_templates_and_storage(self):
        nodes, guests = self.fetch()
        self.assertEqual([n.name for n in nodes], ["pve"])
        self.assertEqual([(g.vmid, g.kind, g.status) for g in guests], [(100, "qemu", "running"), (101, "lxc", "stopped")])

    def test_fingerprint_ok_and_ko(self):
        self.fetch(fingerprint=self.fp)
        self.fetch(fingerprint=":".join(self.fp[i:i + 2] for i in range(0, len(self.fp), 2)).upper())
        with self.assertRaisesRegex(RuntimeError, "empreinte"):
            self.fetch(fingerprint="00" * 32)

    def test_bad_token_401_and_403(self):
        with self.assertRaisesRegex(RuntimeError, "401"):
            self.fetch(token_secret="wrong")
        with self.assertRaisesRegex(RuntimeError, "403"):
            self.fetch(token_id="readonly", token_secret="nope")

    def test_verify_ssl_rejects_selfsigned(self):
        with self.assertRaises(ssl.SSLError):
            self.fetch(verify_ssl=True)

    def test_background_loop_and_snapshot(self):
        c = pve.ProxmoxClient(self.settings())
        for _ in range(40):
            nodes, guests, err, age = c.snapshot()
            if guests:
                break
            time.sleep(0.1)
        c.close()
        self.assertEqual(len(guests), 2)
        self.assertIsNone(err)

    def test_unreachable_reports_error(self):
        import socket
        with socket.socket() as sk:          # port libre puis fermé : connexion refusée immédiatement
            sk.bind(("127.0.0.1", 0)); closed = sk.getsockname()[1]
        c = pve.ProxmoxClient(self.settings(port=closed))
        for _ in range(80):
            _, guests, err, _ = c.snapshot()
            if err:
                break
            time.sleep(0.1)
        c.close()
        self.assertEqual(guests, [])
        self.assertTrue(err)


class SettingsTest(unittest.TestCase):
    def test_env_overrides_file(self):
        import os
        d = tempfile.mkdtemp()
        f = Path(d) / "config.toml"
        f.write_text('[proxmox]\nhost = "a"\ntoken_id = "x@pve!t"\ntoken_secret = "s"\nverify_ssl = true\n', encoding="utf-8")
        old = dict(os.environ)
        try:
            os.environ["PVE_HOST"] = "b"; os.environ["PVE_VERIFY_SSL"] = "0"
            s = pve.load_settings(f)
            self.assertEqual((s.host, s.verify_ssl, s.configured), ("b", False, True))
            os.environ.pop("PVE_HOST")
            self.assertEqual(pve.load_settings(f).host, "a")
        finally:
            os.environ.clear(); os.environ.update(old)
            shutil.rmtree(d, ignore_errors=True)

    def test_not_configured_by_default(self):
        self.assertFalse(pve.Settings().configured)
        self.assertTrue(pve.Settings(demo=True).configured)


if __name__ == "__main__":
    unittest.main()
