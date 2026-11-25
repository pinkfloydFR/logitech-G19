"""Pilote USB minimal pour l'écran couleur 320x240 et les touches du Logitech G19.

Protocole d'après libg19 (jgeboski) / LCDHost. Linux (libusb système) et Windows (WinUSB + libusb-package, voir windows/).
Périphérique : 046d:c229 ("G19 Gaming Keyboard Macro Interface" / "Display interface")
  - interface 0 (vendor) : EP 0x02 OUT bulk  -> trames LCD (512 o d'en-tête + 320*240*2 o RGB565)
                           EP 0x81 IN  intr  -> 2 octets, touches LCD (Home/Cancel/Menu/OK/Droite/Gauche/Bas/Haut)
  - interface 1 (HID)    : EP 0x83 IN  intr  -> 4 o touches G / M (non utilisé ici)
  - SET_REPORT 0x0307    : rétro-éclairage RGB ; 0x0305 : LED M1-M3/MR
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Optional

import usb.core
import usb.util

try:  # Windows : libusb-package embarque libusb-1.0.dll (pip install libusb-package)
    import libusb_package
    _BACKEND = libusb_package.get_libusb1_backend()
except Exception:
    _BACKEND = None  # Linux/macOS : backend libusb système


def _find():
    return usb.core.find(idVendor=VID, idProduct=PID_LCD, backend=_BACKEND)


VID = 0x046D
PID_LCD = 0xC229

LCD_W = 320
LCD_H = 240
FRAME_BYTES = LCD_W * LCD_H * 2

# En-tête de 512 octets : 16 octets utiles puis motif incrémental (identique à libg19-data.c)
_HEADER = bytes(
    [0x10, 0x0F, 0x00, 0x58, 0x02, 0, 0, 0, 0, 0, 0, 0x3F, 0x01, 0xEF, 0x00, 0x0F]
) + bytes((0x10 + i) & 0xFF for i in range(512 - 16))

# Bits de l'octet 0 du rapport EP 0x81 (ordre de libg19)
LKEY_HOME = 1 << 0
LKEY_CANCEL = 1 << 1
LKEY_MENU = 1 << 2
LKEY_OK = 1 << 3
LKEY_RIGHT = 1 << 4
LKEY_LEFT = 1 << 5
LKEY_DOWN = 1 << 6
LKEY_UP = 1 << 7


def rgb_to_rgb565_le(rgb_bytes: bytes) -> bytes:
    """Convertit du RGB888 (w*h*3) en RGB565 little-endian (w*h*2)."""
    try:
        import numpy as np

        a = np.frombuffer(rgb_bytes, dtype=np.uint8).reshape(-1, 3).astype(np.uint16)
        v = ((a[:, 0] & 0xF8) << 8) | ((a[:, 1] & 0xFC) << 3) | (a[:, 2] >> 3)
        return v.astype("<u2").tobytes()
    except ImportError:
        out = bytearray(len(rgb_bytes) // 3 * 2)
        j = 0
        for i in range(0, len(rgb_bytes), 3):
            v = ((rgb_bytes[i] & 0xF8) << 8) | ((rgb_bytes[i + 1] & 0xFC) << 3) | (rgb_bytes[i + 2] >> 3)
            out[j] = v & 0xFF
            out[j + 1] = v >> 8
            j += 2
        return bytes(out)


class G19Device:
    """Accès bas niveau au G19. Lève usb.core.USBError en cas de déconnexion."""

    def __init__(self):
        self.dev = None
        self._lock = threading.Lock()

    @staticmethod
    def present() -> bool:
        return _find() is not None

    def open(self) -> None:
        # Windows : tant que WinUSB n'est pas installé sur l'INTERFACE 1 (Zadig, 046D C229 01), lire EP 0x83
        # échoue en I/O error et INVALIDE le handle (« Entity not found » ensuite, même pour l'écran).
        # Les touches G/M ne sont donc activées sous Windows que si G19_GKEYS=1 (après l'install Zadig n°2).
        want = sys.platform != "win32" or os.environ.get("G19_GKEYS") == "1"
        self._open_once(want_gkeys=want)

    def _open_once(self, want_gkeys: bool) -> None:
        dev = _find()
        if dev is None:
            raise usb.core.USBError("G19 (046d:c229) introuvable")
        try:
            dev.set_configuration()
        except usb.core.USBError:
            pass  # déjà configuré
        for intf in (0, 1):
            try:
                if dev.is_kernel_driver_active(intf):
                    dev.detach_kernel_driver(intf)
            except (usb.core.USBError, NotImplementedError):
                pass
        usb.util.claim_interface(dev, 0)
        self.has_gkeys = False
        if want_gkeys:
            try:
                usb.util.claim_interface(dev, 1)  # interface HID : touches G / M
                self.has_gkeys = True
            except usb.core.USBError:
                pass
        self.dev = dev

    def close(self) -> None:
        dev, self.dev = self.dev, None
        if dev is not None:
            try:
                usb.util.release_interface(dev, 1)
            except Exception:
                pass
            try:
                usb.util.release_interface(dev, 0)
            except Exception:
                pass
            try:
                usb.util.dispose_resources(dev)
            except Exception:
                pass

    def send_frame(self, rgb565: bytes) -> None:
        if len(rgb565) != FRAME_BYTES:
            raise ValueError(f"trame de {len(rgb565)} octets au lieu de {FRAME_BYTES}")
        with self._lock:
            if self.dev is None:
                raise usb.core.USBError("périphérique fermé")
            self.dev.write(0x02, _HEADER + rgb565, timeout=1000)

    def read_lkeys(self, timeout_ms: int = 30) -> Optional[bytes]:
        """Lit un rapport de touches LCD (2 octets) ou None si rien (timeout)."""
        try:
            return bytes(self.dev.read(0x81, 2, timeout=timeout_ms))
        except usb.core.USBTimeoutError:
            return None

    def read_gkeys(self, timeout_ms: int = 20) -> Optional[int]:
        """Touches G/M (EP 0x83, rapport 02 b1 b2 b3), mesuré sur un G19 réel.

        Masque retourné : G1..G12 = bits 0..11, M1/M2/M3/MR = bits 12..15.
        L'octet 3 vaut 0x40 au repos ; 0x08 = touche « soleil » (luminosité, gérée par le firmware).
        """
        if not getattr(self, "has_gkeys", False):
            return None
        try:
            r = bytes(self.dev.read(0x83, 8, timeout=timeout_ms))
        except usb.core.USBTimeoutError:
            return None
        except usb.core.USBError:
            # Windows : l interface 1 reste sous le pilote HID tant que WinUSB n y est pas installé (Zadig).
            # Les touches G/M sont alors désactivées, l écran et les touches LCD continuent de marcher.
            self.has_gkeys = False
            return None
        if len(r) < 3 or r[0] != 0x02:
            return None
        return r[1] | (r[2] << 8)

    def _set_report(self, report_id: int, payload: bytes) -> None:
        if not self.has_gkeys:
            # Ces SET_REPORT visent l'interface 1 : sous Windows sans WinUSB dessus, ils INVALIDENT le handle.
            return
        with self._lock:
            self.dev.ctrl_transfer(0x21, 0x09, 0x0300 | report_id, 1, bytes([report_id]) + payload, timeout=500)

    def set_backlight(self, r: int, g: int, b: int) -> None:
        self._set_report(0x07, bytes([r & 0xFF, g & 0xFF, b & 0xFF]))

    def set_brightness(self, percent: int) -> None:
        """Luminosité de l'écran LCD (0-100). Le firmware la change aussi avec la touche « soleil » près de F12."""
        with self._lock:
            self.dev.ctrl_transfer(0x41, 0x0A, 0x00, 0x00, bytes([max(0, min(100, int(percent)))]), timeout=500)

    def set_mkeys(self, m1: bool = False, m2: bool = False, m3: bool = False, mr: bool = False) -> None:
        bits = (0x80 if m1 else 0) | (0x40 if m2 else 0) | (0x20 if m3 else 0) | (0x10 if mr else 0)
        self._set_report(0x05, bytes([bits]))
