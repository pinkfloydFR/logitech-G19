# G19 HUD — Windows (à faire)

Le cœur (`g19_lcd.py`, `g19_hud.py`, `g15_dashboard.py`) est commun avec la version Linux.
Ce dossier accueillera l'emballage Windows :

- Installation **une fois** du pilote WinUSB sur `046d:c229` (Zadig) — le clavier `046d:c228` n'est pas concerné.
- `libusb-package` pour que pyusb trouve libusb sans installation manuelle.
- Icône dans la barre système, `.exe` PyInstaller, démarrage avec Windows.
- Écran Média (WinRT) en version couleur native.

Voir `../CLAUDE.md` pour le protocole du G19.
