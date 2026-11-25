"""Interface native couleur 320x240 du HUD pour le Logitech G19.

Les écrans réutilisent la logique (boutons, états) des écrans G15 d'origine et ne redessinent
que le rendu : chaque écran ci-dessous enveloppe l'objet « logic » du projet.
Usage : make_screens(module_g15_dashboard) -> liste [(nom, écran)].
"""
from __future__ import annotations

import socket
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import psutil
from PIL import Image, ImageDraw, ImageFont

W, H = 320, 240
HEADER_H = 26
FOOTER_Y = 218
BODY_TOP = 32
BODY_BOTTOM = 212

# Palette sombre
BG = (9, 12, 20)
PANEL = (22, 28, 44)
PANEL2 = (32, 40, 62)
TEXT = (232, 238, 248)
MUTED = (125, 138, 165)
GREEN = (70, 220, 120)
AMBER = (255, 190, 45)
RED = (255, 85, 75)
CYAN = (60, 200, 255)
MAGENTA = (255, 100, 200)
BLUE = (90, 140, 255)
VIOLET = (170, 130, 255)
TEAL = (40, 210, 190)
ORANGE = (255, 140, 30)

JOURS = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre")

_FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF",
    "/usr/share/fonts/truetype/liberation", "C:/Windows/Fonts",
]


@lru_cache(maxsize=None)
def F(size: int, bold: bool = False, mono: bool = False):
    name = ("DejaVuSansMono" if mono else "DejaVuSans") + ("-Bold" if bold else "") + ".ttf"
    win = {"DejaVuSans.ttf": "segoeui.ttf", "DejaVuSans-Bold.ttf": "segoeuib.ttf",
           "DejaVuSansMono.ttf": "consola.ttf", "DejaVuSansMono-Bold.ttf": "consolab.ttf"}[name]
    for d in _FONT_DIRS:
        for n in (name, win):
            p = Path(d) / n
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except OSError:
                    pass
    return ImageFont.load_default()


# --- Primitives ------------------------------------------------------------------
def mix(c1, c2, t: float):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def load_color(pct: float):
    if pct < 60:
        return GREEN
    if pct < 85:
        return AMBER
    return RED


def text(d, xy, s, font, fill=TEXT, anchor="la"):
    d.text(xy, s, font=font, fill=fill, anchor=anchor)


def twidth(d, s, font) -> int:
    return int(d.textlength(s, font=font))


def fit(d, s: str, font, max_px: int) -> str:
    if twidth(d, s, font) <= max_px:
        return s
    while s and twidth(d, s + "…", font) > max_px:
        s = s[:-1]
    return s + "…"


def card(d, box, fill=PANEL, r=8, outline=None):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline)


