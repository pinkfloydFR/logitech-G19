# -*- coding: utf-8 -*-
"""
Logitech G15 v2 / v1 Autonomous Dashboard (Windows 11 x64 — Zéro driver LGS/GHUB)
Version 0.2.0 — Monolithe Python autonome (voir README.md pour le guide complet).
"""

import asyncio
import ctypes
import json
import math
import os
import queue
import random
import re
import socket
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psutil
from PIL import Image, ImageDraw, ImageFont

IS_WIN = sys.platform == "win32"

# hidapi : requis uniquement pour le backend G15 (USB HID). Absent = backend G19/libusb seul.
try:
    import hid
    HAS_HID = True
except Exception:
    hid = None
    HAS_HID = False

# pyusb/libusb : requis pour le backend Logitech G19 (écran couleur 320x240)
try:
    import g19_lcd
    HAS_G19 = True
except Exception:
    g19_lcd = None
    HAS_G19 = False

# ==============================================================================
# DÉPENDANCES OPTIONNELLES (DÉGRADATION GRACIEUSE)
# ==============================================================================
try:
    import numpy as np
    HAS_NUMPY = True
except Exception:
    np = None
    HAS_NUMPY = False

try:
    import pyaudiowpatch as pyaudio
    HAS_PYAUDIO = True
except Exception:
    pyaudio = None
    HAS_PYAUDIO = False

HAS_AUDIO = HAS_NUMPY and HAS_PYAUDIO

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    )
    HAS_WINRT = True
except Exception:
    MediaManager = None
    HAS_WINRT = False

try:
    import pystray
    HAS_TRAY = True
except Exception:
    pystray = None
    HAS_TRAY = False


# ==============================================================================
# CONSTANTES MATÉRIELLES & CHEMINS
# ==============================================================================
VID = 0x046D
PIDS = (0xC227, 0xC222)  # 0xC227 = G15 v2 (ambré) | 0xC222 = G15 v1 (bleu)

BASE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
KEYMAP_FILE = BASE_DIR / "g15_keymap.json"
CONFIG_FILE = BASE_DIR / "config.toml"

LCD_W = 160
LCD_H = 43
LCD_CONTENT_H = 34  # Hauteur utile lorsque la barre des softkeys (y=34..42) est visible

BTN_APPLET = 0
BTN_1 = 1
BTN_2 = 2
BTN_3 = 3
BTN_4 = 4

DEFAULT_G15V2_KEYMAP = {
    0: {"report_id": 2, "byte": 2, "mask": 128},
    1: {"report_id": 2, "byte": 2, "mask": 2},
    2: {"report_id": 2, "byte": 2, "mask": 4},
    3: {"report_id": 2, "byte": 2, "mask": 8},
    4: {"report_id": 2, "byte": 2, "mask": 16},
}

DEFAULT_CONFIG_TOML = """# ==============================================================================
# Configuration G15-Retro-HUD v0.2.0 (éditable à chaud -> Tray : "Recharger config.toml")
# ==============================================================================

[general]
fps = 20
debug_hid = false

[network]
# true = privilégie le Wi-Fi actif avant Ethernet ; false = Ethernet avant Wi-Fi
prefer_wifi = true
ping_interval_sec = 10

[[hosts]]
name = "Gateway"
address = "192.168.1.1"

[[hosts]]
name = "Cloudflr"
address = "1.1.1.1"

[[hosts]]
name = "Google"
address = "8.8.8.8"

[weather]
auto_detect_ip = true
fallback_on_vpn = true
fallback_city = "Lausanne"
fallback_postal = "1000"
fallback_lat = 46.5197
fallback_lon = 6.6323
refresh_minutes = 10

[immersive]
softkey_hide_sec = 3.0
auto_cycle_sec = 25.0
"""


# ==============================================================================
# CONFIGURATION DYNAMIQUE (TOML)
# ==============================================================================
@dataclass
class AppConfig:
    fps: int = 20
    debug_hid: bool = False
    prefer_wifi: bool = True
    ping_interval_sec: float = 10.0
    hosts: list[tuple[str, str]] = field(
        default_factory=lambda: [
            ("Gateway", "192.168.1.1"),
            ("Cloudflr", "1.1.1.1"),
            ("Google", "8.8.8.8"),
        ]
    )
    auto_detect_ip: bool = True
    fallback_on_vpn: bool = True
    fallback_city: str = "Lausanne"
    fallback_postal: str = "1000"
    fallback_lat: float = 46.5197
    fallback_lon: float = 6.6323
    weather_refresh_sec: float = 600.0
    softkey_hide_sec: float = 3.0
    auto_cycle_sec: float = 25.0


class ConfigManager:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.ensure_exists()
        self.current = self._parse()

    def ensure_exists(self):
        if not self.path.exists():
            try:
                self.path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
                print(f"[CONFIG] Fichier par défaut généré : {self.path}")
            except OSError as exc:
                print(f"[CONFIG] Impossible d'écrire {self.path} : {exc}")

    def _parse(self) -> AppConfig:
        cfg = AppConfig()
        if not self.path.exists():
            return cfg
        try:
            raw = tomllib.loads(self.path.read_text(encoding="utf-8"))
            gen = raw.get("general", {})
            cfg.fps = max(5, min(60, int(gen.get("fps", cfg.fps))))
            cfg.debug_hid = bool(gen.get("debug_hid", cfg.debug_hid))

            net = raw.get("network", {})
            cfg.prefer_wifi = bool(net.get("prefer_wifi", cfg.prefer_wifi))
            cfg.ping_interval_sec = max(2.0, float(net.get("ping_interval_sec", cfg.ping_interval_sec)))

            hosts_raw = raw.get("hosts", [])
            if isinstance(hosts_raw, list) and hosts_raw:
                parsed_hosts = []
                for item in hosts_raw[:3]:
                    if isinstance(item, dict) and "name" in item and "address" in item:
                        parsed_hosts.append((str(item["name"])[:9], str(item["address"]).strip()))
                if parsed_hosts:
                    cfg.hosts = parsed_hosts

            wth = raw.get("weather", {})
            cfg.auto_detect_ip = bool(wth.get("auto_detect_ip", cfg.auto_detect_ip))
            cfg.fallback_on_vpn = bool(wth.get("fallback_on_vpn", cfg.fallback_on_vpn))
            cfg.fallback_city = str(wth.get("fallback_city", cfg.fallback_city))
            cfg.fallback_postal = str(wth.get("fallback_postal", cfg.fallback_postal))
            cfg.fallback_lat = float(wth.get("fallback_lat", cfg.fallback_lat))
            cfg.fallback_lon = float(wth.get("fallback_lon", cfg.fallback_lon))
            cfg.weather_refresh_sec = max(60.0, float(wth.get("refresh_minutes", 10.0)) * 60.0)

            imm = raw.get("immersive", {})
            cfg.softkey_hide_sec = max(1.0, float(imm.get("softkey_hide_sec", cfg.softkey_hide_sec)))
            cfg.auto_cycle_sec = max(5.0, float(imm.get("auto_cycle_sec", cfg.auto_cycle_sec)))
        except Exception as exc:
            print(f"[CONFIG] Erreur de lecture TOML ({exc}), conservation des valeurs par défaut.")
        return cfg

    def reload(self) -> AppConfig:
        with self._lock:
            self.current = self._parse()
            print("[CONFIG] config.toml rechargé avec succès.")
            return self.current

    def get(self) -> AppConfig:
        with self._lock:
            return self.current


# ==============================================================================
# POLICES BITMAP STRICTES (SANS ANTIALIASING) & PRIMITIVES 1-BIT
# ==============================================================================
def _find_win_font(candidates: tuple[str, ...], size: int, fallback):
    if IS_WIN:
        font_dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    else:
        font_dirs = [
            Path("/usr/share/fonts/truetype/dejavu"),
            Path("/usr/share/fonts/truetype/liberation"),
            Path("/usr/share/fonts/truetype/liberation2"),
            Path("/usr/share/fonts/dejavu"),
            Path("/usr/share/fonts/TTF"),
            Path.home() / ".fonts",
        ]
    for name in candidates:
        for target in [d / name for d in font_dirs] + [Path(name)]:
            try:
                return ImageFont.truetype(str(target), size)
            except OSError:
                continue
    return fallback


