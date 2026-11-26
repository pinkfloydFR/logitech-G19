"""Cadence : images inchangées non renvoyées, réveil immédiat sur touche (sans matériel).

    python -m unittest tests.test_frames -v
"""
import queue
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image

import g19_dashboard as g


class FakeDev:
    def __init__(self):
        self.sent, self.fail = 0, False

    def send_frame(self, data):
        if self.fail:
            raise OSError("boom")
        self.sent += 1


@unittest.skipUnless(g.HAS_G19, "pyusb absent")
class SendImageTest(unittest.TestCase):
    def make(self):
        c = object.__new__(g.G19Controller)  # sans __init__ : aucun accès USB
        c.dev, c.connected, c._last_raw, c._last_sent = FakeDev(), True, None, 0.0
        c._encode_frame, c._debug = (lambda img: b""), False
        c.dropped = False
        c._mark_disconnected = lambda: setattr(c, "dropped", True)
        return c

    def img(self, color):
        return Image.new("RGB", (320, 240), color)

    def test_unchanged_frame_is_skipped_and_changed_is_sent(self):
        c = self.make()
        for color in ("black", "black", "black", "red", "red", "black"):
            c.send_image(self.img(color))
        self.assertEqual(c.dev.sent, 3)

    def test_keepalive_resends(self):
        c = self.make()
        c.send_image(self.img("black"))
        c._last_sent -= c.KEEPALIVE_SEC + 0.1
        c.send_image(self.img("black"))
        self.assertEqual(c.dev.sent, 2)

    def test_failed_send_is_not_remembered(self):
        c = self.make()
        c.dev.fail = True
        c.send_image(self.img("red"))
        self.assertTrue(c.dropped)
        c.dev.fail = False
        c.send_image(self.img("red"))
        self.assertEqual(c.dev.sent, 1)


class WaitTest(unittest.TestCase):
    def make(self):
        app = object.__new__(g.HudApp)
        app.running = True
        app.hw = type("H", (), {})()
        app.hw.event_queue = queue.Queue()
        return app

    def test_waits_full_period_without_event(self):
        app, t0 = self.make(), time.monotonic()
        app._wait_next_frame(t0, 5)
        self.assertGreaterEqual(time.monotonic() - t0, 0.19)

    def test_key_press_wakes_early(self):
        app, t0 = self.make(), time.monotonic()
        threading.Timer(0.05, app.hw.event_queue.put, args=(1,)).start()
        app._wait_next_frame(t0, 1)  # période d'1 s
        self.assertLess(time.monotonic() - t0, 0.3)


if __name__ == "__main__":
    unittest.main()
