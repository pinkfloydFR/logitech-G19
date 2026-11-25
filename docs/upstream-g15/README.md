# Logitech G19 HUD (fork de G15-Retro-HUD)

Tableau de bord pour l'écran couleur **320x240** et les touches du **Logitech G19**, sans pilote Logitech : USB direct (pyusb/libusb).
Fork de [DPKoss/G15-Retro-HUD](https://github.com/DPKoss/G15-Retro-HUD) (MIT) — le cœur et les écrans viennent du projet d'origine, ce fork ajoute le support G19.

- **Linux / Proxmox : prêt** (testé sur un G19 réel via WSL2 + systemd) → `sudo ./linux/install.sh`, voir [`linux/`](linux/) et [`CLAUDE.md`](CLAUDE.md).
- **Windows : en préparation** (pilote WinUSB, tray, `.exe`) → voir [`windows/`](windows/).
- Interface couleur native (7 écrans), touches LCD, G1–G12, M1–M3/MR, luminosité : détails du protocole dans [`CLAUDE.md`](CLAUDE.md).

Le reste de ce README est celui du projet G15 d'origine (Windows, LCD 160x43).

---

# G15-Retro-HUD

> **English** | **[Documentation en français](README.fr.md)**

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Windows 10 / 11 x64](https://img.shields.io/badge/OS-Windows%2010%20%2F%2011%20x64-0078D6?logo=windows&logoColor=white)
![USB Raw HID](https://img.shields.io/badge/USB-Raw%20HID%20(Zero%20Driver)-FF8C00)
![License MIT](https://img.shields.io/badge/License-MIT-2ea44f)

**Standalone user-space dashboard for the monochrome LCD (`160x43`) and 5 buttons on Logitech G15 keyboards (v1 & v2) for Windows 10/11, without any proprietary drivers.**

> **Note (`v0.2.0`):** This README is in English, while the on-screen LCD labels, System Tray menu, and `config.toml` comments are still in French for now (**English UI planned for `v0.3`** - button labels are translated in the table below). **Looking for testers with a blue G15 v1 (`046d:c222`)!**

[![Animated preview of G15-Retro-HUD (click for the full demo on YouTube)](assets/demo.gif)](https://youtu.be/d3aF21h4JUE)

> **[Watch the full 8-screen demo on YouTube (2:33)](https://youtu.be/d3aF21h4JUE)** *(click the GIF above or the link)*

---

## About

I pulled my ~20-year-old **G15** out of a closet and wanted to get its LCD working again on Windows 11 without installing old kernel drivers that force you to turn off Memory Integrity (HVCI) - especially now that Logitech has removed the old *Logitech Gaming Software (LGS)* installers from its website. The project started out vibe-coded for my own desk setup over several iterations, before being cleaned up and published as **v0.2.0** in a single script (`g15_dashboard.py`).

How it works in short:
* **Direct USB Raw HID:** supports **G15 v2** (`046d:c227`, amber) and **G15 v1** (`046d:c222`, blue), with auto-reconnect every 2 s (wake from sleep or USB dock/switch).
* **Native Windows probes:** calls `nvml.dll` (NVIDIA load, VRAM, temp), `iphlpapi.dll` (routing and VPN check), and `user32.dll` through `ctypes` - no `pynvml` or `pywin32` needed, with a WMI fallback for non-NVIDIA GPUs.
* **Clean 1-bit rendering:** Pillow with antialiasing disabled (`fontmode = "1"`) in `Tahoma` 9 px so text and accents stay readable pixel by pixel.
* **Pick-and-choose dependencies:** only `hidapi`, `psutil`, and `pillow` are required; audio capture, Windows media info, and the System Tray icon are optional.

---

## Requirements & Setup

### Option A - Standalone Windows `.exe` (no Python needed)

Download **`G15-Retro-HUD-v0.2.0-win64.exe`** from [**Releases**](https://github.com/DPKoss/G15-Retro-HUD/releases) and run it directly (single file, no installer, sits in the Windows System Tray).

> **Reproducibility:** built from [`G15-Retro-HUD.spec`](G15-Retro-HUD.spec) via [GitHub Actions](.github/workflows/release.yml) on each `v*` tag, or locally with `.\build.ps1`. Like any unsigned PyInstaller `--onefile` binary, it unpacks to `%TEMP%` at startup: if SmartScreen or your antivirus complains, just run it from source with Option B below.

### Option B - From source (Python 3.11+)

* **Windows 10 / 11 (x64)** and **Python 3.11+** (uses built-in `tomllib` and `winrt-*` wheels).

> [!WARNING]
> **Watch out on PyPI (`hidapi` != `hid`):** install **`hidapi`**, not the `hid` package (different API, won't work with `hid.device()`).

```bash
git clone https://github.com/DPKoss/G15-Retro-HUD.git
cd G15-Retro-HUD

# Full setup (recommended)
pip install -r requirements.txt

# Or minimal setup (no audio spectrum, Windows media info, or System Tray)
pip install hidapi psutil pillow
```

| `pip` package | Purpose | Status |
| :--- | :--- | :--- |
| `hidapi` | USB Raw HID communication (`import hid`) | **Required** |
| `psutil` | CPU, RAM, battery, and network stats | **Required** |
| `pillow` | `160x43` 1-bit rendering & Windows fonts | **Required** |
| `pystray` | System Tray icon and right-click menu | Optional *(console fallback)* |
| `pyaudiowpatch` | WASAPI Loopback audio capture | Optional *(no spectrum)* |
| `numpy` | 20-band FFT, fast HID packing, Demoscene | Optional *(Python fallback)* |
| `winrt-runtime`, `winrt-Windows.Foundation`, `winrt-Windows.Media.Control` | Windows GSMTC media session (title, artist, seek) | Optional *(Media screen)* |

---

## Running & Configuration

```bash
python g15_dashboard.py
# Or without a console window (runs in the System Tray):
pythonw g15_dashboard.py
```

Two local files (ignored by Git) are created on first run:
* **`config.toml`**: FPS, Wi-Fi/Ethernet priority, 3 ping hosts, fallback weather city when on VPN, and fullscreen hide delays. Can be reloaded live via right-click on the System Tray icon -> **Recharger config.toml** *(Reload config.toml)*.
* **`g15_keymap.json`**: 5-button layout (preconfigured for the **G15 v2**). On a **G15 v1** (`046d:c222`), right-click the System Tray icon -> **Recalibrer les boutons** *(Recalibrate buttons)* and follow the LCD prompts.

---

## The 8 Screens & Buttons

* **Round button (Applet):** short press = next screen (`1/8` -> `8/8`) | long press (> 0.9 s) = LCD sleep / wake.

| # | Screen | Content | Buttons `[1]` `[2]` `[3]` `[4]` *(FR -> EN)* |
| :-: | :--- | :--- | :--- |
| **1** | **Clock** | Big clock, date, CPU or battery, uptime | `12/24` . `SEC` . `UPT/DATE` . `-` |
| **2** | **Stopwatch / Timer** | 1/10s stopwatch with laps, or timer with flash alert | `START/PAUSE` . `TOUR/DURÉE` *(Lap/Time)* . `RESET` . `MODE` |
| **3** | **Monitoring** | CPU / RAM / GPU (or Battery), history, up to 64 threads, NVIDIA temp | `VUE` *(View)* . `CORES` . `BAT/GPU` . `PICS` *(Peaks)* |
| **4** | **Media & Visu** | Title/Artist/App (Spotify, browser, VLC...), seek, spectrum & **4 fullscreen visualizers** (Winamp, Oscillo, Bass Tunnel, L/R VU-meter) | `-15s` . `+30s` . `SPECTR` . `VISU` |
| **5** | **Network** | Down/Up graph on active physical NIC (no VPN/WSL double count) + total GB | `PAUSE` . `UNITÉ` *(Unit)* . `RAZ` *(Reset)* . `Total Go` *(GB)* |
| **6** | **Servers & VPN** | ICMP pings (3 hosts), local IP, public IP, and auto `[VPN]` tag | `IP LOC / SERV` . `-` . `-` . `PING!` |
| **7** | **Weather** | Geo-IP off-VPN (fallback city on VPN) via Open-Meteo, 16x16 icons, 6-day & 24h curve | `< JOUR` *(Day)* . `JOUR >` . `24H / JOURS` . `MAJ` *(Update)* |
| **8** | **Demoscene & Pong** | **6 `160x43` 1-bit demos** (Starfield, Matrix, Plasma, Doom Fire, 3D Torus, Rotozoomer) + **playable Pong** (20 FPS) | `< EFF` . `EFF >` . `AUTO` . `PONG` *(hold `HAUT`/`BAS` = Up/Down)* |

*(In **Visu** and **Demoscene** modes, the bottom button bar hides after 3 s to use the full `160x43` screen.)*

---

## Network & Privacy

No telemetry, no accounts, and nothing is sent to the author. The script only makes three background network requests:

* **ICMP Pings** every `10 s` (`ping_interval_sec`) to the 3 hosts in `config.toml` (`192.168.1.1`, `1.1.1.1`, `8.8.8.8` by default).
* **`https://ipwho.is/`** (fallback `https://api.ipify.org`) every `10 min` (`refresh_minutes`, plus on VPN changes or manual `PING!` / `MAJ`), with `User-Agent: G15-HUD/0.2.0`: shares your **public IP** with that service to show your WAN IP (screen 6) and get your city when off-VPN (screen 7). *Note: this call still runs on VPN or with `auto_detect_ip = false` so screen 6 keeps your public IP up to date, while weather switches to the fallback city from `config.toml`.*
* **`https://api.open-meteo.com/v1/forecast`** every `10 min` with latitude/longitude only (from Geo-IP off-VPN, or `fallback_lat`/`fallback_lon` on VPN or if `auto_detect_ip = false`).

---

## Roadmap & Contributing

Issues (especially feedback on the **blue G15 v1**), ideas, and PRs are welcome:

- [ ] **English (EN) translation** for the UI, System Tray menu, and `config.toml`.
- [ ] **G1-G6 macro keys, M1-M3/MR banks & LEDs** via HID Feature Reports.
- [ ] **Live WASAPI audio switching** when the default Windows output changes.
- [ ] **Global mic mute indicator / toggle** (`IAudioEndpointVolume`) and cross-screen flash alerts.
- [ ] **Modular split / Rust port** (standalone native binary).

Contact: [dpkoss@proton.me](mailto:dpkoss@proton.me)

---

## License & Disclaimer

Released under the **MIT** license ([LICENSE](LICENSE)).

Independent project, not affiliated with or endorsed by **Logitech** or **Microsoft**; no proprietary code or drivers included. Trademarks belong to their respective owners and are only used to identify compatible hardware.