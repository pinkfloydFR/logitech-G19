#!/usr/bin/env bash
# Désinstalle le service G19 HUD (conserve /opt/g19-hud/config.toml sauf --purge).
set -euo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
PREFIX="${PREFIX:-/opt/g19-hud}"
systemctl disable --now g19-hud.service 2>/dev/null || true
rm -f /etc/systemd/system/g19-hud.service /etc/udev/rules.d/99-g19-hud.rules
systemctl daemon-reload
udevadm control --reload-rules
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$PREFIX" /etc/default/g19-hud
  userdel g19hud 2>/dev/null || true
fi
echo "G19 HUD désinstallé."
