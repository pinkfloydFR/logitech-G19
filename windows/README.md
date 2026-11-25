# G19 HUD — Windows

Même cœur que la version Linux (`g19_lcd.py`, `g19_hud.py`, `g15_dashboard.py`). Testé sur un G19 réel sous Windows 11, Python 3.14.

## Installation (une seule fois)

1. **Pilote WinUSB sur l'interface 0 de l'écran** avec [Zadig](https://zadig.akeo.ie/) (lancé en administrateur) :
   - `Options → List All Devices`
   - choisir **« G19 Gaming Keyboard (Display interface) (Interface 0) »** et vérifier l'**USB ID `046D C229 00`**
     (⚠️ jamais `C228` : c'est le clavier lui-même)
   - pilote cible **WinUSB**, puis **Install Driver**.
2. Lancer :
   ```powershell
   .\windows\run.ps1            # sans fenêtre, icône dans la barre système
   .\windows\run.ps1 -Console   # avec la console (logs)
   ```
   Le script crée `.venv-win` et installe `windows/requirements.txt` au premier lancement.

Retour en arrière : Gestionnaire de périphériques → l'interface 0 du G19 → *Désinstaller le périphérique* (cocher « supprimer le pilote »).

## Ce qui marche / ce qui est limité

| Fonction | Sans WinUSB sur l'interface 1 | Avec WinUSB sur l'interface 1 + `G19_GKEYS=1` |
|---|---|---|
| Écran couleur 320x240, luminosité | ✅ | ✅ |
| Touches LCD (Menu, OK, flèches, Retour, Home) | ✅ | ✅ |
| Touches G1–G12, M1–M3, MR | ❌ | ✅ |
| Couleur du rétroéclairage, LED M | ❌ | ✅ |

> **Pourquoi désactivé par défaut ?** Sous Windows, toucher l'interface 1 tant qu'elle est sous le pilote HID de
> Windows provoque `Entity not found` et **invalide toute la connexion USB** (écran compris). Le code les ignore donc,
> sauf si `G19_GKEYS=1`. Pour les activer : refaire l'étape Zadig sur **`046D C229 01`** (interface 1), puis
> `$env:G19_GKEYS = "1"` avant de lancer. Cette étape n'est pas encore testée.

## Notes
- Arrêt : menu de l'icône (« Quitter G15 HUD »), `Ctrl+C` en mode console, ou `G19_MAX_SECONDS=N` pour un arrêt automatique (tests).
  **Ne jamais tuer le processus** (`Stop-Process -Force`) pendant que l'écran s'affiche : l'écran peut rester figé jusqu'au rebranchement USB.
- Pas encore fait : `.exe` PyInstaller, démarrage automatique avec Windows, écran Média (WinRT) en couleur native.
- Protocole et pièges détaillés : [`../CLAUDE.md`](../CLAUDE.md).
