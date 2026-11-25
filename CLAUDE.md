# G15-Retro-HUD — port Logitech G19 (Linux / WSL / Proxmox)

Fork de DPKoss/G15-Retro-HUD (MIT) adapté au **Logitech G19** (écran couleur 320x240) sous **Linux**.
Testé sur un G19 réel via WSL2 (usbipd), cible finale : hôte Proxmox (Debian).

## Fichiers ajoutés / modifiés
- `g19_lcd.py` : pilote USB bas niveau (pyusb/libusb) : trames LCD, rétroéclairage, LED M, luminosité, touches LCD, touches G/M.
- `g19_hud.py` : interface **native couleur 320x240** (7 écrans). Réutilise la logique/boutons des écrans G15 d'origine (`logic`), ne redessine que le rendu. `make_screens(module_g15_dashboard)`.
- `g15_dashboard.py` : `G19Controller` (même interface publique que `G15HidController`), `create_hw()` (choix du backend), portage Linux (polices DejaVu, NVML `libnvidia-ml.so.1`, route par défaut via `/proc/net/route`, ping Linux, noms d'interfaces Proxmox), arrêt propre sur SIGINT/SIGTERM. `hidapi` est devenu optionnel.

## Variables d'environnement
| Variable | Défaut | Rôle |
|---|---|---|
| `G15_BACKEND` | `auto` | `g15` / `g19` / `auto` (G19 si branché) |
| `G19_UI` | `native` | `retro` = rendu G15 160x43 agrandi ×2 (thèmes ci-dessous) |
| `G19_THEME` | `amber` | retro uniquement : `amber`, `blue`, `green`, `white`, `amber-dark`, `matrix` |
| `G19_LAYOUT` | `2x` | retro : `2x` (pixels carrés, centré) ou `stretch` (2x5) |
| `G19_BRIGHTNESS` | `100` | luminosité LCD 0-100 appliquée à la connexion |
| `G19_ROT` | `90` | rotation mémoire (ne pas changer sans raison) |
| `G19_DEBUG` | — | `1` = affiche les rapports de touches LCD bruts |
| `G19_DUMP` | — | chemin PNG : sauvegarde la dernière trame (test sans matériel) |

## Protocole G19 (vérifié sur matériel réel)
Périphérique **`046d:c229`** (« Display interface » / « Macro Interface »). Le clavier lui-même est `046d:c228` (laissé à Windows/au noyau, inutile pour le HUD).
- Interface 0 (vendor) : **EP 0x02 OUT bulk** = trames LCD ; **EP 0x81 IN interrupt (2 octets)** = touches LCD.
- Interface 1 (HID) : **EP 0x83 IN interrupt (7 o max, lire 8)** = touches G/M et touche « soleil ».
- Trame LCD = **512 octets d'en-tête + 153 600 octets RGB565 little-endian** (une seule écriture bulk de 154 112 o).
  En-tête : `10 0F 00 58 02 00 00 00 00 00 00 3F 01 EF 00 0F` puis le motif `(0x10 + i) & 0xFF` jusqu'à 512 o (identique à libg19).
- **La mémoire est en 240 colonnes x 320 lignes (portrait)** : pour une image paysage 320x240, appliquer
  `img.transpose(FLIP_LEFT_RIGHT).rotate(90, expand=True)` avant l'encodage. (Sans la rotation l'image est corrompue/tournée ; sans le miroir le texte est inversé.)
- Contrôles (`ctrl_transfer`) :
  - rétroéclairage touches : `0x21, 0x09, wValue=0x0307, wIndex=1`, données `[0x07, R, G, B]`
  - LED M1/M2/M3/MR : `0x21, 0x09, 0x0305, 1`, données `[0x05, bits]` (M1=0x80, M2=0x40, M3=0x20, MR=0x10)
  - luminosité LCD : `0x41, 0x0A, 0, 0`, 1 octet 0-100
- **Touches LCD** (EP 0x81, octet 0 = masque ; octet 1 constant `0x80`) : Home `0x01`, Cancel/Retour `0x02`, Menu `0x04`, OK `0x08`, Droite `0x10`, Gauche `0x20`, Bas `0x40`, Haut `0x80`.
- **Touches G/M** (EP 0x83, rapport `02 b1 b2 b3`) : G1..G8 = bits 0..7 de `b1` ; G9..G12 = bits 0..3 de `b2` ; M1/M2/M3/MR = bits 4..7 de `b2`. `b3` = `0x40` au repos ; **`0x08` = touche « soleil »** (luminosité : gérée par le firmware, éteint/allume tout le clavier + LCD ; le HUD ne la traite pas).
  Exemples : G1 `02 01 00 40`, G12 `02 00 08 40`, M1 `02 00 10 40`, MR `02 00 80 40`, soleil `02 00 00 48`.

