# Zadig (copie pratique)

`zadig-2.9.exe` est une **copie non modifiée** de [Zadig](https://zadig.akeo.ie/) 2.9, l'outil d'installation de pilotes
USB (WinUSB) de Pete Batard / Akeo Consulting, inclus ici pour faciliter l'installation du G19 sous Windows.

| | |
|---|---|
| Source officielle | https://github.com/pbatard/libwdi/releases/tag/v1.5.1 (`zadig-2.9.exe`) |
| Code source | https://github.com/pbatard/libwdi/tree/v1.5.1 |
| Taille | 5 334 088 octets |
| SHA-256 | `4ecaa95df3da3621486a043aef8b3050b8bafe7c901402871e816229ef82039b` |
| Signature | Authenticode valide, éditeur « Akeo Consulting » |
| Licence | GNU GPL v3 (texte : [`ZADIG-LICENSE-GPLv3.txt`](ZADIG-LICENSE-GPLv3.txt)) — ce n'est **pas** la licence MIT du reste du dépôt |

Vérification avant utilisation (PowerShell) :

```powershell
(Get-FileHash .\windows\tools\zadig-2.9.exe -Algorithm SHA256).Hash
(Get-AuthenticodeSignature .\windows\tools\zadig-2.9.exe).Status   # doit afficher Valid
```

En cas de doute, préférez le téléchargement depuis le lien officiel ci-dessus. Mode d'emploi pour le G19 : [`../README.md`](../README.md).