def bar(d, x, y, w, h, pct, color, bg=PANEL2, peak=None):
    pct = max(0.0, min(100.0, pct))
    d.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=bg)
    fw = int(w * pct / 100.0)
    if fw >= h:
        d.rounded_rectangle((x, y, x + fw, y + h), radius=h // 2, fill=color)
    elif fw > 0:
        d.rectangle((x, y, x + fw, y + h), fill=color)
    if peak is not None and peak > 0:
        px = x + int(w * min(100.0, peak) / 100.0)
        d.line((px, y - 2, px, y + h + 2), fill=TEXT, width=2)


def area(d, box, values, vmax, color, grid=True):
    """Courbe pleine (aire + trait) dans box=(x0,y0,x1,y1)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    d.rectangle(box, fill=PANEL)
    if grid:
        for i in range(1, 4):
            gy = y0 + h * i // 4
            d.line((x0, gy, x1, gy), fill=PANEL2)
    n = len(values)
    if n < 2 or vmax <= 0:
        return
    pts = [(x0 + int(i * w / (n - 1)), y1 - int(min(1.0, v / vmax) * (h - 1))) for i, v in enumerate(values)]
    d.polygon(pts + [(x1, y1), (x0, y1)], fill=mix(PANEL, color, 0.35))
    d.line(pts, fill=color, width=2)


def header(d, title, accent, idx, n, right=""):
    d.rectangle((0, 0, W, HEADER_H - 1), fill=PANEL)
    d.rectangle((0, HEADER_H - 2, W, HEADER_H - 1), fill=accent)
    d.rectangle((0, 0, 6, HEADER_H - 3), fill=accent)
    text(d, (14, 12), title, F(15, True), TEXT, "lm")
    x = W - 8
    if right:
        text(d, (x, 12), right, F(11), MUTED, "rm")
        x -= twidth(d, right, F(11)) + 12
    for i in range(n - 1, -1, -1):   # pastilles de page
        d.ellipse((x - 7, 8, x, 15), fill=accent if i == idx else PANEL2)
        x -= 11


ARROWS = ("▲", "▼", "◀", "▶")   # touches LCD du G19 : haut, bas, gauche, droite


def footer(d, labels, accent):
    d.rectangle((0, FOOTER_Y - 2, W, H), fill=PANEL)
    cw = W // 4
    for i, lab in enumerate(labels):
        x0 = i * cw
        if not lab:
            continue
        d.rounded_rectangle((x0 + 3, FOOTER_Y + 1, x0 + cw - 3, H - 2), radius=5, fill=PANEL2)
        text(d, (x0 + 9, FOOTER_Y + 10), ARROWS[i], F(10), accent, "lm")
        text(d, (x0 + 21, FOOTER_Y + 10), fit(d, lab, F(11, True), cw - 28), F(11, True), TEXT, "lm")


def toast(d, msg: str):
    f = F(20, True)
    tw = twidth(d, msg, f) + 36
    x0 = (W - tw) // 2
    d.rounded_rectangle((x0, 98, x0 + tw, 142), radius=12, fill=PANEL2, outline=CYAN, width=2)
    text(d, (W // 2, 120), msg, f, TEXT, "mm")


# --- Icônes météo vectorielles ----------------------------------------------------
def _cloud(d, cx, cy, s, color=(200, 210, 230)):
    d.ellipse((cx - s * 0.9, cy - s * 0.25, cx - s * 0.1, cy + s * 0.45), fill=color)
    d.ellipse((cx - s * 0.5, cy - s * 0.7, cx + s * 0.45, cy + s * 0.3), fill=color)
    d.ellipse((cx + s * 0.05, cy - s * 0.35, cx + s * 0.95, cy + s * 0.45), fill=color)
    d.rectangle((cx - s * 0.5, cy, cx + s * 0.5, cy + s * 0.45), fill=color)


def _sun(d, cx, cy, s, color=AMBER):
    import math
    r = s * 0.45
    for k in range(8):
        a = k * math.pi / 4
        d.line((cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35,
                cx + math.cos(a) * r * 1.95, cy + math.sin(a) * r * 1.95), fill=color, width=max(2, int(s / 12)))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)


def weather_icon(d, cx, cy, s, key):
    if key == "sun":
        _sun(d, cx, cy, s)
    elif key == "partly":
        _sun(d, cx - s * 0.35, cy - s * 0.3, s * 0.75)
        _cloud(d, cx + s * 0.15, cy + s * 0.2, s * 0.8)
    elif key == "cloud":
        _cloud(d, cx, cy, s, (170, 182, 205))
    elif key == "fog":
        _cloud(d, cx, cy - s * 0.25, s * 0.8, (150, 160, 180))
        for i in range(3):
            y = cy + s * 0.35 + i * s * 0.2
            d.line((cx - s * 0.8, y, cx + s * 0.8, y), fill=(150, 160, 180), width=max(2, int(s / 12)))
    else:
        _cloud(d, cx, cy - s * 0.3, s * 0.85, (140, 155, 185) if key != "snow" else (200, 215, 235))
        for i in range(4):
            x = cx - s * 0.6 + i * s * 0.4
            y = cy + s * 0.45
            if key == "snow":
                d.ellipse((x - 3, y, x + 3, y + 6), fill=(235, 245, 255))
            elif key == "storm" and i == 1:
                d.polygon([(x + 4, y - 4), (x - 4, y + 8), (x, y + 8), (x - 4, y + 18), (x + 8, y + 4), (x + 3, y + 4)], fill=AMBER)
            else:
                d.line((x, y, x - 4, y + 12), fill=CYAN, width=max(2, int(s / 14)))


# --- Mesures système (mises en cache) -----------------------------------------------
_cache: dict = {}


def cached(key, ttl, fn):
    now = time.monotonic()
    v = _cache.get(key)
    if v is None or now - v[0] > ttl:
        try:
            val = fn()
        except Exception:
            val = None
        _cache[key] = (now, val)
        return val
    return v[1]


def cpu_temp():
    def _t():
        temps = psutil.sensors_temperatures()
        for k in ("coretemp", "k10temp", "cpu_thermal", "zenpower", "acpitz"):
            if k in temps and temps[k]:
                return max(t.current for t in temps[k])
        return None
    return cached("temp", 3, _t) if hasattr(psutil, "sensors_temperatures") else None


def load_avg():
    return cached("load", 3, lambda: psutil.getloadavg())


def disks():
    def _d():
        out = []
        seen = set()
        for p in psutil.disk_partitions(all=False):
            if p.fstype in ("squashfs", "tmpfs", "overlay", "devtmpfs") or p.mountpoint.startswith(("/snap", "/boot/efi", "/run", "/mnt/wsl", "/usr/lib/wsl")):
                continue
            if p.device in seen:
                continue
            seen.add(p.device)
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            if u.total < 1e9:
                continue
            out.append((p.mountpoint, u.used / 1e9, u.total / 1e9, u.percent))
        return out[:5]
    return cached("disks", 10, _d) or []


# --- Écrans -------------------------------------------------------------------------
class NativeScreen:
    TITLE = ""
    ACCENT = CYAN

    def __init__(self, logic=None):
        self.logic = logic
        self.idx = 0
        self.n = 1

    def on_enter(self):
        if self.logic is not None:
            self.logic.on_enter()

    def on_button(self, btn):
        if self.logic is not None:
            self.logic.on_button(btn)

    def is_immersive(self):
        return False

    def bypass_wake_for_button(self, btn):
        return False

    def labels(self):
        return ["", "", "", ""]

    def right_text(self, tel):
        return ""

    def body(self, d, tel, cfg):
        raise NotImplementedError

    def render(self, d, tel, audio, hw, cfg, show_softkeys):
        d.rectangle((0, 0, W, H), fill=BG)
        self.body(d, tel, cfg)
        header(d, self.TITLE, self.ACCENT, self.idx, self.n, self.right_text(tel))
        footer(d, self.labels(), self.ACCENT)


class ClockNative(NativeScreen):
    TITLE, ACCENT = "HORLOGE", CYAN

    def right_text(self, tel):
        return socket.gethostname()

    def labels(self):
        return ["12/24", "SEC", "UPT/IP", ""]

    def body(self, d, tel, cfg):
        lg = self.logic
        now = datetime.now()
        fmt = "%H:%M" if lg.fmt_24h else "%I:%M"
        if lg.show_sec:
            fmt += ":%S"
        t = now.strftime(fmt)
        size = 62 if lg.show_sec else 84
        text(d, (W // 2, 78), t, F(size, True, True), TEXT, "mm")
        text(d, (W // 2, 126), f"{JOURS[now.weekday()]} {now.day} {MOIS[now.month - 1]} {now.year}", F(18), CYAN, "mm")
        cw = 100
        for i, (lab, val, pct) in enumerate((
            ("CPU", f"{tel.cpu_total:.0f}%", tel.cpu_total),
            ("RAM", f"{tel.ram_pct:.0f}%", tel.ram_pct),
        )):
            x0 = 10 + i * (cw + 8)
            card(d, (x0, 148, x0 + cw, 208))
            text(d, (x0 + 10, 160), lab, F(12, True), MUTED, "lm")
            text(d, (x0 + cw - 10, 160), val, F(18, True), load_color(pct), "rm")
            bar(d, x0 + 10, 182, cw - 20, 10, pct, load_color(pct))
        x0 = 10 + 2 * (cw + 8)
        card(d, (x0, 148, W - 10, 208))
        if lg.show_uptime:
            up = int(time.time() - psutil.boot_time())
            dd, rem = divmod(up, 86400)
            hh, rem = divmod(rem, 3600)
            text(d, (x0 + 10, 160), "UPTIME", F(12, True), MUTED, "lm")
            text(d, (x0 + 10, 190), f"{dd}j {hh}h{rem // 60:02d}" if dd else f"{hh}h{rem // 60:02d}m", F(18, True), TEXT, "lm")
        else:
            text(d, (x0 + 10, 160), "IP", F(12, True), MUTED, "lm")
            text(d, (x0 + 10, 190), tel.local_ip, F(11, True), TEXT, "lm")


class ChronoNative(NativeScreen):
    TITLE, ACCENT = "CHRONO", VIOLET

    def labels(self):
        lg = self.logic
        return ["PAUSE" if lg.running else "START", "DURÉE" if lg.is_timer else "TOUR", "RESET", "MODE"]

    def right_text(self, tel):
        return "MINUTEUR" if self.logic.is_timer else "CHRONOMÈTRE"

    def body(self, d, tel, cfg):
        lg = self.logic
        lg._tick(time.monotonic())
        val = lg.remaining if lg.is_timer else lg.elapsed
        m, s = divmod(val, 60)
        h, m = divmod(int(m), 60)
        alert = lg.is_timer and lg.remaining == 0.0 and int(time.time() * 4) % 2 == 0
        if alert:
            d.rectangle((0, HEADER_H, W, FOOTER_Y - 3), fill=RED)
        t = f"{h}:{m:02d}:{int(s):02d}" if h > 0 else f"{m:02d}:{s:04.1f}"
        text(d, (W // 2, 78), t, F(66, True, True), TEXT if not alert else BG, "mm")
        if lg.is_timer:
            preset = lg.presets[lg.timer_preset_idx]
            bar(d, 20, 124, W - 40, 12, 100.0 * lg.remaining / max(1, preset), VIOLET)
            text(d, (W // 2, 152), f"Cible : {preset // 60} min", F(16), MUTED, "mm")
        else:
            card(d, (10, 128, W - 10, 208))
            if not lg.laps:
                text(d, (W // 2, 168), "Aucun tour", F(14), MUTED, "mm")
            for i, lap in enumerate(list(lg.laps)[:4]):
                text(d, (24, 142 + i * 19), lap, F(14, False, True), TEXT if i == 0 else MUTED, "lm")


class MonitorNative(NativeScreen):
    TITLE, ACCENT = "MONITORING", GREEN

    def labels(self):
        lg = self.logic
        return ["VUE", "CORES", "BAT" if lg.show_bat else "—", "PICS"]

    def right_text(self, tel):
        t = cpu_temp()
        la = load_avg()
        s = f"{t:.0f}°C" if t is not None else ""
        if la:
            s += f"  load {la[0]:.2f}"
        return s.strip()

    def _gauge(self, d, y, label, pct, detail, color, peak=None):
        card(d, (10, y, W - 10, y + 36))
        text(d, (20, y + 12), label, F(13, True), MUTED, "lm")
        text(d, (W - 20, y + 12), f"{pct:.0f}%", F(18, True), color, "rm")
        text(d, (W - 78, y + 12), detail, F(11), MUTED, "rm")
        bar(d, 20, y + 23, W - 40, 8, pct, color, peak=peak)

    def body(self, d, tel, cfg):
        lg = self.logic
        if not tel.has_battery:
            lg.show_bat = False
        peaks = lg.show_peaks
        if lg.graph_mode:
            text(d, (14, 46), "CPU", F(13, True), MUTED, "lm")
            text(d, (W - 14, 44), f"{tel.cpu_total:.0f}%", F(26, True), load_color(tel.cpu_total), "rm")
            area(d, (10, 64, W - 10, 206), list(tel.cpu_hist), 100.0, load_color(tel.cpu_total))
            if peaks:
                text(d, (16, 70), f"pic {tel.cpu_peak:.0f}%", F(11), TEXT, "lm")
            return
        if lg.per_core:
            cores = tel.cpu_cores or [0.0]
            n = len(cores)
            card(d, (10, 36, W - 10, 208))
            gap = 2 if n > 32 else 4
            cols = n
            bw = max(2, (W - 40 - gap * (cols - 1)) // cols)
            x = 20
            for i, v in enumerate(cores):
                hgt = int(150 * v / 100.0)
                d.rectangle((x, 192 - 150, x + bw, 192), fill=PANEL2)
                if hgt > 0:
                    d.rectangle((x, 192 - hgt, x + bw, 192), fill=load_color(v))
                x += bw + gap
            text(d, (16, 200), f"{n} threads", F(11), MUTED, "lm")
            text(d, (W - 16, 200), f"moy {tel.cpu_total:.0f}%", F(11), TEXT, "rm")
            return
        temp = cpu_temp()
        self._gauge(d, 34, "CPU", tel.cpu_total, f"{temp:.0f}°C" if temp is not None else f"{len(tel.cpu_cores)} thr",
                    load_color(tel.cpu_total), tel.cpu_peak if peaks else None)
        self._gauge(d, 74, "RAM", tel.ram_pct, f"{tel.ram_used_gb:.1f} Go", load_color(tel.ram_pct))
        if lg.show_bat and tel.has_battery and tel.bat_pct is not None:
            self._gauge(d, 114, "BATTERIE", tel.bat_pct, "secteur" if tel.bat_plugged else "batterie",
                        GREEN if tel.bat_pct > 30 else RED)
        elif tel.nvml.available:
            self._gauge(d, 114, "GPU", tel.gpu_util, f"{tel.gpu_vram_mb / 1024:.1f} Go"
                        + (f" {tel.gpu_temp_c}°C" if tel.gpu_temp_c is not None else ""), load_color(tel.gpu_util),
                        tel.gpu_peak_util if peaks else None)
        else:
            ds = disks()
            if ds:
                self._gauge(d, 114, f"DISQUE {ds[0][0]}", ds[0][3], f"{ds[0][1]:.0f}/{ds[0][2]:.0f} Go", load_color(ds[0][3]))
        area(d, (10, 156, W - 10, 208), list(tel.cpu_hist), 100.0, load_color(tel.cpu_total))


class NetworkNative(NativeScreen):
    TITLE, ACCENT = "RÉSEAU", CYAN

    def right_text(self, tel):
        gw = next(iter(tel.pings.values()), None)
        return f"{tel.phys_nic_name or '—'}  {gw}ms" if gw is not None else (tel.phys_nic_name or "—")

    def labels(self):
        lg = self.logic
        return ["LIVE" if lg.frozen else "PAUSE", "Mb/Ko", "RAZ", ""]

    def body(self, d, tel, cfg):
        lg = self.logic
        if getattr(lg, "_want_reset", False):
            tel.reset_net_totals()
            lg._want_reset = False
        if not lg.frozen:
            lg.snap_dl = list(tel.dl_hist)
            lg.snap_ul = list(tel.ul_hist)
        card(d, (10, 34, 156, 84))
        text(d, (20, 46), "▼ RÉCEPTION", F(11, True), CYAN, "lm")
        text(d, (20, 68), lg._fmt_rate(tel.dl_speed) + "/s", F(20, True), TEXT, "lm")
        card(d, (164, 34, W - 10, 84))
        text(d, (174, 46), "▲ ENVOI", F(11, True), MAGENTA, "lm")
        text(d, (174, 68), lg._fmt_rate(tel.ul_speed) + "/s", F(20, True), TEXT, "lm")
        peak = max(10240.0, max(lg.snap_dl, default=1.0), max(lg.snap_ul, default=1.0))
        area(d, (10, 92, W - 10, 148), lg.snap_dl, peak, CYAN)
        area(d, (10, 152, W - 10, 208), lg.snap_ul, peak, MAGENTA)
        text(d, (16, 98), "pic " + lg._fmt_rate(peak), F(10), MUTED, "lm")
        text(d, (W - 16, 198), f"Total ▼ {tel.dl_total_gb:.2f} Go   ▲ {tel.ul_total_bytes / 1024 ** 3:.2f} Go", F(10), TEXT, "rm")


class ServersNative(NativeScreen):
    TITLE, ACCENT = "SERVEURS", ORANGE

    def right_text(self, tel):
        return "VPN actif" if tel.vpn_active else ""

    def labels(self):
        return ["SERV" if self.logic.show_ip_loc else "IP", "", "", "PING!"]

    def body(self, d, tel, cfg):
        lg = self.logic
        if getattr(lg, "_want_ping", False):
            tel.trigger_ping_and_wan_refresh()
            lg._want_ping = False
        if lg.show_ip_loc:
            rows = (("CARTE", tel.phys_nic_name or "Aucune", TEXT), ("IP LOCALE", tel.local_ip, GREEN),
                    ("IP PUBLIQUE", tel.public_ip, CYAN), ("VPN", "ACTIF" if tel.vpn_active else "non", AMBER if tel.vpn_active else MUTED))
            for i, (k, v, c) in enumerate(rows):
                y = 36 + i * 44
                card(d, (10, y, W - 10, y + 38))
                text(d, (22, y + 19), k, F(12, True), MUTED, "lm")
                text(d, (W - 22, y + 19), fit(d, v, F(18, True), 190), F(18, True), c, "rm")
            return
        for i, (name, addr) in enumerate(cfg.hosts[:4]):
            y = 36 + i * 44
            ms = tel.pings.get(name)
            card(d, (10, y, W - 10, y + 38))
            col = MUTED if ms is None and not tel.pings else (GREEN if ms is not None and ms < 60 else AMBER if ms is not None and ms < 150 else RED)
            d.ellipse((20, y + 13, 32, y + 25), fill=col)
            text(d, (42, y + 12), name, F(14, True), TEXT, "lm")
            text(d, (42, y + 28), addr, F(10), MUTED, "lm")
            text(d, (W - 22, y + 14), f"{ms} ms" if ms is not None else "HORS LIGNE", F(16, True), col, "rm")
            if ms is not None:
                bar(d, 190, y + 27, 100, 5, min(100.0, ms / 2.0), col)


class WeatherNative(NativeScreen):
    TITLE, ACCENT = "MÉTÉO", AMBER

    def right_text(self, tel):
        return tel.weather_city or ""

    def labels(self):
        lg = self.logic
        return ["", "", "JOURS", "MAJ"] if lg.mode_24h else ["JOUR-", "JOUR+", "24H", "MAJ"]

    def body(self, d, tel, cfg):
        g = self.g
        lg = self.logic
        if getattr(lg, "_want_refresh", False):
            tel.trigger_weather_refresh()
            lg._want_refresh = False
        if not tel.weather_online or not tel.weather_days:
            text(d, (W // 2, 120), "Météo en attente…", F(18), MUTED, "mm")
            return
        if lg.mode_24h and len(tel.weather_hourly_temp) >= 2:
            temps = tel.weather_hourly_temp[:24]
            rains = tel.weather_hourly_rain[:24]
            lo, hi = min(temps), max(temps)
            span = max(1.0, hi - lo)
            text(d, (14, 46), f"Prochaines 24 h   {lo:.0f}° → {hi:.0f}°C", F(14, True), TEXT, "lm")
            x0, y0, x1, y1 = 10, 62, W - 10, 190
            d.rectangle((x0, y0, x1, y1), fill=PANEL)
            n = len(temps)
            for i in range(n):
                x = x0 + 8 + int(i * (x1 - x0 - 16) / max(1, n - 1))
                if i < len(rains) and rains[i] >= 10:
                    rh = int(rains[i] / 100.0 * 40)
                    d.rectangle((x - 3, y1 - rh, x + 3, y1), fill=mix(PANEL, BLUE, 0.8))
            pts = [(x0 + 8 + int(i * (x1 - x0 - 16) / max(1, n - 1)), y1 - 12 - int((t - lo) / span * (y1 - y0 - 40))) for i, t in enumerate(temps)]
            d.line(pts, fill=AMBER, width=3)
            text(d, (x0 + 6, y1 + 12), "maintenant", F(10), MUTED, "lm")
            text(d, (x1 - 6, y1 + 12), "+24 h", F(10), MUTED, "rm")
            text(d, (W // 2, y1 + 12), "barres = pluie %", F(10), BLUE, "mm")
            return
        idx = min(lg.day_idx, len(tel.weather_days) - 1)
        day = tel.weather_days[idx]
        code = tel.weather_code_now if idx == 0 else day.wmo_code
        key, desc = g.wmo_info(code)
        card(d, (10, 34, W - 10, 146))
        weather_icon(d, 62, 90, 52, key)
        if idx == 0 and tel.weather_temp_now is not None:
            text(d, (118, 58), f"{tel.weather_temp_now:.1f}°C", F(40, True), TEXT, "lm")
            sub = f"min {day.t_min:.0f}°  max {day.t_max:.0f}°"
        else:
            text(d, (118, 58), f"{day.t_max:.0f}°C", F(40, True), TEXT, "lm")
            sub = f"min {day.t_min:.0f}°  max {day.t_max:.0f}°"
        text(d, (118, 88), desc, F(16, True), AMBER, "lm")
        text(d, (118, 108), sub, F(12), MUTED, "lm")
        wind = tel.weather_wind_now if idx == 0 else day.wind_kmh
        text(d, (W - 20, 46), f"{day.date_label}", F(13, True), TEXT, "rm")
        text(d, (118, 128), f"vent {wind:.0f} km/h · pluie {day.rain_prob}%", F(11), MUTED, "lm")
        days = tel.weather_days[:6]
        cw = (W - 20) // max(1, len(days))
        for i, dd in enumerate(days):
            x0 = 10 + i * cw
            sel = i == idx
            card(d, (x0 + 2, 150, x0 + cw - 2, 210), fill=PANEL2 if sel else PANEL, r=6, outline=AMBER if sel else None)
            text(d, (x0 + cw // 2, 160), dd.date_label[:3], F(11, True), TEXT, "mm")
            k2, _ = g.wmo_info(dd.wmo_code)
            weather_icon(d, x0 + cw // 2, 180, 17, k2)
            text(d, (x0 + cw // 2, 202), f"{dd.t_min:.0f}/{dd.t_max:.0f}", F(10), MUTED, "mm")


class StorageNative(NativeScreen):
    TITLE, ACCENT = "SYSTÈME", TEAL

    def right_text(self, tel):
        up = int(time.time() - psutil.boot_time())
        dd, rem = divmod(up, 86400)
        return f"up {dd}j {rem // 3600}h"

    def body(self, d, tel, cfg):
        ds = disks()
        if not ds:
            text(d, (W // 2, 110), "Aucun volume", F(16), MUTED, "mm")
        for i, (mnt, used, total, pct) in enumerate(ds):
            y = 34 + i * 31
            card(d, (10, y, W - 10, y + 27), r=6)
            text(d, (18, y + 9), fit(d, mnt, F(11, True), 120), F(11, True), TEXT, "lm")
            text(d, (W - 18, y + 9), f"{used:.0f}/{total:.0f} Go", F(11), MUTED, "rm")
            bar(d, 18, y + 18, W - 36, 5, pct, load_color(pct))
        y = 34 + max(len(ds), 1) * 31 + 2
        la = load_avg() or (0, 0, 0)
        t = cpu_temp()
        sw = psutil.swap_memory()
        items = (("LOAD", f"{la[0]:.2f}", GREEN), ("TEMP", f"{t:.0f}°C" if t is not None else "—", AMBER),
                 ("SWAP", f"{sw.percent:.0f}%", load_color(sw.percent)))
        cw = (W - 20) // 3
        for i, (k, v, c) in enumerate(items):
            x0 = 10 + i * cw
            card(d, (x0 + 2, y, x0 + cw - 2, min(y + 52, 210)))
            text(d, (x0 + cw // 2, y + 14), k, F(10, True), MUTED, "mm")
            text(d, (x0 + cw // 2, y + 34), v, F(18, True), c, "mm")


def make_screens(g):
    """g = module g15_dashboard (fournit ClockScreen, ChronoScreen, ... et wmo_info)."""
    defs = [
        ("HORLOGE", ClockNative(g.ClockScreen())),
        ("CHRONO", ChronoNative(g.ChronoScreen())),
        ("MONITORING", MonitorNative(g.MonitorScreen())),
        ("RÉSEAU", NetworkNative(g.NetworkScreen())),
        ("SERVEURS", ServersNative(g.ServersScreen())),
        ("MÉTÉO", WeatherNative(g.WeatherScreen())),
        ("SYSTÈME", StorageNative()),
    ]
    for i, (_, s) in enumerate(defs):
        s.idx, s.n, s.g = i, len(defs), g
    return defs