## Mapping des touches dans le HUD
- Menu / OK = écran suivant (appui long > 0,9 s = écran off) ; Retour = écran précédent ; Home (carré) = écran off/on.
- Haut / Bas / Gauche / Droite = boutons 1-4 (légendes `▲ ▼ ◀ ▶` dans la barre du bas ; Haut/Bas = Pong sur l'écran retro).
- G1..G7 = saut direct à l'écran n ; M1 = précédent ; M2 = suivant ; MR = écran off/on.
- Remap des touches LCD : `g19_keymap.json` (`{"UP": "1", "MENU": "0", "CANCEL": "PREV_PAGE"}`).

## Pièges (apprentissages)
- **Ne jamais interrompre brutalement une écriture de trame** (SIGKILL, `pkill -9`, fin de process en plein envoi) : le firmware reste désynchronisé, l'écran fige (souvent orange) et seul un **débranchement/rebranchement USB** le débloque (un `reset()` logiciel ne suffit pas). D'où les handlers SIGINT/SIGTERM de `g15_dashboard.py` : toujours arrêter le HUD par SIGTERM/SIGINT.
- Après un envoi de test isolé, garder le périphérique ouvert ~2 s avant `close()` : sinon l'image peut ne pas s'afficher.
- Les erreurs `USBError [Errno 5]` peuvent survenir sur EP 0x83 quand on appuie sur la touche soleil : toujours les intercepter dans les boucles de lecture.
- Deux processus ne peuvent pas ouvrir le G19 en même temps (interfaces « busy »).
- Le fichier `pkill -f "motif"` lancé depuis `bash -lc "..."` se tue lui-même si le motif apparaît dans la commande : utiliser `[g]15_dashboard.py`.

## Test sous WSL2 (poste Windows)
1. `usbipd bind --busid <BUSID>` (une fois) puis `usbipd attach --wsl --busid <BUSID>` pour **`046d:c229`** uniquement (pas le clavier `c228`).
2. Garder une session WSL ouverte (`wsl sleep 3000`), sinon `attach` échoue (« no WSL 2 distribution running »).
3. venv : `python3 -m venv ~/g19/venv && ~/g19/venv/bin/pip install pyusb pillow psutil numpy` (+ `fonts-dejavu-core`, `libusb-1.0-0`).
4. Lancer : `cd src && ~/g19/venv/bin/python -u g15_dashboard.py` (root dans WSL, pas besoin d'udev).
5. Test sans matériel : `G19_DUMP=/tmp/f.png` produit la dernière trame en PNG.

## Organisation du dépôt (un seul dépôt, cœur commun)
- Racine : cœur commun (`g15_dashboard.py`, `g19_lcd.py`, `g19_hud.py`) — à corriger une seule fois pour les deux OS.
- `linux/` : installation Debian/Ubuntu/Proxmox (`install.sh`, `uninstall.sh`, `g19-hud.service`, `99-g19-hud.rules`).
- `windows/` : à faire (pilote WinUSB via Zadig sur `046d:c229`, `libusb-package`, icône tray, `.exe` PyInstaller, écran Média WinRT en couleur native).

## Installation Linux / Proxmox (testée dans WSL2 Ubuntu 24.04 avec systemd, sur G19 réel)
`sudo ./linux/install.sh [--user NOM|root] [--prefix DIR] [--no-apt] [--no-start]` puis `journalctl -u g19-hud -f`.
- Paquets apt (pas de venv) : `python3-usb python3-pil python3-psutil python3-numpy libusb-1.0-0 fonts-dejavu-core iputils-ping lm-sensors`.
- Utilisateur système `g19hud` (groupe `plugdev`), code dans `/opt/g19-hud`, `config.toml` créé au 1er lancement et conservé aux mises à jour.
- udev `99-g19-hud.rules` : `046d:c229` en `0660 plugdev` + `SYSTEMD_WANTS=g19-hud.service` (démarre au branchement).
- Service `g19-hud.service` : `Restart=always`, `KillSignal=SIGTERM`, `TimeoutStopSec=10` (arrêt propre indispensable, cf. pièges), surcharges dans `/etc/default/g19-hud` (`G19_BRIGHTNESS=60`...).
- Vérifié : tourne sans root, 3 `systemctl restart` d'affilée sans figer l'écran, arrêt propre dans le journal.
- Pas de GPU NVIDIA sur un hôte Proxmox : la jauge GPU est remplacée par le disque `/` ; la température CPU exige les capteurs (`sensors-detect` / modules `coretemp`/`k10temp`).
- Non testé sur un vrai Proxmox (Debian 12/13 + noyau PVE) : à valider au premier déploiement (VID/PID visibles par `lsusb`, accès `/dev/bus/usb`).
- Idées : écran des VM/CT Proxmox via l'API (`/api2/json/cluster/resources`), état des disques/ZFS, extinction de l'écran la nuit.

## Hors périmètre / limites
- Écrans Média (WinRT), Audio (WASAPI) et Demoscene non portés dans l'UI native (seul le mode `G19_UI=retro` les garde, hors Média/Audio qui sont Windows).
- Le HUD ne détecte pas la touche « soleil » comme action (le firmware gère la luminosité).
- Jamais testé : plusieurs G19 branchés, reconnexion à chaud prolongée, Windows (pyusb nécessite un driver WinUSB).

## Référence
Protocole d'après libg19 (jgeboski), LCDHost, endlessmind/G19LCD ; valeurs d'orientation, touches G/M et soleil mesurées sur le G19 de l'utilisateur.
