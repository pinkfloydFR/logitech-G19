#!/usr/bin/env bash
# À lancer SUR l'hôte Proxmox, en root : crée un utilisateur « g19hud@pve » en lecture seule (rôle PVEAuditor)
# et un jeton d'API pour l'écran PROXMOX du HUD G19, puis affiche les lignes à coller dans /etc/default/g19-hud.
#
#   sudo ./linux/proxmox-token.sh            # utilisateur g19hud@pve, jeton « hud »
#   sudo ./linux/proxmox-token.sh --write    # écrit aussi /etc/default/g19-hud (0600) et redémarre le service
#
# Le jeton n'a AUCUN droit d'écriture : il ne peut ni démarrer, ni arrêter, ni modifier une VM.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
command -v pveum >/dev/null || { echo "pveum introuvable : ce script se lance sur un hôte Proxmox VE." >&2; exit 1; }

USER_ID="g19hud@pve"
TOKEN="hud"
WRITE=0
[ "${1:-}" = "--write" ] && WRITE=1

pveum user list --output-format json | grep -q "\"userid\":\"$USER_ID\"" || pveum user add "$USER_ID" --comment "HUD Logitech G19 (lecture seule)"
pveum acl modify / --users "$USER_ID" --roles PVEAuditor 2>/dev/null || pveum aclmod / -user "$USER_ID" -role PVEAuditor

if pveum user token list "$USER_ID" --output-format json | grep -q "\"tokenid\":\"$TOKEN\""; then
  echo "Le jeton $USER_ID!$TOKEN existe déjà : le secret n'est affiché qu'à la création." >&2
  echo "Pour en générer un nouveau :  pveum user token remove $USER_ID $TOKEN  puis relancer ce script." >&2
  exit 1
fi
OUT="$(pveum user token add "$USER_ID" "$TOKEN" --privsep 0 --output-format json)"
SECRET="$(printf '%s' "$OUT" | python3 -c 'import sys,json; print(json.load(sys.stdin)["value"])')"
FP="$(openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f' || true)"

LINES="PVE_HOST=127.0.0.1
PVE_TOKEN_ID=$USER_ID!$TOKEN
PVE_TOKEN_SECRET=$SECRET
PVE_FINGERPRINT=$FP"

if [ "$WRITE" -eq 1 ]; then
  touch /etc/default/g19-hud && chmod 600 /etc/default/g19-hud
  sed -i '/^PVE_/d' /etc/default/g19-hud
  printf '%s\n' "$LINES" >> /etc/default/g19-hud
  systemctl restart g19-hud.service 2>/dev/null || true
  echo "Écrit dans /etc/default/g19-hud (0600). Service redémarré."
else
  echo "Ajoutez ceci à /etc/default/g19-hud (fichier en 0600, lisible par root uniquement) :"
  echo "------------------------------------------------------------"
  printf '%s\n' "$LINES"
  echo "------------------------------------------------------------"
  echo "puis :  systemctl restart g19-hud"
fi
