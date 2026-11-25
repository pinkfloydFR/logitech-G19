#!/usr/bin/env bash
# Installe le HUD du Logitech G19 comme service systemd (Debian / Ubuntu / Proxmox VE).
#
#   sudo ./linux/install.sh [--user NOM|root] [--prefix DIR] [--no-apt] [--no-start]
#
# Par défaut : utilisateur système « g19hud » (groupe plugdev), installation dans /opt/g19-hud.
set -euo pipefail

SERVICE_USER="g19hud"
PREFIX="/opt/g19-hud"
DO_APT=1
DO_START=1

while [ $# -gt 0 ]; do
  case "$1" in
    --user)     SERVICE_USER="${2:?--user attend un nom}"; shift 2 ;;
    --prefix)   PREFIX="${2:?--prefix attend un dossier}"; shift 2 ;;
    --no-apt)   DO_APT=0; shift ;;
    --no-start) DO_START=0; shift ;;
    -h|--help)  sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "Option inconnue : $1" >&2; exit 2 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  exec sudo "$0" "$@"
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"

for f in g19_dashboard.py g19_lcd.py g19_hud.py g19_proxmox.py g19_docker.py; do
  [ -f "$REPO/$f" ] || { echo "Fichier manquant : $REPO/$f" >&2; exit 1; }
done

if [ "$DO_APT" -eq 1 ]; then
  echo "==> Paquets"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends \
    python3 python3-usb python3-pil python3-psutil python3-numpy \
    libusb-1.0-0 fonts-dejavu-core iputils-ping lm-sensors ca-certificates
fi

echo "==> Utilisateur de service : $SERVICE_USER"
getent group plugdev >/dev/null || groupadd -r plugdev
if [ "$SERVICE_USER" != "root" ] && ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin -G plugdev "$SERVICE_USER"
fi

echo "==> Fichiers dans $PREFIX"
install -d -m 755 "$PREFIX"
for f in g19_dashboard.py g19_lcd.py g19_hud.py g19_proxmox.py g19_docker.py LICENSE; do
  [ -f "$REPO/$f" ] && install -m 644 "$REPO/$f" "$PREFIX/$f"
done
# config.toml / g19_keymap.json sont créés au premier lancement et conservés aux mises à jour
chown -R "$SERVICE_USER":"$(id -gn "$SERVICE_USER")" "$PREFIX"

echo "==> udev"
install -m 644 "$HERE/99-g19-hud.rules" /etc/udev/rules.d/99-g19-hud.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --attr-match=idVendor=046d --attr-match=idProduct=c229 || true

echo "==> Service systemd"
sed -e "s|@USER@|$SERVICE_USER|g" -e "s|@PREFIX@|$PREFIX|g" "$HERE/g19-hud.service" \
  > /etc/systemd/system/g19-hud.service
[ -f /etc/default/g19-hud ] || cat > /etc/default/g19-hud <<'EOF'
# Surcharges du HUD G19 (voir CLAUDE.md pour la liste des variables)
#G19_BRIGHTNESS=100
#G19_UI=native
#G19_THEME=amber
# Écran PROXMOX (VM/CT) : voir linux/proxmox-token.sh
#PVE_HOST=127.0.0.1
#PVE_TOKEN_ID=g19hud@pve!hud
#PVE_TOKEN_SECRET=
#PVE_FINGERPRINT=
# Écran DOCKER : conteneurs surveillés (vide = tous) ; DOCKER_HOST=tcp://127.0.0.1:2375 pour un proxy en lecture seule
#G19_DOCKER_CONTAINERS=nextcloud,jellyfin
EOF
chmod 600 /etc/default/g19-hud   # peut contenir le secret du jeton d'API
systemctl daemon-reload
if [ "$DO_START" -eq 1 ]; then
  systemctl enable g19-hud.service
  systemctl restart g19-hud.service
  sleep 2
  systemctl --no-pager --lines=8 status g19-hud.service || true
else
  systemctl enable g19-hud.service
  echo "Service activé mais non démarré (--no-start)."
fi

echo
echo "Terminé. Journal : journalctl -u g19-hud -f   |   Arrêt propre : systemctl stop g19-hud"