def load_fonts():
    f_default = ImageFont.load_default()
    # Tahoma 9px en fontmode="1" possède un hinting bitmap natif Microsoft ultra-net avec accents.
    # Sous Linux : DejaVu Sans / Mono (paquet fonts-dejavu-core, présent sur Debian/Proxmox).
    f_tiny = _find_win_font(("tahoma.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "consola.ttf", "segoeui.ttf"), 9, f_default)
    f_med = _find_win_font(("consolab.ttf", "tahomabd.ttf", "DejaVuSansMono-Bold.ttf", "tahoma.ttf"), 11, f_tiny)
    f_big = _find_win_font(("consolab.ttf", "consola.ttf", "DejaVuSansMono-Bold.ttf", "tahomabd.ttf"), 20, f_med)
    f_huge = _find_win_font(("consolab.ttf", "consola.ttf", "DejaVuSansMono-Bold.ttf", "tahomabd.ttf"), 25, f_big)
    return f_tiny, f_med, f_big, f_huge


FONT_TINY, FONT_MED, FONT_BIG, FONT_HUGE = load_fonts()

_CHAR_NORM = str.maketrans({
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "…": "...",
    "\u00a0": " ",
})


def clean_str(text: str) -> str:
    if not text:
        return ""
    return text.translate(_CHAR_NORM)


def make_draw(img: Image.Image) -> ImageDraw.ImageDraw:
    draw = ImageDraw.Draw(img)
    # Option 3.A : rendu 1-bit strict sans antialiasing ni bavure sur les accents
    draw.fontmode = "1"
    return draw


def text_metrics(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int, int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]


def text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    if not text:
        return 0
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def draw_right_text(draw: ImageDraw.ImageDraw, x_right: int, y: int, text: str, font, fill: int = 0):
    text = clean_str(text)
    if not text:
        return
    bx0, _, w, _ = text_metrics(draw, text, font)
    draw.text((x_right - w - bx0, y), text, font=font, fill=fill)


def draw_centered_in_box(
    draw: ImageDraw.ImageDraw, x0: int, y0: int, w: int, h: int, text: str, font, fill: int = 0
):
    text = clean_str(text)
    if not text:
        return
    bx0, by0, tw, th = text_metrics(draw, text, font)
    tx = x0 + (w - tw) // 2 - bx0
    ty = y0 + (h - th) // 2 - by0
    draw.text((tx, ty), text, font=font, fill=fill)


def truncate_to_px(draw: ImageDraw.ImageDraw, text: str, font, max_px: int) -> str:
    text = clean_str(text)
    if text_width(draw, text, font) <= max_px:
        return text
    while len(text) > 1 and text_width(draw, text + ".", font) > max_px:
        text = text[:-1]
    return text + "." if text else ""


def draw_softkeys(draw: ImageDraw.ImageDraw, labels: list[str]):
    """Dessine la barre contextuelle des 4 boutons sur y=34..42."""
    draw.rectangle((0, 34, 159, 42), fill=1)
    draw.line((0, 34, 159, 34), fill=0)
    widths = (39, 39, 39, 40)
    x = 0
    for i, label in enumerate(labels[:4]):
        w = widths[i]
        if label:
            txt = clean_str(label)[:7]
            draw.rectangle((x, 35, x + w - 1, 42), fill=0)
            draw_centered_in_box(draw, x, 35, w, 8, txt, FONT_TINY, fill=1)
        x += w + 1


def draw_segmented_bar(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, pct: float):
    draw.rectangle((x, y, x + w, y + h), outline=0, fill=1)
    inner_w = max(1, w - 2)
    filled = int((max(0.0, min(100.0, pct)) / 100.0) * inner_w)
    for sx in range(0, filled, 3):
        seg_end = min(sx + 1, inner_w - 1)
        draw.rectangle((x + 1 + sx, y + 1, x + 1 + seg_end, y + h - 1), fill=0)


def draw_toast_banner(draw: ImageDraw.ImageDraw, text: str, y_center: int = 16):
    txt = truncate_to_px(draw, clean_str(text), FONT_TINY, 148)
    _, _, tw, th = text_metrics(draw, txt, FONT_TINY)
    bw = tw + 10
    bh = max(12, th + 5)
    x0 = max(1, (LCD_W - bw) // 2)
    y0 = max(1, y_center - bh // 2)
    draw.rectangle((x0 - 1, y0 - 1, x0 + bw, y0 + bh), outline=0, fill=1)
    draw.rectangle((x0, y0, x0 + bw - 1, y0 + bh - 1), fill=0)
    draw_centered_in_box(draw, x0, y0, bw, bh, txt, FONT_TINY, fill=1)


def draw_play_pause_5x5(draw: ImageDraw.ImageDraw, x: int, y: int, playing: bool, fill: int = 0):
    """Pictogramme 5x5 pixels ► (lecture) ou ❚❚ (pause)."""
    if playing:
        draw.polygon([(x, y), (x, y + 4), (x + 4, y + 2)], fill=fill)
    else:
        draw.rectangle((x, y, x + 1, y + 4), fill=fill)
        draw.rectangle((x + 3, y, x + 4, y + 4), fill=fill)


# ==============================================================================
# ICÔNES MÉTÉO PIXEL-ART 16x16 (1-BIT)
# ==============================================================================
WEATHER_SPRITES_16 = {
    "sun": [
        ".......##.......",
        ".......##.......",
        "..#....##....#..",
        "...#........#...",
        ".....######.....",
        "....########....",
        "....########....",
        "###.########.###",
        "###.########.###",
        "....########....",
        "....########....",
        ".....######.....",
        "...#........#...",
        "..#....##....#..",
        ".......##.......",
        ".......##.......",
    ],
    "partly": [
        "....#...#.......",
        "..#..###..#.....",
        "...######.......",
        ".#.######.......",
        "...####.#####...",
        ".......#######..",
        ".....##########.",
        "....############",
        "...#############",
        "...#############",
        "....###########.",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "cloud": [
        "................",
        "................",
        "......#####.....",
        "....#########...",
        "...###########..",
        "..#############.",
        ".###############",
        ".###############",
        ".###############",
        "..#############.",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "rain": [
        "......#####.....",
        "....#########...",
        "...###########..",
        "..#############.",
        ".###############",
        "..#############.",
        "................",
        "...##...##...##.",
        "..##...##...##..",
        "................",
        "....##...##...##",
        "...##...##...##.",
        "................",
        "..##...##.......",
        ".##...##........",
        "................",
    ],
    "storm": [
        "......#####.....",
        "....#########...",
        "...###########..",
        ".###############",
        "..#############.",
        ".......###......",
        "......###.......",
        ".....######.....",
        ".......###......",
        "......###.......",
        "..#..##.....#...",
        ".#...#.....#....",
        "................",
        "................",
        "................",
        "................",
    ],
    "snow": [
        "......#####.....",
        "....#########...",
        "..#############.",
        ".###############",
        "..#############.",
        "................",
        "..#.#..#.#..#.#.",
        "...#....#....#..",
        "..#.#..#.#..#.#.",
        "................",
        "....#.#..#.#....",
        ".....#....#.....",
        "....#.#..#.#....",
        "................",
        "................",
        "................",
    ],
    "fog": [
        "................",
        "....#########...",
        "..#############.",
        "................",
        ".##############.",
        "................",
        "...#############",
        "................",
        ".#############..",
        "................",
        "..############..",
        "................",
        "....#########...",
        "................",
        "................",
        "................",
    ],
}


def wmo_info(code: int) -> tuple[str, str]:
    """Retourne (clé_sprite_16x16, libellé_français) pour un code WMO Open-Meteo."""
    if code == 0:
        return "sun", "Clair"
    if code in (1, 2):
        return "partly", "Éclaircies"
    if code == 3:
        return "cloud", "Couvert"
    if code in (45, 48):
        return "fog", "Brouillard"
    if code in (51, 53, 55, 56, 57):
        return "rain", "Bruine"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "rain", "Pluie"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow", "Neige"
    if code in (95, 96, 99):
        return "storm", "Orage"
    return "cloud", f"WMO {code}"


def draw_weather_icon(draw: ImageDraw.ImageDraw, x0: int, y0: int, sprite_key: str, fill: int = 0):
    rows = WEATHER_SPRITES_16.get(sprite_key, WEATHER_SPRITES_16["cloud"])
    for dy, row in enumerate(rows):
        for dx, ch in enumerate(row):
            if ch == "#":
                draw.point((x0 + dx, y0 + dy), fill=fill)


# ==============================================================================
# SONDE GPU NVIDIA NATIVE (NVML.DLL VIA CTYPES, ZÉRO POWERSHELL)
# ==============================================================================
class _NvmlUtilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _NvmlMemory(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


class NvmlProbe:
    """Interroge directement nvml.dll sur PC fixe NVIDIA (charge %, VRAM, température °C)."""

    def __init__(self):
        self.available = False
        self._lib = None
        self._handle = ctypes.c_void_p()
        self._init_nvml()

    def _init_nvml(self):
        if IS_WIN:
            dll_paths = (
                "nvml.dll",
                r"C:\Windows\System32\nvml.dll",
                r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll",
            )
            loader = lambda p: ctypes.WinDLL(p)  # noqa: E731
        else:
            dll_paths = ("libnvidia-ml.so.1", "libnvidia-ml.so")
            loader = lambda p: ctypes.CDLL(p)  # noqa: E731
        for p in dll_paths:
            try:
                lib = loader(p)
                if lib.nvmlInit_v2() == 0:
                    if lib.nvmlDeviceGetHandleByIndex_v2(ctypes.c_uint(0), ctypes.byref(self._handle)) == 0:
                        self._lib = lib
                        self.available = True
                        return
                    lib.nvmlShutdown()
            except Exception:
                continue

    def read(self) -> Optional[tuple[int, int, Optional[int]]]:
        """Retourne (gpu_util_pct, vram_used_mb, temp_celsius) ou None."""
        if not self.available or self._lib is None:
            return None
        try:
            util = _NvmlUtilization()
            mem = _NvmlMemory()
            temp = ctypes.c_uint(0)

            rc_u = self._lib.nvmlDeviceGetUtilizationRates(self._handle, ctypes.byref(util))
            rc_m = self._lib.nvmlDeviceGetMemoryInfo(self._handle, ctypes.byref(mem))
            rc_t = self._lib.nvmlDeviceGetTemperature(self._handle, ctypes.c_uint(0), ctypes.byref(temp))

            gpu_pct = int(util.gpu) if rc_u == 0 else 0
            vram_mb = int(mem.used // (1024 * 1024)) if rc_m == 0 else 0
            temp_c = int(temp.value) if rc_t == 0 else None
            return gpu_pct, vram_mb, temp_c
        except Exception:
            return None

    def close(self):
        if self.available and self._lib is not None:
            try:
                self._lib.nvmlShutdown()
            except Exception:
                pass
            self.available = False


# ==============================================================================
# DÉTECTION D'INTERFACE RÉSEAU PHYSIQUE (WIFI -> ETH) & TUNNEL VPN
# ==============================================================================
VIRTUAL_NIC_KEYWORDS = (
    "wireguard", "proton", "tailscale", "wintun", "tap", "tun", "openvpn",
    "nordlynx", "warp", "cloudflare", "zerotier", "anyconnect", "cisco",
    "forti", "pangp", "wsl", "vethernet", "hyper-v", "vmware", "virtualbox",
    "vbox", "loopback", "bluetooth", "npcap", "teredo", "isatap", "docker",
    # Linux / Proxmox : veth, ponts invités (fwbr/fwpr/fwln), taps de VM (tapNNNi0), libvirt
    "veth", "virbr", "fwbr", "fwpr", "fwln", "br-",
)

VPN_TUNNEL_KEYWORDS = (
    "wireguard", "proton", "wintun", "openvpn", "nordlynx", "warp",
    "anyconnect", "forti", "pangp", "tap-windows", "tun", "wg", "tailscale",
)

WIFI_NIC_KEYWORDS = ("wi-fi", "wifi", "wlan", "wlp", "wireless", "sans fil", "802.11")


def _get_best_route_ifname() -> Optional[str]:
    """Retourne le nom de l'interface portant la route par défaut (iphlpapi sous Windows, /proc/net/route sous Linux)."""
    if not IS_WIN:
        try:
            best, best_metric = None, None
            for line in Path("/proc/net/route").read_text().splitlines()[1:]:
                f = line.split()
                if len(f) >= 7 and f[1] == "00000000" and int(f[3], 16) & 0x2:  # route par défaut, UP
                    metric = int(f[6])
                    if best_metric is None or metric < best_metric:
                        best, best_metric = f[0], metric
            return best
        except Exception:
            return None
    try:
        best_idx = ctypes.c_ulong(0)
        # 0x08080808 = 8.8.8.8 en network byte order
        rc = ctypes.windll.iphlpapi.GetBestInterface(ctypes.c_ulong(0x08080808), ctypes.byref(best_idx))
        if rc == 0 and best_idx.value > 0:
            return socket.if_indextoname(best_idx.value)
    except Exception:
        pass
    return None


def inspect_network_interfaces(prefer_wifi: bool = True) -> tuple[Optional[str], str, str, bool]:
    """
    Identifie :
    - la carte physique active (priorité Wi-Fi puis Ethernet si prefer_wifi=True)
    - son étiquette courte ('WiFi' ou 'Eth')
    - son IPv4 locale
    - la présence d'un tunnel VPN actif routant le trafic (vpn_active)
    """
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    best_route_nic = _get_best_route_ifname()

    vpn_active = False
    if best_route_nic:
        low_best = best_route_nic.lower()
        if any(k in low_best for k in VIRTUAL_NIC_KEYWORDS):
            vpn_active = True

    candidates = []
    for nic_name, nic_stat in stats.items():
        if not nic_stat.isup:
            continue
        low = nic_name.lower()

        # Vérifie aussi les tunnels VPN explicites montés avec IP valide
        nic_ipv4 = None
        for snic in addrs.get(nic_name, ()):
            if snic.family == socket.AF_INET:
                ip = snic.address
                if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                    nic_ipv4 = ip
                    break

        if not nic_ipv4:
            continue

        if any(k in low for k in VPN_TUNNEL_KEYWORDS):
            vpn_active = True
            continue

        if any(k in low for k in VIRTUAL_NIC_KEYWORDS):
            continue

        is_wifi = any(k in low for k in WIFI_NIC_KEYWORDS)
        if prefer_wifi:
            priority = 0 if is_wifi else 1
        else:
            priority = 0 if not is_wifi else 1

        short_tag = "WiFi" if is_wifi else "Eth"
        candidates.append((priority, nic_name, short_tag, nic_ipv4))

    if candidates:
        candidates.sort(key=lambda item: item[0])
        _, chosen_name, chosen_tag, chosen_ip = candidates[0]
        return chosen_name, chosen_tag, chosen_ip, vpn_active

    return None, "---", "0.0.0.0", vpn_active


# ==============================================================================
# CAPTURE AUDIO TEMPS RÉEL (WASAPI LOOPBACK + FFT / VU / OSCILLO / BASSES)
# ==============================================================================
@dataclass
class AudioFrame:
    bars_20: list[float] = field(default_factory=lambda: [0.0] * 20)
    peaks_20: list[float] = field(default_factory=lambda: [0.0] * 20)
    wave_160: list[float] = field(default_factory=lambda: [0.0] * 160)
    vu_left: float = 0.0
    vu_right: float = 0.0
    bass_energy: float = 0.0
    active: bool = False


class AudioLoopbackWorker:
    """Capture la sortie audio Windows en WASAPI Loopback pour le spectre et les 4 visualiseurs."""

    def __init__(self):
        self.running = True
        self._lock = threading.Lock()
        self._frame = AudioFrame()
        self._latest_pcm: Optional[bytes] = None
        self._latest_pcm_t: float = 0.0
        self._channels = 2
        self._rate = 48000
        self._pa = None
        self._stream = None

        self._bars = [0.0] * 20
        self._peaks = [0.0] * 20
        self._peak_vel = [0.0] * 20
        self._vu_l = 0.0
        self._vu_r = 0.0
        self._bass = 0.0

        if HAS_AUDIO:
            threading.Thread(target=self._run_loop, daemon=True).start()
        else:
            print("[INIT] Audio WASAPI inactif (installez 'pyaudiowpatch' et 'numpy' pour les visualiseurs).")

    def snapshot(self) -> AudioFrame:
        with self._lock:
            return AudioFrame(
                bars_20=list(self._frame.bars_20),
                peaks_20=list(self._frame.peaks_20),
                wave_160=list(self._frame.wave_160),
                vu_left=self._frame.vu_left,
                vu_right=self._frame.vu_right,
                bass_energy=self._frame.bass_energy,
                active=self._frame.active,
            )

    def _audio_callback(self, in_data, frame_count, time_info, status):
        self._latest_pcm = in_data
        self._latest_pcm_t = time.monotonic()
        return (None, pyaudio.paContinue)

    def _open_wasapi_loopback(self) -> bool:
        try:
            self._pa = pyaudio.PyAudio()
            loopback = self._pa.get_default_wasapi_loopback()
            self._channels = max(1, int(loopback.get("maxInputChannels", 2)))
            self._rate = int(loopback.get("defaultSampleRate", 48000))
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=self._channels,
                rate=self._rate,
                input=True,
                frames_per_buffer=1024,
                input_device_index=loopback["index"],
                stream_callback=self._audio_callback,
            )
            self._stream.start_stream()
            print(f"[INIT] Audio WASAPI Loopback actif : {loopback.get('name')} ({self._rate} Hz)")
            return True
        except Exception as exc:
            print(f"[AUDIO] Ouverture WASAPI Loopback impossible : {exc}")
            return False

    def _decay_silent(self):
        for i in range(20):
            self._bars[i] *= 0.72
            if self._bars[i] < 0.01:
                self._bars[i] = 0.0
            self._peak_vel[i] += 0.008
            self._peaks[i] = max(self._bars[i], self._peaks[i] - self._peak_vel[i])
            if self._peaks[i] <= self._bars[i]:
                self._peak_vel[i] = 0.0
        self._vu_l *= 0.78
        self._vu_r *= 0.78
        self._bass *= 0.70

        with self._lock:
            self._frame.bars_20 = list(self._bars)
            self._frame.peaks_20 = list(self._peaks)
            self._frame.wave_160 = [w * 0.65 for w in self._frame.wave_160]
            self._frame.vu_left = self._vu_l
            self._frame.vu_right = self._vu_r
            self._frame.bass_energy = self._bass
            self._frame.active = False

    def _process_pcm(self, pcm: bytes):
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) * (1.0 / 32768.0)
        if samples.size < self._channels * 64:
            self._decay_silent()
            return

        frames = samples.reshape(-1, self._channels)
        left = frames[:, 0]
        right = frames[:, 1] if self._channels > 1 else left
        mono = 0.5 * (left + right)

        # VU-mètres gauche/droite avec inertie balistique
        rms_l = min(1.0, float(np.sqrt(np.mean(left * left))) * 4.2)
        rms_r = min(1.0, float(np.sqrt(np.mean(right * right))) * 4.2)
        self._vu_l = rms_l if rms_l > self._vu_l else (self._vu_l * 0.75 + rms_l * 0.25)
        self._vu_r = rms_r if rms_r > self._vu_r else (self._vu_r * 0.75 + rms_r * 0.25)

        # Oscilloscope 160 échantillons
        idx_wave = np.linspace(0, len(mono) - 1, 160).astype(np.int32)
        wave = np.clip(mono[idx_wave] * 2.4, -1.0, 1.0).tolist()

        # FFT fenêtrée Hann -> 20 bandes logarithmiques (45 Hz .. 15 kHz) façon Winamp
        n = len(mono)
        win = np.hanning(n)
        spec = np.abs(np.fft.rfft(mono * win)) / max(1.0, n * 0.5)
        freqs = np.fft.rfftfreq(n, d=1.0 / self._rate)
        edges = np.geomspace(45.0, min(15500.0, self._rate * 0.45), 21)

        new_bars = [0.0] * 20
        for i in range(20):
            mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
            if np.any(mask):
                val = float(np.max(spec[mask]))
            else:
                b_idx = int(np.argmin(np.abs(freqs - edges[i])))
                val = float(spec[b_idx])

            # Conversion dB + légère compensation des aigus
            db = 20.0 * math.log10(val + 1e-6)
            norm = (db + 56.0) / 44.0 + (i / 20.0) * 0.18
            lvl = max(0.0, min(1.0, norm))

            # Montée rapide, descente amortie + chapeaux de crête avec gravité
            if lvl >= self._bars[i]:
                self._bars[i] = lvl
            else:
                self._bars[i] = self._bars[i] * 0.68 + lvl * 0.32

            if self._bars[i] >= self._peaks[i]:
                self._peaks[i] = self._bars[i]
                self._peak_vel[i] = 0.0
            else:
                self._peak_vel[i] += 0.007
                self._peaks[i] = max(self._bars[i], self._peaks[i] - self._peak_vel[i])

            new_bars[i] = self._bars[i]

        bass_now = sum(new_bars[:3]) / 3.0
        self._bass = bass_now if bass_now > self._bass else (self._bass * 0.72 + bass_now * 0.28)

        with self._lock:
            self._frame.bars_20 = new_bars
            self._frame.peaks_20 = list(self._peaks)
            self._frame.wave_160 = wave
            self._frame.vu_left = self._vu_l
            self._frame.vu_right = self._vu_r
            self._frame.bass_energy = self._bass
            self._frame.active = max(new_bars, default=0.0) > 0.02

    def _run_loop(self):
        if not self._open_wasapi_loopback():
            return
        last_processed_t = 0.0
        while self.running:
            now = time.monotonic()
            pcm = self._latest_pcm
            pcm_t = self._latest_pcm_t
            if pcm is not None and pcm_t > last_processed_t and (now - pcm_t) < 0.12:
                last_processed_t = pcm_t
                try:
                    self._process_pcm(pcm)
                except Exception:
                    self._decay_silent()
            else:
                self._decay_silent()
            time.sleep(0.035)

    def close(self):
        self.running = False
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:
            pass
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass


# ==============================================================================
# COLLECTEUR DE TÉLÉMÉTRIE, MÉDIA WINRT, PINGS, WAN & MÉTÉO OPEN-METEO
# ==============================================================================
@dataclass
class DailyForecast:
    date_label: str
    wmo_code: int
    t_min: float
    t_max: float
    rain_prob: int
    wind_kmh: float


class TelemetryHub:
    def __init__(self, cfg_mgr: ConfigManager):
        self.cfg_mgr = cfg_mgr
        self.running = True

        # CPU / RAM / Batterie
        self.cpu_total = 0.0
        self.cpu_cores: list[float] = []
        self.cpu_peak = 0.0
        self.cpu_hist = deque([0.0] * 158, maxlen=158)
        self.ram_pct = 0.0
        self.ram_used_gb = 0.0

        bat_init = psutil.sensors_battery()
        self.has_battery = bat_init is not None
        self.bat_pct: Optional[float] = bat_init.percent if bat_init else None
        self.bat_plugged: bool = bool(bat_init.power_plugged) if bat_init else False

        # GPU (NVML natif sur PC fixe NVIDIA, repli WMI iGPU sinon)
        self.nvml = NvmlProbe()
        self.gpu_label = "GPU" if self.nvml.available else "iGPU"
        self.gpu_util = 0
        self.gpu_vram_mb = 0
        self.gpu_temp_c: Optional[int] = None
        self.gpu_peak_util = 0

        if self.nvml.available:
            print("[INIT] Sonde GPU NVIDIA NVML native active (zéro processus PowerShell).")
        elif IS_WIN:
            print("[INIT] NVML absent -> bascule sur compteurs WMI iGPU Windows.")
        else:
            print("[INIT] Pas de GPU NVIDIA (libnvidia-ml absente) -> jauge GPU inactive.")

        # Réseau (carte physique uniquement : Wi-Fi prioritaire puis Ethernet)
        self.phys_nic_name: Optional[str] = None
        self.phys_nic_tag: str = "---"
        self.local_ip: str = "0.0.0.0"
        self.public_ip: str = "---"
        self.vpn_active: bool = False

        self.dl_speed = 0.0
        self.ul_speed = 0.0
        self.dl_total_bytes = 0
        self.ul_total_bytes = 0
        self.dl_hist = deque([0.0] * 160, maxlen=160)
        self.ul_hist = deque([0.0] * 160, maxlen=160)
        self._last_nic_name: Optional[str] = None
        self._last_nic_cnt = None
        self._last_net_t = time.monotonic()
        self._reset_net_requested = False

        # Serveurs & Pings
        self.pings: dict[str, Optional[int]] = {n: None for n, _ in self.cfg_mgr.get().hosts}
        self._force_ping_event = threading.Event()
        self._force_weather_event = threading.Event()

        # Météo (Géo-IP hors VPN, repli Lausanne 1000 sous VPN ou hors-ligne)
        self.weather_city = self.cfg_mgr.get().fallback_city
        self.weather_is_fallback = True
        self.weather_temp_now: Optional[float] = None
        self.weather_code_now: int = 0
        self.weather_wind_now: float = 0.0
        self.weather_days: list[DailyForecast] = []
        self.weather_hourly_temp: list[float] = []
        self.weather_hourly_rain: list[int] = []
        self.weather_online = False

        # Média WinRT GSMTC
        self.media_active = False
        self.media_playing = False
        self.media_title = "Aucun média actif"
        self.media_artist = ""
        self.media_app = ""
        self.media_pos_sec = 0.0
        self.media_dur_sec = 0.0
        self.media_track_changed_until = 0.0
        self._last_track_sig = ("", "")
        self._media_seek_queue: queue.Queue[float] = queue.Queue()

        # Démarrage des workers
        threading.Thread(target=self._fast_sensor_loop, daemon=True).start()
        if not self.nvml.available and IS_WIN:
            threading.Thread(target=self._igpu_wmi_loop, daemon=True).start()
        threading.Thread(target=self._ping_loop, daemon=True).start()
        threading.Thread(target=self._weather_and_wan_loop, daemon=True).start()
        if HAS_WINRT:
            threading.Thread(target=self._media_loop, daemon=True).start()

    @property
    def dl_total_gb(self) -> float:
        return self.dl_total_bytes / (1024 ** 3)

    def reset_net_totals(self):
        self._reset_net_requested = True

    def trigger_ping_and_wan_refresh(self):
        self._force_ping_event.set()
        self._force_weather_event.set()

    def trigger_weather_refresh(self):
        self._force_weather_event.set()

    def seek_media_relative(self, delta_sec: float):
        self._media_seek_queue.put(delta_sec)

    # --- Boucle rapide 1 Hz : CPU, RAM, Batterie, NVML, Débit carte physique ---
    def _fast_sensor_loop(self):
        psutil.cpu_percent()
        while self.running:
            cfg = self.cfg_mgr.get()
            self.cpu_total = psutil.cpu_percent()
            self.cpu_cores = psutil.cpu_percent(percpu=True)
            self.cpu_peak = max(self.cpu_peak, self.cpu_total)
            self.cpu_hist.append(self.cpu_total)

            vm = psutil.virtual_memory()
            self.ram_pct = vm.percent
            self.ram_used_gb = vm.used / (1024 ** 3)

            bat = psutil.sensors_battery()
            if bat is not None:
                self.has_battery = True
                self.bat_pct = bat.percent
                self.bat_plugged = bool(bat.power_plugged)
            else:
                self.has_battery = False
                self.bat_pct = None

            if self.nvml.available:
                res = self.nvml.read()
                if res is not None:
                    self.gpu_util, self.gpu_vram_mb, self.gpu_temp_c = res
                    self.gpu_peak_util = max(self.gpu_peak_util, self.gpu_util)

            # Sélection stricte de la carte physique active (Wi-Fi puis Ethernet)
            nic_name, nic_tag, loc_ip, vpn_on = inspect_network_interfaces(cfg.prefer_wifi)
            self.phys_nic_name = nic_name
            self.phys_nic_tag = nic_tag
            self.local_ip = loc_ip
            if vpn_on != self.vpn_active:
                self.vpn_active = vpn_on
                # Un changement d'état VPN relance immédiatement la détection IP / météo Lausanne
                self._force_weather_event.set()

            if self._reset_net_requested:
                self.dl_total_bytes = 0
                self.ul_total_bytes = 0
                self._reset_net_requested = False

            now = time.monotonic()
            dt = max(0.1, now - self._last_net_t)
            per_nic = psutil.net_io_counters(pernic=True)

            if nic_name and nic_name in per_nic:
                cur = per_nic[nic_name]
                if self._last_nic_name == nic_name and self._last_nic_cnt is not None:
                    d_rx = max(0, cur.bytes_recv - self._last_nic_cnt.bytes_recv)
                    d_tx = max(0, cur.bytes_sent - self._last_nic_cnt.bytes_sent)
                    self.dl_speed = d_rx / dt
                    self.ul_speed = d_tx / dt
                    self.dl_total_bytes += d_rx
                    self.ul_total_bytes += d_tx
                else:
                    self.dl_speed = 0.0
                    self.ul_speed = 0.0
                self._last_nic_name = nic_name
                self._last_nic_cnt = cur
            else:
                self.dl_speed = 0.0
                self.ul_speed = 0.0
                self._last_nic_name = None
                self._last_nic_cnt = None

            self.dl_hist.append(self.dl_speed)
            self.ul_hist.append(self.ul_speed)
            self._last_net_t = now

            time.sleep(1.0)

    # --- Boucle de repli WMI iGPU (uniquement si NVML est absent, ex. Surface Pro) ---
    def _igpu_wmi_loop(self):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        ps_script = (
            "while ($true) { "
            "$eng = Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Name -like '*engtype_3D*' -or $_.Name -like '*engtype_Compute*' -or $_.Name -like '*engtype_VideoDecode*' } | "
            "Measure-Object -Property UtilizationPercentage -Sum; "
            "$mem = Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory -ErrorAction SilentlyContinue; "
            "$u = if ($eng.Sum) { [math]::Min(100, [int]$eng.Sum) } else { 0 }; "
            "$bytes = 0; foreach ($m in $mem) { $bytes += ($m.DedicatedUsage + $m.SharedUsage) }; "
            "$mb = [math]::Round($bytes / 1MB, 0); "
            "Write-Output \"$u|$mb\"; "
            "Start-Sleep -Seconds 1 }"
        )
        try:
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", ps_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                text=True,
                bufsize=1,
            )
            for line in proc.stdout:
                if not self.running:
                    proc.terminate()
                    break
                parts = line.strip().split("|")
                if len(parts) == 2 and parts[0].isdigit():
                    self.gpu_util = int(parts[0])
                    self.gpu_vram_mb = int(float(parts[1]))
                    self.gpu_peak_util = max(self.gpu_peak_util, self.gpu_util)
        except Exception:
            pass

    # --- Boucle Pings ICMP des 3 serveurs ---
    @staticmethod
    def _ping_once(host: str) -> Optional[int]:
        if not IS_WIN:
            try:
                out = subprocess.check_output(
                    ["ping", "-c", "1", "-W", "1", host],
                    stderr=subprocess.DEVNULL, text=True, errors="ignore", timeout=3,
                )
                m = re.search(r"time[=<]\s*([\d.]+)\s*ms", out)
                if m:
                    return max(1, int(round(float(m.group(1)))))
            except Exception:
                pass
            return None
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            out = subprocess.check_output(
                ["ping", "-n", "1", "-w", "800", host],
                startupinfo=startupinfo,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="cp850",
                errors="ignore",
            )
            for token in out.replace("=", " ").replace("<", " ").split():
                if token.lower().endswith("ms") and token[:-2].isdigit():
                    return int(token[:-2])
        except Exception:
            pass
        return None

    def _ping_loop(self):
        while self.running:
            hosts = self.cfg_mgr.get().hosts
            new_pings = {}
            for name, addr in hosts:
                new_pings[name] = self._ping_once(addr)
            self.pings = new_pings
            self._force_ping_event.wait(timeout=self.cfg_mgr.get().ping_interval_sec)
            self._force_ping_event.clear()

    # --- Boucle WAN IP, Géolocalisation IP & Prévisions Open-Meteo (7j + 24h) ---
    @staticmethod
    def _http_json(url: str, timeout: float = 4.5) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": "G15-HUD/0.2.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def _resolve_wan_and_location(self, cfg: AppConfig) -> tuple[str, float, float, bool]:
        """Retourne (city, lat, lon, is_fallback) et met à jour self.public_ip en clair."""
        geo_city = None
        geo_lat = None
        geo_lon = None

        try:
            info = self._http_json("https://ipwho.is/", timeout=4.0)
            if info.get("success", False):
                self.public_ip = str(info.get("ip", self.public_ip))
                geo_city = str(info.get("city") or "").strip()
                geo_lat = float(info["latitude"])
                geo_lon = float(info["longitude"])
        except Exception:
            try:
                ip_only = self._http_json("https://api.ipify.org?format=json", timeout=3.5)
                self.public_ip = str(ip_only.get("ip", self.public_ip))
            except Exception:
                pass

        # Si VPN actif (et option activée) ou géo-IP indisponible -> Repli sur Lausanne 1000
        if (cfg.fallback_on_vpn and self.vpn_active) or not cfg.auto_detect_ip or not geo_city or geo_lat is None:
            return cfg.fallback_city, cfg.fallback_lat, cfg.fallback_lon, True

        return geo_city, geo_lat, geo_lon, False

    def _fetch_open_meteo(self, city: str, lat: float, lon: float, is_fallback: bool):
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat:.4f}&longitude={lon:.4f}"
            f"&current=temperature_2m,weather_code,wind_speed_10m"
            f"&hourly=temperature_2m,precipitation_probability"
            f"&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max"
            f"&timezone=auto&forecast_days=7"
        )
        data = self._http_json(url, timeout=5.0)
        cur = data["current"]
        self.weather_city = city
        self.weather_is_fallback = is_fallback
        self.weather_temp_now = float(cur["temperature_2m"])
        self.weather_code_now = int(cur["weather_code"])
        self.weather_wind_now = float(cur["wind_speed_10m"])

        # Prévisions 7 jours
        daily = data["daily"]
        jours_fr = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")
        days_out: list[DailyForecast] = []
        for idx, d_str in enumerate(daily["time"]):
            if idx == 0:
                lbl = "Auj."
            elif idx == 1:
                lbl = "Demain"
            else:
                dt_obj = datetime.strptime(d_str, "%Y-%m-%d")
                lbl = f"{jours_fr[dt_obj.weekday()]} {dt_obj.strftime('%d/%m')}"
            rain_list = daily.get("precipitation_probability_max", [])
            rain_p = int(rain_list[idx] or 0) if idx < len(rain_list) else 0
            days_out.append(
                DailyForecast(
                    date_label=lbl,
                    wmo_code=int(daily["weather_code"][idx]),
                    t_min=float(daily["temperature_2m_min"][idx]),
                    t_max=float(daily["temperature_2m_max"][idx]),
                    rain_prob=rain_p,
                    wind_kmh=float(daily["wind_speed_10m_max"][idx]),
                )
            )
        self.weather_days = days_out

        # Courbe sur les 24 prochaines heures à partir de l'heure courante
        hourly = data["hourly"]
        now_key = datetime.now().strftime("%Y-%m-%dT%H:00")
        h_times = hourly["time"]
        start_i = h_times.index(now_key) if now_key in h_times else 0
        self.weather_hourly_temp = [float(x) for x in hourly["temperature_2m"][start_i : start_i + 24]]
        h_rain = hourly.get("precipitation_probability", [])
        self.weather_hourly_rain = [int(x or 0) for x in h_rain[start_i : start_i + 24]]
        self.weather_online = True

    def _weather_and_wan_loop(self):
        time.sleep(0.6)  # Laisse la détection d'interface/VPN initialiser son premier cycle
        while self.running:
            cfg = self.cfg_mgr.get()
            city, lat, lon, is_fb = self._resolve_wan_and_location(cfg)
            try:
                self._fetch_open_meteo(city, lat, lon, is_fb)
            except Exception:
                self.weather_city = city
                self.weather_is_fallback = is_fb
                self.weather_online = False

            self._force_weather_event.wait(timeout=cfg.weather_refresh_sec)
            self._force_weather_event.clear()

    # --- Boucle Média WinRT GSMTC (0.4 s + saut temporel -15s / +30s) ---
    @staticmethod
    def _clean_app_name(aumid: str) -> str:
        if not aumid:
            return ""
        low = aumid.lower()
        for key, nice in (
            ("spotify", "Spotify"),
            ("firefox", "Firefox"),
            ("msedge", "Edge"),
            ("chrome", "Chrome"),
            ("brave", "Brave"),
            ("vlc", "VLC"),
            ("foobar", "foobar"),
            ("musicbee", "MusicBee"),
            ("applemusic", "AppleMus"),
            ("zune", "MediaPlr"),
        ):
            if key in low:
                return nice
        base = aumid.split("!")[0].split("\\")[-1]
        if base.lower().endswith(".exe"):
            base = base[:-4]
        return base[:9]

    @staticmethod
    def _td_seconds(val) -> float:
        if val is None:
            return 0.0
        if hasattr(val, "total_seconds"):
            return max(0.0, float(val.total_seconds()))
        if hasattr(val, "duration"):
            return max(0.0, float(val.duration) / 10_000_000.0)
        return 0.0

    def _media_loop(self):
        async def step_media():
            try:
                mgr = await MediaManager.request_async()
                session = mgr.get_current_session()
                if not session:
                    self.media_active = False
                    self.media_playing = False
                    self.media_title = "Aucun média actif"
                    self.media_artist = ""
                    self.media_app = ""
                    self.media_pos_sec = 0.0
                    self.media_dur_sec = 0.0
                    return

                self.media_active = True
                self.media_app = self._clean_app_name(getattr(session, "source_app_user_model_id", "") or "")
                info = session.get_playback_info()
                self.media_playing = int(info.playback_status) == 4

                tl = session.get_timeline_properties()
                start_s = self._td_seconds(getattr(tl, "start_time", None))
                end_s = self._td_seconds(getattr(tl, "end_time", None))
                pos_s = self._td_seconds(getattr(tl, "position", None))
                dur_s = max(0.0, end_s - start_s)

                # Interpolation fluide du compteur temporel entre deux notifications Spotify/WinRT
                if self.media_playing and dur_s > 0:
                    upd = getattr(tl, "last_updated_time", None)
                    if isinstance(upd, datetime) and upd.year > 2000:
                        drift = (datetime.now(timezone.utc) - upd.astimezone(timezone.utc)).total_seconds()
                        if 0.0 <= drift <= 30.0:
                            pos_s = min(end_s, pos_s + drift)

                # Traitement immédiat des requêtes de saut temporel (-15s / +30s)
                while not self._media_seek_queue.empty():
                    delta = self._media_seek_queue.get_nowait()
                    if dur_s > 0:
                        target_s = max(start_s, min(end_s, pos_s + delta))
                        ticks_100ns = int(target_s * 10_000_000)
                        await session.try_change_playback_position_async(ticks_100ns)
                        pos_s = target_s

                self.media_dur_sec = dur_s
                self.media_pos_sec = max(0.0, pos_s - start_s) if dur_s > 0 else 0.0

                props = await session.try_get_media_properties_async()
                title = (props.title or "Titre inconnu").strip()
                artist = (props.artist or "").strip()
                self.media_title = title
                self.media_artist = artist

                sig = (title, artist)
                if sig != self._last_track_sig and title != "Titre inconnu":
                    self._last_track_sig = sig
                    self.media_track_changed_until = time.monotonic() + 3.0
            except Exception:
                pass

        loop = asyncio.new_event_loop()
        while self.running:
            loop.run_until_complete(step_media())
            time.sleep(0.15 if not self._media_seek_queue.empty() else 0.45)

    def close(self):
        self.running = False
        self._force_ping_event.set()
        self._force_weather_event.set()
        self.nvml.close()


# ==============================================================================
# LES 8 ÉCRANS INTERACTIFS DU DASHBOARD
# ==============================================================================
class BaseScreen:
    def is_immersive(self) -> bool:
        """Retourne True si l'écran masque la barre des softkeys après 3s pour passer en 160x43."""
        return False

    def bypass_wake_for_button(self, btn: int) -> bool:
        """Exception Pong : les boutons 1 et 2 agissent immédiatement sans réveiller la barre."""
        return False

    def on_enter(self):
        pass

    def on_button(self, btn: int):
        pass

    def render(
        self,
        draw: ImageDraw.ImageDraw,
        tel: TelemetryHub,
        audio: AudioLoopbackWorker,
        hw: "G15HidController",
        cfg: AppConfig,
        show_softkeys: bool,
    ):
        raise NotImplementedError


# --- ÉCRAN 1 : HORLOGE ---
class ClockScreen(BaseScreen):
    def __init__(self):
        self.fmt_24h = True
        self.show_sec = True
        self.show_uptime = False

    def on_button(self, btn: int):
        if btn == 1:
            self.fmt_24h = not self.fmt_24h
        elif btn == 2:
            self.show_sec = not self.show_sec
        elif btn == 3:
            self.show_uptime = not self.show_uptime

    def render(self, draw, tel, audio, hw, cfg, show_softkeys):
        now = datetime.now()
        fmt = "%H:%M" if self.fmt_24h else "%I:%M"
        if self.show_sec:
            fmt += ":%S"
        t_str = now.strftime(fmt)

        if self.show_uptime:
            up_sec = int(time.time() - psutil.boot_time())
            h, rem = divmod(up_sec, 3600)
            m, _ = divmod(rem, 60)
            draw.text((2, 0), f"Uptime {h}h{m:02d}m", font=FONT_TINY, fill=0)
            draw_right_text(draw, 158, 0, f"CPU {tel.cpu_total:.0f}%", FONT_TINY, fill=0)
        else:
            draw.text((2, 0), now.strftime("%d/%m/%Y"), font=FONT_TINY, fill=0)
            if tel.has_battery and tel.bat_pct is not None:
                plug = "+" if tel.bat_plugged else ""
                right_info = f"BAT {tel.bat_pct:.0f}%{plug}"
            else:
                right_info = f"CPU {tel.cpu_total:.0f}%"
            draw_right_text(draw, 158, 0, right_info, FONT_TINY, fill=0)

        font_use = FONT_BIG if self.show_sec else FONT_HUGE
        draw_centered_in_box(draw, 0, 10, 160, 23, t_str, font_use, fill=0)
        draw_softkeys(draw, ["12/24", "SEC", "DATE" if self.show_uptime else "UPT", ""])


# --- ÉCRAN 2 : CHRONO / MINUTEUR ---
class ChronoScreen(BaseScreen):
    def __init__(self):
        self.is_timer = False
        self.running = False
        self.elapsed = 0.0
        self.last_t = 0.0
        self.laps = deque(maxlen=2)
        self.lap_count = 0
        self.timer_preset_idx = 0
        self.presets = [60, 180, 300, 600, 1500, 2700]
        self.remaining = float(self.presets[0])

    def on_button(self, btn: int):
        now = time.monotonic()
        if btn == 1:
            if not self.running:
                self.last_t = now
                self.running = True
            else:
                self._tick(now)
                self.running = False
        elif btn == 2:
            if not self.is_timer and self.elapsed > 0:
                self._tick(now)
                self.lap_count += 1
                m, s = divmod(self.elapsed, 60)
                self.laps.appendleft(f"#{self.lap_count} {int(m):02d}:{s:04.1f}")
            elif self.is_timer and not self.running:
                self.timer_preset_idx = (self.timer_preset_idx + 1) % len(self.presets)
                self.remaining = float(self.presets[self.timer_preset_idx])
        elif btn == 3:
            self.running = False
            self.elapsed = 0.0
            self.lap_count = 0
            self.laps.clear()
            self.remaining = float(self.presets[self.timer_preset_idx])
        elif btn == 4:
            self.running = False
            self.is_timer = not self.is_timer

    def _tick(self, now: float):
        if self.running:
            dt = now - self.last_t
            self.last_t = now
            if self.is_timer:
                self.remaining = max(0.0, self.remaining - dt)
                if self.remaining == 0.0:
                    self.running = False
            else:
                self.elapsed += dt

    def render(self, draw, tel, audio, hw, cfg, show_softkeys):
        self._tick(time.monotonic())
        val = self.remaining if self.is_timer else self.elapsed
        m, s = divmod(val, 60)
        h, m = divmod(int(m), 60)

        invert_alert = self.is_timer and self.remaining == 0.0 and (int(time.time() * 4) % 2 == 0)
        if invert_alert:
            draw.rectangle((0, 0, 159, 33), fill=0)

        fg = 1 if invert_alert else 0
        draw.text((2, 0), "MINUTEUR" if self.is_timer else "CHRONO", font=FONT_TINY, fill=fg)
        t_str = f"{h}:{m:02d}:{int(s):02d}" if h > 0 else f"{m:02d}:{s:04.1f}"
        draw.text((2, 10), t_str, font=FONT_BIG, fill=fg)

        if not self.is_timer:
            for i, lap in enumerate(self.laps):
                draw_right_text(draw, 158, 2 + i * 11, lap, FONT_TINY, fill=fg)
        else:
            preset_m = self.presets[self.timer_preset_idx] // 60
            draw_right_text(draw, 158, 12, f"Cible {preset_m}m", FONT_TINY, fill=fg)

        draw_softkeys(
            draw,
            ["PAUSE" if self.running else "START", "DURÉE" if self.is_timer else "TOUR", "RESET", "MODE"],
        )


# --- ÉCRAN 3 : MONITORING (ADAPTATION AUTO SURFACE PRO / PC FIXE + NVML °C) ---
class MonitorScreen(BaseScreen):
    def __init__(self):
        self.graph_mode = False
        self.per_core = False
        self.show_bat = False
        self.show_peaks = False

    def on_button(self, btn: int):
        if btn == 1:
            self.graph_mode = not self.graph_mode
            if self.graph_mode:
                self.per_core = False
        elif btn == 2:
            self.per_core = not self.per_core
            if self.per_core:
                self.graph_mode = False
        elif btn == 3:
            self.show_bat = not self.show_bat
        elif btn == 4:
            self.show_peaks = not self.show_peaks

    def render(self, draw, tel, audio, hw, cfg, show_softkeys):
        if not tel.has_battery:
            self.show_bat = False

        if self.graph_mode:
            t_sfx = f" {tel.gpu_temp_c}°C" if tel.gpu_temp_c is not None else f" ({tel.gpu_vram_mb}M)"
            hdr = f"CPU {tel.cpu_total:2.0f}% | {tel.gpu_label} {tel.gpu_util:2d}%{t_sfx}"
            draw.text((2, 0), hdr, font=FONT_TINY, fill=0)
            draw.rectangle((0, 11, 159, 33), outline=0)
            pts_cpu = list(tel.cpu_hist)
            for i in range(1, len(pts_cpu)):
                y1 = 32 - int((pts_cpu[i - 1] / 100.0) * 20)
                y2 = 32 - int((pts_cpu[i] / 100.0) * 20)
                draw.line((i, y1, i + 1, y2), fill=0)

        elif self.per_core and tel.cpu_cores:
            total_threads = len(tel.cpu_cores)
            draw.text((2, 0), f"CORES ({total_threads}) | Moy {tel.cpu_total:.0f}%", font=FONT_TINY, fill=0)
            # Supporte jusqu'à 64 threads (ex. Threadripper) par regroupement si > 38 coeurs
            cores = tel.cpu_cores
            if len(cores) > 38:
                cores = [(cores[i] + cores[i + 1]) * 0.5 for i in range(0, len(cores) - 1, 2)]
            n = len(cores)
            step = max(2, 156 // max(1, n))
            bar_w = max(1, min(7, step - 1))
            for idx, c_pct in enumerate(cores):
                bx = 2 + idx * step
                if bx + bar_w > 158:
                    break
                bh = int((c_pct / 100.0) * 21)
                draw.rectangle((bx, 11, bx + bar_w - 1, 32), outline=0)
                if bh > 0:
                    draw.rectangle((bx, 32 - bh, bx + bar_w - 1, 32), fill=0)

        else:
            # Ligne 1 : CPU
            cpu_lbl = f"Pic {tel.cpu_peak:.0f}%" if self.show_peaks else f"{tel.cpu_total:4.1f}%"
            draw.text((1, 0), "CPU", font=FONT_TINY, fill=0)
            draw_segmented_bar(draw, 24, 1, 84, 7, tel.cpu_total)
            draw_right_text(draw, 159, 0, cpu_lbl, FONT_TINY, fill=0)

            # Ligne 2 : RAM
            draw.text((1, 11), "RAM", font=FONT_TINY, fill=0)
            draw_segmented_bar(draw, 24, 12, 84, 7, tel.ram_pct)
            draw_right_text(draw, 159, 11, f"{tel.ram_used_gb:.1f}G", FONT_TINY, fill=0)

            # Ligne 3 : Batterie (Surface Pro si activée) ou GPU/dGPU
            if self.show_bat and tel.has_battery and tel.bat_pct is not None:
                plug = "+" if tel.bat_plugged else ""
                draw.text((1, 22), "BAT", font=FONT_TINY, fill=0)
                draw_segmented_bar(draw, 24, 23, 84, 7, float(tel.bat_pct))
                draw_right_text(draw, 159, 22, f"{tel.bat_pct:.0f}%{plug}", FONT_TINY, fill=0)
            else:
                draw.text((1, 22), tel.gpu_label[:4], font=FONT_TINY, fill=0)
                vram_g = tel.gpu_vram_mb / 1024.0
                if self.show_peaks:
                    t_part = f" {tel.gpu_temp_c}°" if tel.gpu_temp_c is not None else ""
                    g_txt = f"Pic{tel.gpu_peak_util:3d}%{t_part}"
                    bar_w = 72
                elif tel.gpu_temp_c is not None:
                    g_txt = f"{tel.gpu_util:2d}% {vram_g:.1f}G {tel.gpu_temp_c}°"
                    bar_w = 64
                else:
                    g_txt = f"{tel.gpu_util:2d}% {vram_g:.1f}G"
                    bar_w = 76
                draw_segmented_bar(draw, 24, 23, bar_w, 7, float(tel.gpu_util))
                draw_right_text(draw, 159, 22, g_txt, FONT_TINY, fill=0)

        btn3_lbl = ("GPU" if self.show_bat else "BAT") if tel.has_battery else ""
        draw_softkeys(draw, ["VUE", "CORES", btn3_lbl, "PICS"])


# --- ÉCRAN 4 : MÉDIA 3 LIGNES + SPECTRE + 4 VISUALISEURS WINAMP / WMP ---
class MediaScreen(BaseScreen):
    VISU_NAMES = ("SPECTRE WINAMP", "OSCILLO WMP", "TUNNEL BASSES", "DOUBLE VU L/R", "AUTO-CYCLE 25s")

    def __init__(self):
        self.spectr_line = False
        self.visu_mode = False
        self.visu_idx = 0  # 0..3 = presets, 4 = Auto-Cycle 25s
        self.pin_title = False
        self.mode_toast_until = 0.0
        self._phase = 0.0

        # Matrice Bayer 4x4 précalculée pour l'effet Tunnel Basses
        if HAS_NUMPY:
            b4 = (1.0 / 16.0) * np.array(
                [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
                dtype=np.float32,
            ) + (0.5 / 16.0)
            self._bayer = np.tile(b4, (12, 40))[:LCD_H, :LCD_W]
            yy, xx = np.mgrid[0:LCD_H, 0:LCD_W].astype(np.float32)
            self._xx = xx
            self._yy = yy
        else:
            self._bayer = None

    def is_immersive(self) -> bool:
        return self.visu_mode

    def on_button(self, btn: int):
        if not self.visu_mode:
            # Boutons non-redondants de la vue Média 3 lignes
            if btn == 1:
                self._pending_seek = -15.0
            elif btn == 2:
                self._pending_seek = +30.0
            elif btn == 3:
                self.spectr_line = not self.spectr_line
            elif btn == 4:
                self.visu_mode = True
                self.mode_toast_until = time.monotonic() + 1.4
        else:
            # Boutons de la vue plein écran [Visu]
            if btn == 1:
                self.visu_idx = (self.visu_idx - 1) % 5
                self.mode_toast_until = time.monotonic() + 1.4
            elif btn == 2:
                self.visu_idx = (self.visu_idx + 1) % 5
                self.mode_toast_until = time.monotonic() + 1.4
            elif btn == 3:
                self.pin_title = not self.pin_title
            elif btn == 4:
                self.visu_mode = False

    @staticmethod
    def _fmt_mmss(sec: float) -> str:
        s = max(0, int(sec))
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

    def _render_media_3lines(self, draw: ImageDraw.ImageDraw, tel: TelemetryHub, af: AudioFrame):
        # Ligne 1 (y=0) : Artiste à gauche + Application source alignée à droite
        app_tag = tel.media_app
        app_px = text_width(draw, app_tag, FONT_TINY) + 4 if app_tag else 0
        if app_tag:
            draw_right_text(draw, 159, 0, app_tag, FONT_TINY, fill=0)

        artist_txt = tel.media_artist if tel.media_active else ""
        if not artist_txt and tel.media_active:
            artist_txt = "Média actif"
        draw.text((2, 0), truncate_to_px(draw, artist_txt, FONT_TINY, 156 - app_px), font=FONT_TINY, fill=0)

        # Ligne 2 (y=10) : Titre défilant fluide
        title = clean_str(tel.media_title)
        if text_width(draw, title, FONT_MED) > 156:
            padded = title + "   •   "
            idx = int(time.monotonic() * 4.5) % len(padded)
            loop_txt = (padded + padded)[idx : idx + 32]
            draw.text((2, 10), loop_txt, font=FONT_MED, fill=0)
        else:
            draw.text((2, 10), title, font=FONT_MED, fill=0)

        # Ligne 3 (y=23..33) : Spectre 1-bit OU Barre temporelle OU ► LECTURE (si durée absente)
        if self.spectr_line:
            draw.line((0, 33, 159, 33), fill=0)
            for i in range(20):
                bx = i * 8 + 1
                bh = int(af.bars_20[i] * 10)
                ph = int(af.peaks_20[i] * 10)
                if bh > 0:
                    draw.rectangle((bx, 33 - bh, bx + 5, 33), fill=0)
                if ph > 0:
                    py = max(23, 33 - ph)
                    draw.line((bx, py, bx + 5, py), fill=0)
        elif tel.media_active and tel.media_dur_sec > 0.5:
            draw_play_pause_5x5(draw, 2, 25, tel.media_playing, fill=0)
            time_lbl = f"{self._fmt_mmss(tel.media_pos_sec)}/{self._fmt_mmss(tel.media_dur_sec)}"
            tw = text_width(draw, time_lbl, FONT_TINY)
            draw_right_text(draw, 159, 23, time_lbl, FONT_TINY, fill=0)
            bar_x0 = 10
            bar_w = max(20, 155 - tw - bar_x0)
            pct = (tel.media_pos_sec / tel.media_dur_sec) * 100.0
            draw_segmented_bar(draw, bar_x0, 24, bar_w, 6, pct)
        elif tel.media_active:
            # Source sans durée totale : uniquement l'état ► LECTURE / ❚❚ PAUSE
            draw_play_pause_5x5(draw, 2, 25, tel.media_playing, fill=0)
            st_txt = "LECTURE" if tel.media_playing else "PAUSE"
            draw.text((10, 23), st_txt, font=FONT_TINY, fill=0)
        else:
            draw.text((2, 23), "En attente de lecture...", font=FONT_TINY, fill=0)

        draw_softkeys(draw, ["-15s", "+30s", "TEMPS" if self.spectr_line else "SPECTR", "VISU"])

    def _render_visu_spectrum(self, draw: ImageDraw.ImageDraw, af: AudioFrame, h: int):
        """Preset 0 : Analyseur Winamp segmenté + chapeaux de crête tombants."""
        draw.rectangle((0, 0, 159, h - 1), fill=0)
        max_h = h - 2
        for i in range(20):
            bx = i * 8 + 1
            bh = int(af.bars_20[i] * max_h)
            ph = int(af.peaks_20[i] * max_h)
            for sy in range(0, bh, 2):
                y_line = h - 1 - sy
                draw.line((bx, y_line, bx + 5, y_line), fill=1)
            if ph > 0:
                py = max(0, h - 1 - ph)
                draw.rectangle((bx, py, bx + 5, min(h - 1, py + 1)), fill=1)

    def _render_visu_oscillo(self, draw: ImageDraw.ImageDraw, af: AudioFrame, h: int):
        """Preset 1 : Oscilloscope d'onde WMP (Bars & Waves)."""
        draw.rectangle((0, 0, 159, h - 1), fill=0)
        mid = h // 2
        amp = (h // 2) - 2
        # Ligne de référence pointillée
        for x in range(0, 160, 4):
            draw.point((x, mid), fill=1)
        prev_y = mid
        for x, sample in enumerate(af.wave_160):
            y = max(0, min(h - 1, int(mid - sample * amp)))
            draw.line((max(0, x - 1), prev_y, x, y), fill=1)
            if abs(sample) > 0.45 and y + 1 < h:
                draw.point((x, y + 1), fill=1)
            prev_y = y

    def _render_visu_bass_tunnel(self, img: Image.Image, draw: ImageDraw.ImageDraw, af: AudioFrame, h: int):
        """Preset 2 : Tunnel psychédélique & figure de Lissajous réactifs aux basses (Geiss / Alchemy)."""
        kick = af.bass_energy
        self._phase += 0.14 + kick * 0.42
        if HAS_NUMPY and self._bayer is not None:
            dx = (self._xx[:h, :] - 80.0) / (1.0 + kick * 0.55)
            dy = (self._yy[:h, :] - (h * 0.5)) * 1.45 / (1.0 + kick * 0.55)
            dist = np.hypot(dx, dy) + 1.0
            ang = np.arctan2(dy, dx)
            val = 0.5 + 0.5 * np.sin(26.0 / dist - self._phase * 2.2 + np.sin(ang * 4.0 + self._phase))
            # Boost central sur les impacts de basse
            val = np.clip(val * (0.38 + kick * 0.85), 0.0, 1.0)
            lit = val > self._bayer[:h, :]
            # Rappel : dans PIL "1" de notre pipeline, 1 = ambré lumineux, 0 = cristal noir
            sub = Image.fromarray((lit.astype(np.uint8) * 255), mode="L").convert("1")
            img.paste(sub, (0, 0))
        else:
            draw.rectangle((0, 0, 159, h - 1), fill=0)

        # Courbe de Lissajous centrale réactive
        cx, cy = 80.0, h * 0.5
        rx = 18.0 + kick * 36.0
        ry = 6.0 + kick * (h * 0.36)
        pts = []
        for k in range(44):
            u = (k / 43.0) * math.tau
            px = int(cx + rx * math.sin(3.0 * u + self._phase))
            py = int(cy + ry * math.sin(2.0 * u))
            pts.append((max(0, min(159, px)), max(0, min(h - 1, py))))
        if len(pts) > 1:
            draw.line(pts, fill=1)

    def _render_visu_vumeter(self, draw: ImageDraw.ImageDraw, af: AudioFrame, h: int):
        """Preset 3 : Double VU-mètre analogique rétroéclairé Gauche / Droite à aiguilles."""
        draw.rectangle((0, 0, 159, h - 1), fill=0)
        panels = ((2, 78, "L", af.vu_left), (81, 157, "R", af.vu_right))
        for x0, x1, ch_lbl, level in panels:
            # Cadran rétroéclairé ambré
            draw.rectangle((x0, 1, x1, h - 2), fill=1, outline=0)
            cx = (x0 + x1) // 2
            cy = h - 3
            radius = min(32, h - 6)

            # Graduations d'arc (-55° .. +55°)
            for step in range(9):
                ang = math.radians(-55.0 + step * (110.0 / 8.0) - 90.0)
                r_in = radius - (4 if step in (0, 4, 8) else 2)
                gx0 = int(cx + r_in * math.cos(ang))
                gy0 = int(cy + r_in * math.sin(ang))
                gx1 = int(cx + radius * math.cos(ang))
                gy1 = int(cy + radius * math.sin(ang))
                draw.line((gx0, gy0, gx1, gy1), fill=0)

            draw.text((x0 + 3, 2), ch_lbl, font=FONT_TINY, fill=0)
            # Voyant PEAK si saturation proche
            if level > 0.85:
                draw.rectangle((x1 - 11, 3, x1 - 3, 8), fill=0)

            # Aiguille mobile
            needle_deg = -55.0 + max(0.0, min(1.0, level)) * 110.0 - 90.0
            n_ang = math.radians(needle_deg)
            nx = int(cx + (radius - 2) * math.cos(n_ang))
            ny = int(cy + (radius - 2) * math.sin(n_ang))
            draw.line((cx, cy, nx, ny), fill=0, width=1)
            draw.rectangle((cx - 2, cy - 2, cx + 2, cy), fill=0)

    def render(self, draw, tel, audio, hw, cfg, show_softkeys):
        seek_val = getattr(self, "_pending_seek", None)
        if seek_val is not None:
            tel.seek_media_relative(seek_val)
            self._pending_seek = None

        af = audio.snapshot()
        if not self.visu_mode:
            self._render_media_3lines(draw, tel, af)
            return

        h = LCD_CONTENT_H if show_softkeys else LCD_H
        now = time.monotonic()
        active_preset = int(now / cfg.auto_cycle_sec) % 4 if self.visu_idx == 4 else self.visu_idx

        if active_preset == 0:
            self._render_visu_spectrum(draw, af, h)
        elif active_preset == 1:
            self._render_visu_oscillo(draw, af, h)
        elif active_preset == 2:
            self._render_visu_bass_tunnel(draw._image, draw, af, h)
        else:
            self._render_visu_vumeter(draw, af, h)

        # Bandeau fugace de changement de morceau (3s, façon Winamp) ou titre épinglé
        if self.pin_title or now < tel.media_track_changed_until:
            track_lbl = (
                f"{tel.media_artist} - {tel.media_title}"
                if tel.media_artist
                else tel.media_title
            )
            draw_toast_banner(draw, track_lbl, y_center=h // 2)
        elif now < self.mode_toast_until:
            draw_toast_banner(draw, self.VISU_NAMES[self.visu_idx], y_center=h // 2)

        if show_softkeys:
            draw_softkeys(draw, ["< VISU", "VISU >", "TITRE", "RETOUR"])


# --- ÉCRAN 5 : RÉSEAU (CARTE PHYSIQUE UNIQUEMENT : WIFI PUIS ETH) ---
class NetworkScreen(BaseScreen):
    def __init__(self):
        self.frozen = False
        self.in_mbps = False
        self.snap_dl: list[float] = []
        self.snap_ul: list[float] = []

    def on_button(self, btn: int):
        if btn == 1:
            self.frozen = not self.frozen
        elif btn == 2:
            self.in_mbps = not self.in_mbps
        elif btn == 3:
            self._want_reset = True

    def _fmt_rate(self, bps: float) -> str:
        if self.in_mbps:
            return f"{(bps * 8.0) / 1_000_000.0:.1f}Mb"
        kb = bps / 1024.0
        return f"{kb:.0f}Ko" if kb < 1000 else f"{kb / 1024.0:.1f}Mo"

    def render(self, draw, tel, audio, hw, cfg, show_softkeys):
        if getattr(self, "_want_reset", False):
            tel.reset_net_totals()
            self._want_reset = False

        if not self.frozen:
            self.snap_dl = list(tel.dl_hist)
            self.snap_ul = list(tel.ul_hist)

        gw_ping = next(iter(tel.pings.values()), None)
        p_str = f"{gw_ping}ms" if gw_ping is not None else "--ms"
        hdr = f"{tel.phys_nic_tag} D:{self._fmt_rate(tel.dl_speed)} U:{self._fmt_rate(tel.ul_speed)}"
        draw.text((1, 0), hdr, font=FONT_TINY, fill=0)
        draw_right_text(draw, 159, 0, p_str, FONT_TINY, fill=0)

        mid_y = 21
        draw.line((0, mid_y, 159, mid_y), fill=0)
        peak = max(10240.0, max(self.snap_dl, default=1.0), max(self.snap_ul, default=1.0))

        for x in range(min(160, len(self.snap_dl))):
            dh = int((self.snap_dl[x] / peak) * 11)
            uh = int((self.snap_ul[x] / peak) * 11)
            if dh > 0:
                draw.line((x, mid_y - dh, x, mid_y), fill=0)
            if uh > 0:
                draw.line((x, mid_y, x, mid_y + uh), fill=0)

        draw_softkeys(draw, ["LIVE" if self.frozen else "PAUSE", "UNITÉ", "RAZ", f"{tel.dl_total_gb:.1f}G"])


# --- ÉCRAN 6 : SERVEURS & CONNEXION ACTIVE ([IP LOC] + BADGE [VPN]) ---
class ServersScreen(BaseScreen):
    def __init__(self):
        self.show_ip_loc = False

    def on_button(self, btn: int):
        if btn == 1:
            self.show_ip_loc = not self.show_ip_loc
        elif btn == 4:
            self._want_ping = True

    def render(self, draw, tel, audio, hw, cfg, show_softkeys):
        if getattr(self, "_want_ping", False):
            tel.trigger_ping_and_wan_refresh()
            self._want_ping = False

        if self.show_ip_loc:
            nic_disp = tel.phys_nic_name or "Aucune carte"
            vpn_w = 0
            if tel.vpn_active:
                # Badge [VPN] en vidéo inversée en haut à droite
                vpn_w = 30
                draw.rectangle((130, 0, 159, 9), fill=0)
                draw_centered_in_box(draw, 130, 0, 30, 10, "VPN", FONT_TINY, fill=1)

            draw.text(
                (2, 0),
                truncate_to_px(draw, f"Carte : {nic_disp}", FONT_TINY, 155 - vpn_w),
                font=FONT_TINY,
                fill=0,
            )
            draw.text((2, 11), f"IP Loc : {tel.local_ip}", font=FONT_TINY, fill=0)
            draw.text((2, 22), f"IP Pub : {tel.public_ip}", font=FONT_TINY, fill=0)
        else:
            for idx, (name, _) in enumerate(cfg.hosts[:3]):
                ms = tel.pings.get(name)
                y = idx * 11
                state = f"[OK] {ms:3d} ms" if ms is not None else "[DOWN] ---"
                draw.text((2, y), name, font=FONT_TINY, fill=0)
                draw_right_text(draw, 158, y, state, FONT_TINY, fill=0)

        draw_softkeys(draw, ["SERV" if self.show_ip_loc else "IP LOC", "", "", "PING!"])


# --- ÉCRAN 7 : MÉTÉO (GÉO-IP AUTO / REPLI Lausanne 1000 + ICÔNES 16x16 + J+6 & 24H) ---
class WeatherScreen(BaseScreen):
    def __init__(self):
        self.day_idx = 0
        self.mode_24h = False

    def on_button(self, btn: int):
        if btn == 1 and not self.mode_24h:
            self.day_idx = max(0, self.day_idx - 1)
        elif btn == 2 and not self.mode_24h:
            self.day_idx = min(6, self.day_idx + 1)
        elif btn == 3:
            self.mode_24h = not self.mode_24h
        elif btn == 4:
            self._want_refresh = True

    def render(self, draw, tel, audio, hw, cfg, show_softkeys):
        if getattr(self, "_want_refresh", False):
            tel.trigger_weather_refresh()
            self._want_refresh = False

        if not tel.weather_online or not tel.weather_days:
            draw.text((2, 0), tel.weather_city, font=FONT_TINY, fill=0)
            draw.text((2, 12), "Météo en attente...", font=FONT_MED, fill=0)
            draw_softkeys(draw, ["< JOUR", "JOUR >", "24H", "MAJ"])
            return

        if self.mode_24h and len(tel.weather_hourly_temp) >= 2:
            temps = tel.weather_hourly_temp[:24]
            rains = tel.weather_hourly_rain[:24]
            t_min, t_max = min(temps), max(temps)
            span = max(1.0, t_max - t_min)

            draw.text((2, 0), truncate_to_px(draw, f"{tel.weather_city} 24h", FONT_TINY, 96), font=FONT_TINY, fill=0)
            draw_right_text(draw, 159, 0, f"{t_min:.0f}°..{t_max:.0f}°C", FONT_TINY, fill=0)
            draw.rectangle((0, 10, 159, 33), outline=0)

            n = len(temps)
            prev_pt = None
            for i in range(n):
                x = 3 + int((i / max(1, n - 1)) * 153)
                # Barres verticales de probabilité de pluie en bas du graphe
                if i < len(rains) and rains[i] >= 20:
                    rh = max(1, int((rains[i] / 100.0) * 7))
                    draw.line((x, 32 - rh, x, 32), fill=0)
                # Courbe de température
                ty = 30 - int(((temps[i] - t_min) / span) * 17)
                if prev_pt is not None:
                    draw.line((prev_pt[0], prev_pt[1], x, ty), fill=0)
                prev_pt = (x, ty)
        else:
            idx = min(self.day_idx, len(tel.weather_days) - 1)
            day = tel.weather_days[idx]
            code = tel.weather_code_now if idx == 0 else day.wmo_code
            sprite_key, desc = wmo_info(code)

            # En-tête : Ville à gauche + Jour aligné à droite
            day_w = text_width(draw, day.date_label, FONT_TINY) + 4
            city_lbl = f"{tel.weather_city}"
            draw.text((2, 0), truncate_to_px(draw, city_lbl, FONT_TINY, 156 - day_w), font=FONT_TINY, fill=0)
            draw_right_text(draw, 159, 0, day.date_label, FONT_TINY, fill=0)

            # Icône météo pixel-art 16x16 à gauche
            draw_weather_icon(draw, 2, 13, sprite_key, fill=0)

            # Température + description
            if idx == 0 and tel.weather_temp_now is not None:
                line2 = f"{tel.weather_temp_now:.1f}°C ({day.t_min:.0f}°/{day.t_max:.0f}°)"
            else:
                line2 = f"{day.t_min:.0f}°/{day.t_max:.0f}°C {desc}"
            draw.text((22, 10), truncate_to_px(draw, line2, FONT_MED, 137), font=FONT_MED, fill=0)

            wind_val = tel.weather_wind_now if idx == 0 else day.wind_kmh
            line3 = f"{desc} · V:{wind_val:.0f}km/h · Pluie {day.rain_prob}%" if idx == 0 else f"Vent {wind_val:.0f}km/h · Pluie {day.rain_prob}%"
            draw.text((22, 23), truncate_to_px(draw, line3, FONT_TINY, 137), font=FONT_TINY, fill=0)

        draw_softkeys(
            draw,
            [
                "" if self.mode_24h else "< JOUR",
                "" if self.mode_24h else "JOUR >",
                "JOURS" if self.mode_24h else "24H",
                "MAJ",
            ],
        )


# --- ÉCRAN 8 : DEMOSCENE PLEIN ÉCRAN 160x43 (6 EFFETS 1-BIT + AUTO 25s + PONG) ---
class DemosceneScreen(BaseScreen):
    EFFECT_NAMES = (
        "HYPERESPACE 4x4",
        "MATRIX RAIN",
        "PLASMA BAYER",
        "DOOM FIRE",
        "TORUS 3D",
        "ROTOZOOMER",
    )

    def __init__(self):
        self.effect_idx = 0
        self.auto_cycle = False
        self.in_pong = False
        self.effect_toast_until = 0.0
        self._t = 0.0

        # 0. Hyperespace (x, y, z, prev_z)
        self.stars = [
            [random.uniform(-90, 90), random.uniform(-26, 26), random.uniform(2, 64)]
            for _ in range(75)
        ]

        # 1. Matrix
        self.drops_y = [random.uniform(-40, 42) for _ in range(40)]
        self.drops_spd = [random.uniform(1.2, 2.8) for _ in range(40)]

        # 2 & 3. Précalculs Numpy pour Plasma Bayer, Doom Fire & Rotozoomer
        if HAS_NUMPY:
            b4 = (1.0 / 16.0) * np.array(
                [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]],
                dtype=np.float32,
            ) + (0.5 / 16.0)
            self._bayer = np.tile(b4, (12, 40))[:LCD_H, :LCD_W]
            yy, xx = np.mgrid[0:LCD_H, 0:LCD_W].astype(np.float32)
            self._xx = xx
            self._yy = yy
            self._fire = np.zeros((LCD_H + 2, LCD_W), dtype=np.float32)
            self._fire[-1, :] = 1.0

            # Texture 16x16 procédurale (motif alien/géométrique) pour le Rotozoomer
            tex = np.zeros((16, 16), dtype=np.uint8)
            tex[1:15, 1:15] = 1
            tex[3:13, 3:13] = 0
            tex[5:11, 5:11] = 1
            tex[7:9, 4:12] = 0
            tex[4:12, 7:9] = 0
            self._roto_tex = tex
        else:
            self._bayer = None

        # 4. Maillage Torus 3D précalculé (10 anneaux x 8 facettes) pour hidden-face culling
        self._torus_verts, self._torus_quads = self._build_torus(n_u=12, n_v=7, r_maj=15.5, r_min=6.5)

        # Pong rééquilibré
        self.pad_y = 14.0
        self.ai_y = 14.0
        self.bx, self.by = 80.0, 20.0
        self.vx, self.vy = 3.1, 1.5
        self.score_p = 0
        self.score_ai = 0
        self._ai_err = 0.0

    @staticmethod
    def _build_torus(n_u: int, n_v: int, r_maj: float, r_min: float):
        verts = []
        for iu in range(n_u):
            u = (iu / n_u) * math.tau
            cu, su = math.cos(u), math.sin(u)
            for iv in range(n_v):
                v = (iv / n_v) * math.tau
                cv, sv = math.cos(v), math.sin(v)
                x = (r_maj + r_min * cv) * cu
                y = (r_maj + r_min * cv) * su
                z = r_min * sv
                verts.append((x, y, z))
        quads = []
        for iu in range(n_u):
            niu = (iu + 1) % n_u
            for iv in range(n_v):
                niv = (iv + 1) % n_v
                quads.append((iu * n_v + iv, niu * n_v + iv, niu * n_v + niv, iu * n_v + niv))
        return verts, quads

    def is_immersive(self) -> bool:
        return True

    def bypass_wake_for_button(self, btn: int) -> bool:
        # En plein Pong, les boutons 1 et 2 déplacent la raquette sans réafficher la barre
        return self.in_pong and btn in (BTN_1, BTN_2)

    def on_button(self, btn: int):
        if not self.in_pong:
            if btn == 1:
                self.auto_cycle = False
                self.effect_idx = (self.effect_idx - 1) % len(self.EFFECT_NAMES)
                self.effect_toast_until = time.monotonic() + 1.2
            elif btn == 2:
                self.auto_cycle = False
                self.effect_idx = (self.effect_idx + 1) % len(self.EFFECT_NAMES)
                self.effect_toast_until = time.monotonic() + 1.2
            elif btn == 3:
                self.auto_cycle = not self.auto_cycle
                lbl = "AUTO-CYCLE ON" if self.auto_cycle else "AUTO-CYCLE OFF"
                self._custom_toast = lbl
                self.effect_toast_until = time.monotonic() + 1.2
            elif btn == 4:
                self.in_pong = True
        else:
            if btn == 1:
                self.pad_y = max(1.0, self.pad_y - 3.0)
            elif btn == 2:
                self.pad_y = min(32.0, self.pad_y + 3.0)
            elif btn == 3:
                self.score_p = self.score_ai = 0
                self._reset_ball(direction=1)
            elif btn == 4:
                self.in_pong = False

    def _reset_ball(self, direction: int):
        self.bx = 80.0
        self.by = 18.0
        self.vx = 3.0 * direction
        self.vy = random.choice((-1.6, -1.1, 1.1, 1.6))
        self._ai_err = random.uniform(-3.2, 3.2)

    def _render_hyperspace(self, draw: ImageDraw.ImageDraw, h: int):
        """Particules grandissant de 1x1 jusqu'à des blocs 4x4 + traînée de vitesse."""
        draw.rectangle((0, 0, 159, h - 1), fill=0)
        cy = h * 0.5
        for s in self.stars:
            old_z = s[2]
            s[2] -= 1.85
            if s[2] <= 1.2:
                s[0] = random.uniform(-90, 90)
                s[1] = random.uniform(-26, 26)
                s[2] = 64.0
                old_z = 64.0

            sx = int(80.0 + (s[0] / s[2]) * 46.0)
            sy = int(cy + (s[1] / s[2]) * 26.0)
            px = int(80.0 + (s[0] / old_z) * 46.0)
            py = int(cy + (s[1] / old_z) * 26.0)

            if 0 <= sx < 160 and 0 <= sy < h:
                draw.line((max(0, min(159, px)), max(0, min(h - 1, py)), sx, sy), fill=1)
                # Grossissement progressif jusqu'à 4x4 au premier plan
                if s[2] < 10.0:
                    size = 4
                elif s[2] < 22.0:
                    size = 3
                elif s[2] < 38.0:
                    size = 2
                else:
                    size = 1
                x2 = min(159, sx + size - 1)
                y2 = min(h - 1, sy + size - 1)
                draw.rectangle((sx, sy, x2, y2), fill=1)

    def _render_matrix(self, draw: ImageDraw.ImageDraw, h: int):
        draw.rectangle((0, 0, 159, h - 1), fill=0)
        for i in range(40):
            x = i * 4
            y = int(self.drops_y[i])
            for tail in range(0, 15, 3):
                py = y - tail
                if 0 <= py < h:
                    if tail == 0:
                        draw.rectangle((x, py, x + 2, min(h - 1, py + 1)), fill=1)
                    elif (py + i) % 2 == 0:
                        draw.rectangle((x, py, x + 1, py), fill=1)
            self.drops_y[i] += self.drops_spd[i]
            if self.drops_y[i] - 15 > h:
                self.drops_y[i] = random.uniform(-22, -2)
                self.drops_spd[i] = random.uniform(1.2, 2.8)

    def _render_plasma(self, img: Image.Image, draw: ImageDraw.ImageDraw, h: int):
        if not HAS_NUMPY or self._bayer is None:
            self._render_matrix(draw, h)
            return
        t = self._t
        x = self._xx[:h, :]
        y = self._yy[:h, :]
        v = (
            np.sin(x * 0.075 + t * 2.2)
            + np.sin(y * 0.12 - t * 1.8)
            + np.sin((x + y) * 0.065 + t * 1.4)
            + np.sin(np.hypot(x - (80.0 + 36.0 * math.cos(t * 0.9)), y - (h * 0.5 + 11.0 * math.sin(t * 1.3))) * 0.14 - t * 2.8)
        )
        norm = (v + 4.0) * 0.125
        lit = norm > self._bayer[:h, :]
        sub = Image.fromarray((lit.astype(np.uint8) * 255), mode="L").convert("1")
        img.paste(sub, (0, 0))

    def _render_doom_fire(self, img: Image.Image, draw: ImageDraw.ImageDraw, h: int):
        if not HAS_NUMPY or self._bayer is None:
            self._render_hyperspace(draw, h)
            return
        # Foyer ardent en bas de l'écran actif
        self._fire[h, :] = np.random.uniform(0.82, 1.0, size=LCD_W)
        for y in range(h):
            shift = random.choice((-1, 0, 0, 1))
            decay = np.random.uniform(0.018, 0.052, size=LCD_W).astype(np.float32)
            self._fire[y, :] = np.maximum(0.0, np.roll(self._fire[y + 1, :], shift) - decay)
        lit = self._fire[:h, :] > self._bayer[:h, :]
        sub = Image.fromarray((lit.astype(np.uint8) * 255), mode="L").convert("1")
        img.paste(sub, (0, 0))

    def _render_torus_3d(self, draw: ImageDraw.ImageDraw, h: int):
        draw.rectangle((0, 0, 159, h - 1), fill=0)
        ax = self._t * 1.15
        ay = self._t * 0.78
        cx_a, sx_a = math.cos(ax), math.sin(ax)
        cy_a, sy_a = math.cos(ay), math.sin(ay)
        cy_scr = h * 0.5

        cam_pts = []
        proj_pts = []
        for vx, vy, vz in self._torus_verts:
            # Rotation X puis Y
            y1 = vy * cx_a - vz * sx_a
            z1 = vy * sx_a + vz * cx_a
            x2 = vx * cy_a + z1 * sy_a
            z2 = -vx * sy_a + z1 * cy_a + 44.0

            cam_pts.append((x2, y1, z2))
            fov = 62.0 / max(8.0, z2)
            px = int(80.0 + x2 * fov * 1.25)
            py = int(cy_scr + y1 * fov)
            proj_pts.append((px, py))

        # Suppression des faces cachées (back-face culling en espace caméra)
        for i0, i1, i2, i3 in self._torus_quads:
            p0, p1, p2 = cam_pts[i0], cam_pts[i1], cam_pts[i2]
            ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
            wx, wy, wz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
            nz = ux * wy - uy * wx
            if nz < 0.0:
                q0, q1, q2, q3 = proj_pts[i0], proj_pts[i1], proj_pts[i2], proj_pts[i3]
                draw.line((q0, q1, q2, q3, q0), fill=1)

    def _render_rotozoomer(self, img: Image.Image, draw: ImageDraw.ImageDraw, h: int):
        if not HAS_NUMPY:
            self._render_torus_3d(draw, h)
            return
        ang = self._t * 0.95
        scale = 0.22 + 0.14 * math.sin(self._t * 0.75)
        ca = math.cos(ang) * scale
        sa = math.sin(ang) * scale
        dx = self._xx[:h, :] - 80.0
        dy = self._yy[:h, :] - (h * 0.5)
        u = ((dx * ca - dy * sa + self._t * 14.0).astype(np.int32)) & 15
        v = ((dx * sa + dy * ca + self._t * 8.0).astype(np.int32)) & 15
        lit = self._roto_tex[v, u]
        sub = Image.fromarray((lit * 255), mode="L").convert("1")
        img.paste(sub, (0, 0))

    def _render_pong(self, draw: ImageDraw.ImageDraw, hw: "G15HidController", h: int):
        draw.rectangle((0, 0, 159, h - 1), fill=0)
        pad_h = 10
        max_pad_y = float(h - pad_h - 1)

        # Glisse fluide 20 FPS sur maintien des boutons 1 et 2
        if hw.is_button_held(BTN_1):
            self.pad_y -= 2.7
        if hw.is_button_held(BTN_2):
            self.pad_y += 2.7
        self.pad_y = max(1.0, min(max_pad_y, self.pad_y))
        self.ai_y = max(1.0, min(max_pad_y, self.ai_y))

        # Physique de la balle
        self.bx += self.vx
        self.by += self.vy
        if self.by <= 1.0:
            self.by = 1.0
            self.vy = abs(self.vy)
        elif self.by >= h - 3.0:
            self.by = h - 3.0
            self.vy = -abs(self.vy)

        # IA battable : ne réagit que quand la balle franchit x > 62 vers la droite + vitesse plafonnée
        if self.vx > 0 and self.bx > 62.0:
            target_y = (self.by - pad_h * 0.5) + self._ai_err
            diff = target_y - self.ai_y
            ai_max_spd = 1.75
            if abs(diff) > 1.2:
                self.ai_y += max(-ai_max_spd, min(ai_max_spd, diff * 0.35))
        else:
            # Recentrage lent en attente
            center_diff = (h * 0.5 - pad_h * 0.5) - self.ai_y
            self.ai_y += max(-0.6, min(0.6, center_diff * 0.1))
        self.ai_y = max(1.0, min(max_pad_y, self.ai_y))

        # Rebond angulaire sur la raquette joueur
        if self.vx < 0 and 3.0 <= self.bx <= 6.5 and (self.pad_y - 2.0) <= self.by <= (self.pad_y + pad_h + 1.0):
            hit_norm = ((self.by - self.pad_y) / pad_h) * 2.0 - 1.0  # -1.0 (haut) .. +1.0 (bas)
            self.vx = min(4.6, abs(self.vx) * 1.04)
            self.vy = max(-2.9, min(2.9, hit_norm * 2.6))
            self._ai_err = random.uniform(-3.6, 3.6)

        # Rebond sur la raquette IA
        elif self.vx > 0 and 151.0 <= self.bx <= 155.0 and (self.ai_y - 2.0) <= self.by <= (self.ai_y + pad_h + 1.0):
            hit_norm = ((self.by - self.ai_y) / pad_h) * 2.0 - 1.0
            self.vx = -min(4.6, abs(self.vx) * 1.03)
            self.vy = max(-2.7, min(2.7, hit_norm * 2.3))

        elif self.bx < 0.0:
            self.score_ai += 1
            self._reset_ball(direction=1)
        elif self.bx > 159.0:
            self.score_p += 1
            self._reset_ball(direction=-1)

        # Filet central pointillé, raquettes, balle et score
        for ny in range(1, h - 1, 4):
            draw.line((79, ny, 79, min(h - 2, ny + 1)), fill=1)

        py_i = int(self.pad_y)
        ay_i = int(self.ai_y)
        draw.rectangle((3, py_i, 5, py_i + pad_h - 1), fill=1)
        draw.rectangle((154, ay_i, 156, ay_i + pad_h - 1), fill=1)
        draw.rectangle((int(self.bx), int(self.by), int(self.bx) + 2, int(self.by) + 2), fill=1)
        draw_centered_in_box(draw, 50, 1, 60, 9, f"{self.score_p}   {self.score_ai}", FONT_TINY, fill=1)

    def render(self, draw, tel, audio, hw, cfg, show_softkeys):
        h = LCD_CONTENT_H if show_softkeys else LCD_H
        now = time.monotonic()
        self._t += 1.0 / max(5, cfg.fps)

        if self.in_pong:
            self._render_pong(draw, hw, h)
            if show_softkeys:
                draw_softkeys(draw, ["HAUT", "BAS", "ZÉRO", "QUITT"])
            return

        eff = int(now / cfg.auto_cycle_sec) % len(self.EFFECT_NAMES) if self.auto_cycle else self.effect_idx
        if eff == 0:
            self._render_hyperspace(draw, h)
        elif eff == 1:
            self._render_matrix(draw, h)
        elif eff == 2:
            self._render_plasma(draw._image, draw, h)
        elif eff == 3:
            self._render_doom_fire(draw._image, draw, h)
        elif eff == 4:
            self._render_torus_3d(draw, h)
        else:
            self._render_rotozoomer(draw._image, draw, h)

        if now < self.effect_toast_until:
            lbl = getattr(self, "_custom_toast", None) or self.EFFECT_NAMES[eff]
            self._custom_toast = None
            draw_toast_banner(draw, lbl, y_center=h // 2)

        if show_softkeys:
            auto_lbl = "AUTO*" if self.auto_cycle else "AUTO"
            draw_softkeys(draw, ["< EFF", "EFF >", auto_lbl, "PONG"])


# ==============================================================================
# CONTRÔLEUR HID G15 (HANDLES SÉPARÉS, ÉTAT MAINTENU & HOT-PLUG AUTO 2s)
# ==============================================================================
CALIB_STEPS = [
    (BTN_APPLET, "BOUTON ROND", ["", "", "", ""]),
    (BTN_1, "TOUCHE ÉCRAN 1", ["ICI", "", "", ""]),
    (BTN_2, "TOUCHE ÉCRAN 2", ["", "ICI", "", ""]),
    (BTN_3, "TOUCHE ÉCRAN 3", ["", "", "ICI", ""]),
    (BTN_4, "TOUCHE ÉCRAN 4", ["", "", "", "ICI"]),
]

if HAS_NUMPY:
    _BIT_WEIGHTS = (1 << np.arange(8, dtype=np.uint8)).reshape(1, 8, 1)


class G15HidController:
    def __init__(self, cfg_mgr: ConfigManager):
        self.cfg_mgr = cfg_mgr
        self.running = True
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self.lcd_dev = None
        self.read_devs = []
        self.connected = False
        self._last_reconnect_t = 0.0

        self.event_queue = queue.Queue()
        self.screen_blanked = False

        self.keymap = self._load_keymap()
        self.calibrating = self.keymap is None
        self.calib_idx = 0
        self.calib_down_pkt = None
        self.calib_temp = {}

        self.pressed_set: set[int] = set()
        self.applet_press_time: Optional[float] = None
        self.applet_long_handled = False

        self._try_connect(initial=True)
        threading.Thread(target=self._reader_loop, daemon=True).start()

    def _load_keymap(self) -> Optional[dict]:
        if KEYMAP_FILE.exists():
            try:
                data = json.loads(KEYMAP_FILE.read_text(encoding="utf-8"))
                if len(data) == 5:
                    return {int(k): v for k, v in data.items()}
            except Exception:
                pass
        # Utilise le profil natif 046d:c227 s'il est détecté, tout en générant g15_keymap.json
        try:
            KEYMAP_FILE.write_text(
                json.dumps({str(k): v for k, v in DEFAULT_G15V2_KEYMAP.items()}, indent=2),
                encoding="utf-8",
            )
            return dict(DEFAULT_G15V2_KEYMAP)
        except Exception:
            return dict(DEFAULT_G15V2_KEYMAP)

    def is_button_held(self, btn_id: int) -> bool:
        with self._state_lock:
            return btn_id in self.pressed_set

    def start_calibration(self):
        with self._state_lock:
            self.calib_idx = 0
            self.calib_down_pkt = None
            self.calib_temp = {}
            self.calibrating = True
            self.pressed_set.clear()
        print("[CALIBRATION] Appuie puis relâche chaque bouton demandé sur l'écran du G15...")

    def _close_handles_unlocked(self):
        for dev in self.read_devs:
            if dev is not self.lcd_dev:
                try:
                    dev.close()
                except Exception:
                    pass
        self.read_devs.clear()
        if self.lcd_dev is not None:
            try:
                self.lcd_dev.close()
            except Exception:
                pass
            self.lcd_dev = None
        with self._state_lock:
            self.pressed_set.clear()
            self.applet_press_time = None

    def _try_connect(self, initial: bool = False) -> bool:
        with self._io_lock:
            self._close_handles_unlocked()
            blank_pkt = self._encode_frame(Image.new("1", (LCD_W, LCD_H), 1))
            try:
                all_infos = [d for d in hid.enumerate(VID) if d.get("product_id") in PIDS]
            except Exception:
                all_infos = []

            if not all_infos:
                if initial:
                    print("[HID] Aucun clavier Logitech G15 détecté — attente de connexion USB (hot-plug 2s)...")
                self.connected = False
                return False

            debug = self.cfg_mgr.get().debug_hid
            for d in all_infos:
                if debug:
                    print(
                        f"[HID] Détecté PID=0x{d['product_id']:04X} "
                        f"iface={d.get('interface_number')} "
                        f"usage_page=0x{d.get('usage_page', 0):04X} usage=0x{d.get('usage', 0):04X}"
                    )

            # 1. Handle exclusif d'écriture LCD (collection constructeur >= 0xFF00)
            for d in all_infos:
                up = d.get("usage_page", 0)
                if up >= 0xFF00 or up == 0:
                    try:
                        wdev = hid.device()
                        wdev.open_path(d["path"])
                        if wdev.write(blank_pkt) > 0:
                            self.lcd_dev = wdev
                            break
                        wdev.close()
                    except OSError:
                        continue

            if self.lcd_dev is None:
                if initial:
                    print("[HID] G15 trouvé mais écran inaccessible — nouvelle tentative toutes les 2s...")
                self.connected = False
                return False

            # 2. Handles de lecture non-bloquants séparés (évite les blocages Overlapped I/O Windows)
            for d in all_infos:
                sig = (d.get("usage_page", 0), d.get("usage", 0))
                if sig in ((0x01, 0x06), (0x0C, 0x01)):
                    continue
                try:
                    rdev = hid.device()
                    rdev.open_path(d["path"])
                    rdev.set_nonblocking(True)
                    self.read_devs.append(rdev)
                except OSError:
                    continue

            if not self.read_devs:
                self.lcd_dev.set_nonblocking(True)
                self.read_devs.append(self.lcd_dev)

            self.connected = True
            print("[HID] Logitech G15 connecté et opérationnel.")
            return True

    def _mark_disconnected(self):
        if self.connected:
            print("[HID] Déconnexion USB ou mise en veille détectée — reconnexion auto toutes les 2s...")
        self.connected = False
        with self._io_lock:
            self._close_handles_unlocked()

    @staticmethod
    def _encode_frame(img: Image.Image) -> bytes:
        if HAS_NUMPY:
            arr = np.zeros((48, LCD_W), dtype=np.uint8)
            # img en mode "1" -> np.asarray donne False (0) pour pixel noir allumé
            arr[:LCD_H, :] = (np.asarray(img) == 0).astype(np.uint8)
            bands = arr.reshape(6, 8, LCD_W)
            packed = (bands * _BIT_WEIGHTS).sum(axis=1, dtype=np.uint8)
            buf = bytearray(992)
            buf[0] = 0x03
            buf[32:] = packed.tobytes()
            return bytes(buf)

        px = img.load()
        buf = bytearray(992)
        buf[0] = 0x03
        offset = 32
        for band in range(6):
            y_base = band * 8
            for x in range(LCD_W):
                b = 0
                for bit in range(8):
                    y = y_base + bit
                    if y < LCD_H and px[x, y] == 0:
                        b |= 1 << bit
                buf[offset] = b
                offset += 1
        return bytes(buf)

    def send_image(self, img: Image.Image):
        if not self.connected:
            now = time.monotonic()
            if now - self._last_reconnect_t >= 2.0:
                self._last_reconnect_t = now
                self._try_connect(initial=False)
            return

        pkt = self._encode_frame(img)
        try:
            with self._io_lock:
                if self.lcd_dev is None:
                    return
                res = self.lcd_dev.write(pkt)
            if res < 0:
                self._mark_disconnected()
        except (OSError, ValueError):
            self._mark_disconnected()

    def render_calibration(self, draw: ImageDraw.ImageDraw):
        _, label, softkeys = CALIB_STEPS[self.calib_idx]
        draw.text((2, 0), f"CALIBRATION ({self.calib_idx + 1}/5)", font=FONT_TINY, fill=0)
        if self.calib_down_pkt is None:
            draw.text((2, 11), f"Appuie : {label}", font=FONT_MED, fill=0)
        else:
            draw.text((2, 11), "Relâche le bouton...", font=FONT_MED, fill=0)
        draw_softkeys(draw, softkeys)

    def _handle_calibration_pkt(self, pkt: list[int]):
        if self.calib_down_pkt is None:
            if any(b != 0 for b in pkt[1:]):
                self.calib_down_pkt = list(pkt)
            return

        if pkt[0] != self.calib_down_pkt[0] or len(pkt) != len(self.calib_down_pkt):
            return

        found = None
        for idx in range(1, len(pkt)):
            released_bits = self.calib_down_pkt[idx] & ~pkt[idx]
            if released_bits:
                lsb = released_bits & -released_bits
                candidate = {"report_id": pkt[0], "byte": idx, "mask": lsb}
                if candidate not in self.calib_temp.values():
                    found = candidate
                    break

        if found is not None:
            btn_id, btn_name, _ = CALIB_STEPS[self.calib_idx]
            self.calib_temp[btn_id] = found
            print(
                f"[CALIBRATION] {btn_name} -> Report 0x{found['report_id']:02X}, "
                f"Octet {found['byte']}, Masque 0x{found['mask']:02X}"
            )
            self.calib_down_pkt = None
            self.calib_idx += 1

            if self.calib_idx >= len(CALIB_STEPS):
                self.keymap = self.calib_temp
                KEYMAP_FILE.write_text(
                    json.dumps({str(k): v for k, v in self.keymap.items()}, indent=2),
                    encoding="utf-8",
                )
                print(f"[CALIBRATION] Sauvegardée dans {KEYMAP_FILE}")
                self.calibrating = False

    def _handle_normal_pkt(self, pkt: list[int]):
        if not self.keymap:
            return
        relevant = [
            btn_id
            for btn_id, cfg in self.keymap.items()
            if pkt[0] == cfg["report_id"] and len(pkt) > cfg["byte"]
        ]
        if not relevant:
            return

        with self._state_lock:
            now_pressed = set(self.pressed_set)
            for btn_id in relevant:
                cfg = self.keymap[btn_id]
                if pkt[cfg["byte"]] & cfg["mask"]:
                    now_pressed.add(btn_id)
                else:
                    now_pressed.discard(btn_id)

            newly_pressed = now_pressed - self.pressed_set
            newly_released = self.pressed_set - now_pressed

            for b in (BTN_1, BTN_2, BTN_3, BTN_4):
                if b in newly_pressed:
                    self.event_queue.put(b)

            if BTN_APPLET in newly_pressed:
                self.applet_press_time = time.monotonic()
                self.applet_long_handled = False

            if BTN_APPLET in newly_released and self.applet_press_time is not None:
                if not self.applet_long_handled:
                    self.event_queue.put("NEXT_PAGE")
                self.applet_press_time = None

            self.pressed_set = now_pressed

    def _reader_loop(self):
        while self.running:
            if not self.connected:
                time.sleep(0.05)
                continue

            devs_snapshot = list(self.read_devs)
            debug = self.cfg_mgr.get().debug_hid
            had_error = False

            for rdev in devs_snapshot:
                while self.running and self.connected:
                    try:
                        pkt = rdev.read(64)
                    except (OSError, ValueError):
                        break
                    if not pkt:
                        break
                    if debug:
                        hex_str = " ".join(f"{b:02X}" for b in pkt)
                        print(f"[HID RX] ({len(pkt)} octets) : {hex_str}")

                    if self.calibrating:
                        self._handle_calibration_pkt(pkt)
                    else:
                        self._handle_normal_pkt(pkt)

            if had_error:
                self._mark_disconnected()
                continue

            with self._state_lock:
                if not self.calibrating and self.applet_press_time is not None and not self.applet_long_handled:
                    if time.monotonic() - self.applet_press_time > 0.9:
                        self.applet_long_handled = True
                        self.event_queue.put("TOGGLE_BLANK")

            time.sleep(0.01)

    def poll_events(self) -> list:
        evs = []
        while not self.event_queue.empty():
            evs.append(self.event_queue.get_nowait())
        return evs

    def close(self):
        self.running = False
        time.sleep(0.03)
        if self.connected:
            try:
                self.send_image(Image.new("1", (LCD_W, LCD_H), 1))
            except Exception:
                pass
        with self._io_lock:
            self._close_handles_unlocked()


# ==============================================================================
# CONTRÔLEUR LOGITECH G19 (LIBUSB / PYUSB, ÉCRAN COULEUR 320x240, MÊME INTERFACE QUE LE G15)
# ==============================================================================
# Thèmes (couleur fond "LCD éteint", couleur pixel "allumé") ; le rendu 160x43 reste inchangé.
G19_THEMES = {
    "amber": ((235, 150, 0), (28, 14, 0), (255, 140, 0)),     # look G15 v2 d'origine
    "blue": ((70, 120, 255), (0, 8, 40), (40, 90, 255)),      # look G15 v1
    "green": ((120, 220, 40), (6, 26, 0), (60, 255, 0)),
    "white": ((235, 235, 235), (10, 10, 10), (255, 255, 255)),
    "amber-dark": ((8, 5, 0), (255, 170, 0), (255, 140, 0)),  # fond noir, pixels ambre
    "matrix": ((0, 8, 0), (60, 255, 80), (0, 255, 0)),
}

# Touches LCD du G19 -> boutons logiques du HUD (modifiable via g19_keymap.json : {"UP":"1",...})
G19_DEFAULT_KEYMAP = {
    "MENU": BTN_APPLET, "OK": BTN_APPLET,
    "UP": BTN_1, "DOWN": BTN_2, "LEFT": BTN_3, "RIGHT": BTN_4,
    "CANCEL": "PREV_PAGE",     # touche retour : écran précédent
    "HOME": "TOGGLE_BLANK",    # touche carrée : éteint / rallume l'écran
}


class G19Controller:
    """Remplace G15HidController : mêmes attributs/méthodes publics, rendu 160x43 -> 320x240 RGB565."""

    def __init__(self, cfg_mgr: ConfigManager):
        self.cfg_mgr = cfg_mgr
        self.running = True
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.dev = g19_lcd.G19Device()
        self.connected = False
        self._last_reconnect_t = 0.0
        self._debug = os.environ.get("G19_DEBUG", "") not in ("", "0")

        self.event_queue = queue.Queue()
        self.screen_blanked = False
        self.calibrating = False  # pas de calibration : les 8 touches LCD du G19 ont des bits fixes
        self.pressed_set: set[int] = set()
        self.applet_press_time: Optional[float] = None
        self.applet_long_handled = False

        theme = os.environ.get("G19_THEME", "amber").lower()
        self.bg, self.fg, self.backlight = G19_THEMES.get(theme, G19_THEMES["amber"])
        self.layout = os.environ.get("G19_LAYOUT", "2x").lower()  # "2x" (pixels carrés) | "stretch" (2x5)
        # G19_UI=native (défaut) : interface couleur 320x240 ; G19_UI=retro : rendu G15 160x43 agrandi
        self.native = os.environ.get("G19_UI", "native").lower() != "retro"
        self._bits = self._load_keymap()

        self._try_connect(initial=True)
        threading.Thread(target=self._reader_loop, daemon=True).start()

    # --- Touches ---------------------------------------------------------------
    def _load_keymap(self) -> dict[int, int]:
        names = {
            "HOME": g19_lcd.LKEY_HOME, "CANCEL": g19_lcd.LKEY_CANCEL, "MENU": g19_lcd.LKEY_MENU,
            "OK": g19_lcd.LKEY_OK, "RIGHT": g19_lcd.LKEY_RIGHT, "LEFT": g19_lcd.LKEY_LEFT,
            "DOWN": g19_lcd.LKEY_DOWN, "UP": g19_lcd.LKEY_UP,
        }
        mapping = dict(G19_DEFAULT_KEYMAP)
        f = BASE_DIR / "g19_keymap.json"
        if f.exists():
            try:
                mapping = {
                    k.upper(): (int(v) if str(v).lstrip("-").isdigit() else str(v))
                    for k, v in json.loads(f.read_text(encoding="utf-8")).items()
                }
            except Exception as e:
                print(f"[G19] g19_keymap.json illisible ({e}) -> mapping par défaut")
        return {names[k]: v for k, v in mapping.items() if k in names}

    def is_button_held(self, btn_id: int) -> bool:
        with self._state_lock:
            return btn_id in self.pressed_set

    def start_calibration(self):
        print("[G19] Pas de calibration nécessaire (adapter g19_keymap.json pour remapper les touches).")

    def render_calibration(self, draw):
        pass

    def _handle_report(self, pkt: bytes):
        bits = pkt[0] | (pkt[1] << 8 if len(pkt) > 1 else 0)
        now_pressed = {btn for mask, btn in self._bits.items() if bits & mask}
        with self._state_lock:
            for ev in sorted(e for e in now_pressed - self.pressed_set if isinstance(e, str)):
                self.event_queue.put(ev)  # actions directes (PREV_PAGE, TOGGLE_BLANK...)
            now_pressed = {b for b in now_pressed if isinstance(b, int)}
            newly_pressed = now_pressed - self.pressed_set
            newly_released = self.pressed_set - now_pressed
            for b in (BTN_1, BTN_2, BTN_3, BTN_4):
                if b in newly_pressed:
                    self.event_queue.put(b)
            if BTN_APPLET in newly_pressed:
                self.applet_press_time = time.monotonic()
                self.applet_long_handled = False
            if BTN_APPLET in newly_released and self.applet_press_time is not None:
                if not self.applet_long_handled:
                    self.event_queue.put("NEXT_PAGE")
                self.applet_press_time = None
            self.pressed_set = now_pressed

    # Touches G1..G12 -> saut direct à l'écran n ; M1 = précédent, M2 = suivant, MR = écran on/off
    def _handle_gkeys(self, mask: int):
        newly = mask & ~getattr(self, "_gm_prev", 0)
        self._gm_prev = mask
        for i in range(12):
            if newly & (1 << i):
                self.event_queue.put(f"GOTO:{i}")
        if newly & (1 << 12):
            self.event_queue.put("PREV_PAGE")
        if newly & (1 << 13):
            self.event_queue.put("NEXT_PAGE")
        if newly & (1 << 15):
            self.event_queue.put("TOGGLE_BLANK")

    def _reader_loop(self):
        import usb.core
        while self.running:
            if not self.connected:
                time.sleep(0.05)
                continue
            try:
                gm = self.dev.read_gkeys(20)
                if gm is not None:
                    self._handle_gkeys(gm)
                pkt = self.dev.read_lkeys(30)
            except usb.core.USBError as e:
                if self._debug:
                    print(f"[G19] erreur lecture touches : {e!r}")
                self._mark_disconnected()
                continue
            except Exception as e:
                if self._debug:
                    print(f"[G19] exception lecture touches : {e!r}")
                time.sleep(0.05)
                continue
            if pkt:
                if self._debug:
                    print(f"[G19 RX] {pkt.hex(' ')}")
                self._handle_report(pkt)
            with self._state_lock:
                if self.applet_press_time is not None and not self.applet_long_handled:
                    if time.monotonic() - self.applet_press_time > 0.9:
                        self.applet_long_handled = True
                        self.event_queue.put("TOGGLE_BLANK")

    # --- Connexion -------------------------------------------------------------
    def _try_connect(self, initial: bool = False) -> bool:
        import usb.core
        with self._io_lock:
            self.dev.close()
            try:
                self.dev.open()
                self.dev.set_backlight(*self.backlight)
                self.dev.set_brightness(int(os.environ.get("G19_BRIGHTNESS", "100")))
                self.dev.set_mkeys(m1=True)
                self.connected = True
                print("[G19] Logitech G19 connecté et opérationnel.")
                return True
            except (usb.core.USBError, OSError, ValueError) as e:
                if initial:
                    print(f"[G19] Aucun G19 utilisable ({e}) — attente de connexion USB (hot-plug 2s)...")
                self.connected = False
                self.dev.close()
                return False

    def _mark_disconnected(self):
        if self.connected:
            print("[G19] Déconnexion USB détectée — reconnexion auto toutes les 2s...")
        self.connected = False
        with self._io_lock:
            self.dev.close()
        with self._state_lock:
            self.pressed_set.clear()
            self.applet_press_time = None

    # --- Rendu -----------------------------------------------------------------
    def _encode_frame(self, img: Image.Image) -> bytes:
        W, H = g19_lcd.LCD_W, g19_lcd.LCD_H
        if img.mode == "RGB" and img.size == (W, H):
            return self._finish_frame(img)  # interface native couleur : aucun traitement 1-bit
        lit = img.convert("L").point(lambda v: 255 if v < 128 else 0)  # 255 = pixel allumé (noir sur G15)
        if self.layout == "stretch":
            lit = lit.resize((W, 5 * LCD_H), Image.NEAREST)
        else:
            lit = lit.resize((2 * LCD_W, 2 * LCD_H), Image.NEAREST)
        canvas_mask = Image.new("L", (W, H), 0)
        canvas_mask.paste(lit, ((W - lit.width) // 2, (H - lit.height) // 2))
        frame = Image.composite(
            Image.new("RGB", (W, H), self.fg), Image.new("RGB", (W, H), self.bg), canvas_mask
        )
        return self._finish_frame(frame)

    def _finish_frame(self, frame: Image.Image) -> bytes:
        dump = os.environ.get("G19_DUMP")  # debug : sauvegarde la dernière trame (PNG) sans matériel
        if dump and time.monotonic() - getattr(self, "_last_dump", 0) > 1.0:
            self._last_dump = time.monotonic()
            frame.save(dump)
        # La mémoire du G19 est en 240 colonnes x 320 lignes (écran monté en portrait) :
        # miroir horizontal + rotation (validé à l'écran sur un G19 réel)
        frame = frame.transpose(Image.FLIP_LEFT_RIGHT).rotate(int(os.environ.get("G19_ROT", "90")), expand=True)
        return g19_lcd.rgb_to_rgb565_le(frame.tobytes())

    def send_image(self, img: Image.Image):
        import usb.core
        if not self.connected:
            now = time.monotonic()
            if now - self._last_reconnect_t >= 2.0:
                self._last_reconnect_t = now
                self._try_connect(initial=False)
            return
        try:
            self.dev.send_frame(self._encode_frame(img))
        except (usb.core.USBError, OSError, ValueError) as e:
            if self._debug:
                print(f"[G19] erreur envoi trame : {e!r}")
            self._mark_disconnected()

    def poll_events(self) -> list:
        evs = []
        while not self.event_queue.empty():
            evs.append(self.event_queue.get_nowait())
        return evs

    def close(self):
        self.running = False
        time.sleep(0.06)
        if self.connected:
            try:
                if self.native:
                    self.send_image(Image.new("RGB", (320, 240), (0, 0, 0)))  # écran noir à l'arrêt
                else:
                    self.send_image(Image.new("1", (LCD_W, LCD_H), 1))
                self.dev.set_mkeys()
            except Exception:
                pass
        with self._io_lock:
            self.dev.close()


def create_hw(cfg_mgr: ConfigManager):
    """Choisit le backend : G15_BACKEND=g15|g19|auto (auto = G19 si branché, sinon G15)."""
    want = os.environ.get("G15_BACKEND", "auto").lower()
    if want == "g19" or (want == "auto" and HAS_G19 and (not HAS_HID or _g19_present())):
        if not HAS_G19:
            sys.exit("Backend G19 demandé mais pyusb/libusb est indisponible (pip install pyusb).")
        return G19Controller(cfg_mgr)
    if not HAS_HID:
        sys.exit("Aucun backend USB : installer 'hidapi' (G15) ou 'pyusb' (G19).")
    return G15HidController(cfg_mgr)


def _g19_present() -> bool:
    try:
        return g19_lcd.G19Device.present()
    except Exception:
        return False


# ==============================================================================
# APPLICATION PRINCIPALE (8 ÉCRANS, MASQUAGE AUTO 3s & SYSTEM TRAY)
# ==============================================================================
class G15App:
    def __init__(self):
        self.cfg_mgr = ConfigManager(CONFIG_FILE)
        self.hw = create_hw(self.cfg_mgr)
        self.telemetry = TelemetryHub(self.cfg_mgr)
        self.audio = AudioLoopbackWorker()

        self.native = bool(getattr(self.hw, "native", False))
        if self.native:
            import g19_hud
            self.g19_hud = g19_hud
            self.screens = g19_hud.make_screens(sys.modules[__name__])
        else:
            self.screens: list[tuple[str, BaseScreen]] = [
                ("HORLOGE", ClockScreen()),
                ("CHRONO", ChronoScreen()),
                ("MONITORING", MonitorScreen()),
                ("MÉDIA", MediaScreen()),
                ("RÉSEAU", NetworkScreen()),
                ("SERVEURS", ServersScreen()),
                ("MÉTÉO", WeatherScreen()),
                ("DEMOSCENE", DemosceneScreen()),
            ]
        self.page_idx = 0
        now = time.monotonic()
        self.banner_until = now + 1.2
        self.softkeys_awake_until = now + self.cfg_mgr.get().softkey_hide_sec
        self.toast_msg = ""
        self.toast_until = 0.0
        self.running = True

    def next_page(self):
        self.page_idx = (self.page_idx + 1) % len(self.screens)
        now = time.monotonic()
        self.banner_until = now + 0.85
        self.softkeys_awake_until = now + self.cfg_mgr.get().softkey_hide_sec
        self.screens[self.page_idx][1].on_enter()

    def reload_config(self):
        cfg = self.cfg_mgr.reload()
        self.telemetry.trigger_ping_and_wan_refresh()
        self.toast_msg = "CONFIG RECHARGÉE"
        self.toast_until = time.monotonic() + 1.5
        self.softkeys_awake_until = time.monotonic() + cfg.softkey_hide_sec

    def run_loop(self):
        while self.running:
            t0 = time.monotonic()
            cfg = self.cfg_mgr.get()
            frame_dt = 1.0 / max(5, cfg.fps)
            page_name, screen = self.screens[self.page_idx]

            for ev in self.hw.poll_events():
                now = time.monotonic()
                if ev == "TOGGLE_BLANK":
                    self.hw.screen_blanked = not self.hw.screen_blanked
                    if not self.hw.screen_blanked:
                        self.softkeys_awake_until = now + cfg.softkey_hide_sec
                elif self.hw.screen_blanked:
                    self.hw.screen_blanked = False
                    self.softkeys_awake_until = now + cfg.softkey_hide_sec
                elif ev == "NEXT_PAGE":
                    self.next_page()
                    page_name, screen = self.screens[self.page_idx]
                elif isinstance(ev, str) and ev.startswith("GOTO:"):
                    n = int(ev[5:])
                    if 0 <= n < len(self.screens) and n != self.page_idx:
                        self.page_idx = (n - 1) % len(self.screens)
                        self.next_page()
                        page_name, screen = self.screens[self.page_idx]
                elif ev == "PREV_PAGE":
                    self.page_idx = (self.page_idx - 2) % len(self.screens)
                    self.next_page()
                    page_name, screen = self.screens[self.page_idx]
                elif isinstance(ev, int):
                    was_immersive = screen.is_immersive()
                    softkeys_currently_shown = (not was_immersive) or (now < self.softkeys_awake_until)

                    if was_immersive and not softkeys_currently_shown:
                        if screen.bypass_wake_for_button(ev):
                            # Exception Pong : boutons 1 & 2 actifs immédiatement sans réafficher la barre
                            screen.on_button(ev)
                        else:
                            # Règle 3.2 : le 1er appui réveille uniquement la barre de légende pour 3s
                            self.softkeys_awake_until = now + cfg.softkey_hide_sec
                    else:
                        screen.on_button(ev)
                        if screen.is_immersive():
                            self.softkeys_awake_until = now + cfg.softkey_hide_sec

            if self.native:
                img = Image.new("RGB", (320, 240), (0, 0, 0))
                if not self.hw.screen_blanked:
                    draw = ImageDraw.Draw(img)
                    now = time.monotonic()
                    screen.render(draw, self.telemetry, self.audio, self.hw, cfg, True)
                    if now < self.banner_until:
                        self.g19_hud.toast(draw, f"{self.page_idx + 1}/{len(self.screens)}  {page_name}")
                    elif now < self.toast_until:
                        self.g19_hud.toast(draw, self.toast_msg)
                self.hw.send_image(img)
                elapsed = time.monotonic() - t0
                if elapsed < frame_dt:
                    time.sleep(frame_dt - elapsed)
                continue

            img = Image.new("1", (LCD_W, LCD_H), 1)
            if not self.hw.screen_blanked:
                draw = make_draw(img)
                if self.hw.calibrating:
                    self.hw.render_calibration(draw)
                else:
                    now = time.monotonic()
                    show_sk = (not screen.is_immersive()) or (now < self.softkeys_awake_until)
                    screen.render(draw, self.telemetry, self.audio, self.hw, cfg, show_sk)

                    if now < self.banner_until:
                        draw_toast_banner(
                            draw, f"{self.page_idx + 1}/{len(self.screens)} {page_name}", y_center=15
                        )
                    elif now < self.toast_until:
                        draw_toast_banner(draw, self.toast_msg, y_center=15)

            self.hw.send_image(img)

            elapsed = time.monotonic() - t0
            if elapsed < frame_dt:
                time.sleep(frame_dt - elapsed)

        self.audio.close()
        self.telemetry.close()
        self.hw.close()

    def stop(self, icon=None, item=None):
        self.running = False
        icon = icon or getattr(self, "tray_icon", None)  # arrêt par signal / minuteur : fermer aussi le tray
        if icon:
            icon.stop()


def create_tray_icon(app: G15App):
    ico = Image.new("RGB", (64, 64), (20, 20, 20))
    d = ImageDraw.Draw(ico)
    d.rectangle((4, 14, 60, 50), outline=(255, 140, 0), width=3)
    d.text((14, 25), "G15", fill=(255, 140, 0))
    menu = pystray.Menu(
        pystray.MenuItem("Page suivante", lambda icon, item: app.next_page()),
        pystray.MenuItem("Recharger config.toml", lambda icon, item: app.reload_config()),
        pystray.MenuItem("Recalibrer les boutons", lambda icon, item: app.hw.start_calibration()),
        pystray.MenuItem("Quitter G15 HUD", app.stop),
    )
    icon = pystray.Icon("g15_hud", ico, "G15-Retro-HUD v0.2.0", menu)
    app.tray_icon = icon
    return icon


if __name__ == "__main__":
    app = G15App()
    # Arrêt propre (systemd / Ctrl+C / Ctrl+Break) : on laisse finir la trame USB en cours.
    # Couper une trame G19 en plein envoi laisse l'écran figé jusqu'au rebranchement.
    import signal
    for _sig in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, _sig):
            try:
                signal.signal(getattr(signal, _sig), lambda *_: app.stop())
            except (ValueError, OSError):
                pass
    _max_s = float(os.environ.get("G19_MAX_SECONDS", "0") or 0)  # tests : arrêt automatique propre
    if _max_s > 0:
        threading.Timer(_max_s, app.stop).start()
    if HAS_TRAY:
        worker = threading.Thread(target=app.run_loop, daemon=True)
        worker.start()
        tray = create_tray_icon(app)

        def _watch_stop():  # signal / minuteur / « Quitter » : refermer l'icône quand la boucle doit s'arrêter
            while app.running:
                time.sleep(0.2)
            try:
                tray.stop()
            except Exception:
                pass

        threading.Thread(target=_watch_stop, daemon=True).start()
        try:
            tray.run()
        except KeyboardInterrupt:
            app.stop(tray)
        app.running = False
        worker.join(timeout=8)  # laisser finir la trame USB en cours + l'écran noir final (sinon écran figé)
    else:
        try:
            app.run_loop()
        except KeyboardInterrupt:
            app.stop()
