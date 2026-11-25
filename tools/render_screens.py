"""Rend les 7 écrans de l'interface native en PNG (320x240) SANS G19 branché, avec des données de DÉMO
(aucune donnée de la machine : nom d'hôte, IP, ville, disques sont fictifs).

    python tools/render_screens.py [dossier_de_sortie]
"""
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image, ImageDraw

import g19_dashboard as g
import g19_hud

out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots")
out.mkdir(parents=True, exist_ok=True)

cfg_mgr = g.ConfigManager(Path(g.__file__).parent / "config.toml")
tel = g.TelemetryHub(cfg_mgr)
tel.running = False  # coupe les threads de collecte : on injecte des valeurs fictives
time.sleep(1.5)
screens = g19_hud.make_screens(g)

# --- données de démo ---
g19_hud.socket.gethostname = lambda: "proxmox"
tel.cpu_total, tel.cpu_peak, tel.cpu_cores = 34.0, 71.0, [20, 35, 60, 12, 80, 44, 9, 30, 55, 25, 18, 70, 33, 41, 22, 15]
tel.cpu_hist.extend([30 + 25 * math.sin(i / 9) + 10 * math.sin(i / 3) for i in range(158)])
tel.ram_pct, tel.ram_used_gb, tel.has_battery = 41.0, 13.1, False
tel.gpu_util, tel.gpu_vram_mb, tel.gpu_temp_c, tel.gpu_peak_util = 27, 3100, 54, 60
tel.phys_nic_name, tel.phys_nic_tag, tel.local_ip, tel.public_ip, tel.vpn_active = "enp3s0", "Eth", "192.168.1.20", "203.0.113.7", False
tel.dl_speed, tel.ul_speed = 3.4e6, 0.4e6
tel.dl_hist.extend([max(0, 2e6 + 1.6e6 * math.sin(i / 7) + 8e5 * math.sin(i / 2)) for i in range(160)])
tel.ul_hist.extend([max(0, 3e5 + 2e5 * math.sin(i / 11)) for i in range(160)])
tel.dl_total_bytes, tel.ul_total_bytes = int(14.2 * 1024 ** 3), int(2.1 * 1024 ** 3)
for (name, _), ms in zip(cfg_mgr.get().hosts, (1, 9, 12)):
    tel.pings[name] = ms
tel.weather_online, tel.weather_city, tel.weather_temp_now, tel.weather_code_now, tel.weather_wind_now = True, "Lausanne", 12.4, 61, 11.0
labels = ("Auj.", "Dem.", "Ven.", "Sam.", "Dim.", "Lun.")
codes = (61, 3, 0, 2, 63, 45)
tel.weather_days = [g.DailyForecast(l, c, 6 + i, 13 + i % 3, (80, 30, 5, 10, 90, 20)[i], 10 + 3 * i) for i, (l, c) in enumerate(zip(labels, codes))]
tel.weather_hourly_temp = [8 + 6 * math.sin((h - 8) / 24 * 2 * math.pi) for h in range(24)]
tel.weather_hourly_rain = [90, 80, 60, 40, 10, 0, 0, 0, 5, 10, 20, 30, 40, 60, 70, 50, 30, 10, 0, 0, 0, 10, 30, 50]
far = time.monotonic() + 1e9
g19_hud._cache["disks"] = (far, [("/", 212.0, 480.0, 44.0), ("/mnt/data", 3100.0, 8000.0, 39.0), ("/var/lib/vz", 640.0, 1000.0, 64.0)])
g19_hud._cache["temp"] = (far, 58.0)
g19_hud._cache["load"] = (far, (0.82, 0.74, 0.61))

for i, (name, scr) in enumerate(screens):
    img = Image.new("RGB", (320, 240))
    scr.render(ImageDraw.Draw(img), tel, None, None, cfg_mgr.get(), True)
    img.save(out / f"{i + 1}-{name.lower().replace('é', 'e').replace('è', 'e')}.png")
    print("ok", name)

sheet = Image.new("RGB", (4 * 330, 2 * 250), (40, 40, 40))
for i in range(len(screens)):
    f = sorted(out.glob(f"{i + 1}-*.png"))[0]
    sheet.paste(Image.open(f), ((i % 4) * 330 + 5, (i // 4) * 250 + 5))
sheet.save(out / "overview.png")
