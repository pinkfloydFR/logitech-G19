# Logitech G19 HUD — notes techniques (Linux / Windows / Proxmox)

Fork de DPKoss/G15-Retro-HUD (MIT, tableau de bord G15 d origine) adapté au **Logitech G19** (écran couleur 320x240) sous **Linux**.
Testé sur un G19 réel via WSL2 (usbipd), cible finale : hôte Proxmox (Debian).

## Fichiers ajoutés / modifiés
- `g19_lcd.py` : pilote USB bas niveau (pyusb/libusb) : trames LCD, rétroéclairage, LED M, luminosité, touches LCD, touches G/M.
- `g19_hud.py` : interface **native couleur 320x240** (7 écrans). Réutilise la logique/boutons des écrans G15 d'origine (`logic`), ne redessine que le rendu. `make_screens(module_g19_dashboard)`.
- `g19_dashboard.py` : `G19Controller` (même interface publique que `G15HidController`), `create_hw()` (choix du backend), portage Linux (polices DejaVu, NVML `libnvidia-ml.so.1`, route par défaut via `/proc/net/route`, ping Linux, noms d'interfaces Proxmox), arrêt propre sur SIGINT/SIGTERM. `hidapi` est devenu optionnel.

## Variables d'environnement
| Variable | Défaut | Rôle |
|---|---|---|
| `G19_BACKEND` | `auto` | `g15` / `g19` / `auto` (G19 si branché) |
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
- **Ne jamais interrompre brutalement une écriture de trame** (SIGKILL, `pkill -9`, fin de process en plein envoi) : le firmware reste désynchronisé, l'écran fige (souvent orange) et seul un **débranchement/rebranchement USB** le débloque (un `reset()` logiciel ne suffit pas). D'où les handlers SIGINT/SIGTERM de `g19_dashboard.py` : toujours arrêter le HUD par SIGTERM/SIGINT.
- Après un envoi de test isolé, garder le périphérique ouvert ~2 s avant `close()` : sinon l'image peut ne pas s'afficher.
- Les erreurs `USBError [Errno 5]` peuvent survenir sur EP 0x83 quand on appuie sur la touche soleil : toujours les intercepter dans les boucles de lecture.
- Deux processus ne peuvent pas ouvrir le G19 en même temps (interfaces « busy »).
- Le fichier `pkill -f "motif"` lancé depuis `bash -lc "..."` se tue lui-même si le motif apparaît dans la commande : utiliser `[g]15_dashboard.py`.

## Test sous WSL2 (poste Windows)
1. `usbipd bind --busid <BUSID>` (une fois) puis `usbipd attach --wsl --busid <BUSID>` pour **`046d:c229`** uniquement (pas le clavier `c228`).
2. Garder une session WSL ouverte (`wsl sleep 3000`), sinon `attach` échoue (« no WSL 2 distribution running »).
3. venv : `python3 -m venv ~/g19/venv && ~/g19/venv/bin/pip install pyusb pillow psutil numpy` (+ `fonts-dejavu-core`, `libusb-1.0-0`).
4. Lancer : `cd src && ~/g19/venv/bin/python -u g19_dashboard.py` (root dans WSL, pas besoin d'udev).
5. Test sans matériel : `G19_DUMP=/tmp/f.png` produit la dernière trame en PNG.

## Organisation du dépôt (un seul dépôt, cœur commun)
- Racine : cœur commun (`g19_dashboard.py`, `g19_lcd.py`, `g19_hud.py`) — à corriger une seule fois pour les deux OS.
- `linux/` : installation Debian/Ubuntu/Proxmox (`install.sh`, `uninstall.sh`, `g19-hud.service`, `99-g19-hud.rules`).
- `windows/` : `run.ps1`, `requirements.txt`, `README.md` — testé sur G19 réel (voir section Windows). Reste : `.exe` PyInstaller, démarrage auto, écran Média WinRT en couleur native.

## Windows (testé sur G19 réel, Windows 11, Python 3.14)
- Pilote **WinUSB** (Zadig, lancé en admin) sur l'interface **0** de `046D C229` (⚠️ jamais `C228` = le clavier). Sans pilote, `usb.core.find` voit le périphérique mais `open()` lève `NotImplementedError`.
- `libusb-package` fournit `libusb-1.0.dll` (`g19_lcd._BACKEND`). Si l'appareil est « Shared » dans usbipd, faire `usbipd detach` + `unbind` avant (sinon `Access denied`).
- **Piège majeur** : toucher l'**interface 1** (lecture EP 0x83, `SET_REPORT` avec `wIndex=1`) tant qu'elle est sous le pilote HID Windows → `USBError 2 Entity not found` sur TOUT le handle, écran compris. Donc sous Windows `has_gkeys=False` par défaut : pas de touches G/M, pas de couleur de rétroéclairage ni de LED M, sauf `G19_GKEYS=1` après une 2ᵉ install Zadig sur `046D C229 01` (non testé). La luminosité (`wIndex=0`) et les touches LCD (EP 0x81) marchent sans cela.
- Arrêt propre : SIGINT/SIGTERM/SIGBREAK + `G19_MAX_SECONDS=N` (tests) ; avec l'icône tray, un thread surveille `app.running` pour fermer le tray puis `worker.join(8)` laisse finir la trame USB et l'écran noir final. Ne jamais `Stop-Process -Force` pendant un envoi (écran figé → rebrancher).
- À l'arrêt, le HUD natif envoie un écran **noir** (l'ambre = bug : c'était l'image vide du mode retro).

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

## Dépôt GitHub
https://github.com/pinkfloydFR/logitech-G19 (public, branche `main`). Origine : https://github.com/DPKoss/G15-Retro-HUD (remote `upstream` en local).
`windows/tools/` contient une copie de Zadig 2.9 (GPLv3, SHA-256 dans son README) — `.exe` ajouté avec `git add -f` car `*.exe` est dans `.gitignore`.

## Écrans, Proxmox et arrêt (ajouts récents)
- `G19_SCREENS` (liste ordonnée, `all`) choisit les écrans ; défaut `monitoring,reseau,serveurs,proxmox,systeme` (horloge/chrono/météo retirés par choix de l'utilisateur, toujours disponibles). Le télémétrie météo/IP publique tourne quand même en arrière-plan (la météo est inutile si l'écran est absent : à désactiver si besoin).
- Monitoring : la jauge GPU est remplacée par l'utilisation réseau (débit / vitesse du lien ; lien inconnu, ex. `vmbr0` → max(100 Mb/s, pic récent)).
- Écran **Proxmox** (`g19_proxmox.py` + `ProxmoxNative`) : `GET /api2/json/cluster/resources` avec jeton `PVEAPIToken=...` (PVEAuditor), TLS non vérifié par défaut + épinglage `PVE_FINGERPRINT`, thread d'arrière-plan (`snapshot()` sans blocage), `PVE_DEMO=1` pour des données fictives. Testé uniquement contre un faux serveur HTTPS (`tests/test_proxmox.py`, 8 tests, Windows + Linux). `linux/proxmox-token.sh` **jamais exécuté sur un vrai Proxmox**.
- Arrêt propre supplémentaire : `G19_STOPFILE=chemin` (créer le fichier arrête le HUD proprement, utile sous Windows sans signal) en plus de `G19_MAX_SECONDS`.
- `tools/render_screens.py` : rend les écrans en PNG avec des données fictives (jamais de données réelles dans les captures publiées : ville, IP, nom d'hôte).
