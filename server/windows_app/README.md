# MPD Player (Windows)

Desktop-Fenster für den mpdbackend-Web-Player (WebView2).

## Features

- Gleiche Oberfläche wie der Browser-Web-Player
- **Taskleisten-Icon** — Schließen (X) minimiert ins Tray (konfigurierbar)
- **Autostart** mit Windows (Einstellungen oder Tray-Menü)
- **App-Icon** für EXE und Taskleiste (`assets/icon.ico`)

## Voraussetzungen

- Windows 10/11 mit [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
- Python 3.10+ (Entwicklung / EXE-Bau)
- Laufender mpdbackend-Server

## Schnellstart

```powershell
cd server\windows_app
pip install -r requirements.txt
python generate_icon.py
python mpd_player.py
```

```powershell
python mpd_player.py --settings
python mpd_player.py --url http://192.168.1.10:4533 --password geheim
python mpd_player.py --no-tray
```

Konfiguration: `%APPDATA%\mpdbackend-player\config.json` (Vorlage: `config.example.json`)

| Option | Bedeutung |
|--------|-----------|
| `minimize_to_tray` | X schließt ins Tray statt Beenden (Standard: an) |
| `autostart` | App beim Windows-Login starten |

## Taskleiste

- **Doppelklick / Anzeigen** — Fenster wiederherstellen
- **Einstellungen** — URL, Passwort, Tray, Autostart
- **Autostart** — Haken setzen/entfernen
- **Beenden** — App wirklich beenden

## EXE bauen

```powershell
.\build.bat
```

Erzeugt `dist\MPD-Player.exe` mit Icon und eingebetteten `assets/`.

PyInstaller wird über `python -m PyInstaller` aufgerufen (nicht `pyinstaller` im PATH nötig).
pywebview bringt einen eigenen PyInstaller-Hook mit; `--collect-all webview` ist nicht nötig
und erzeugt sonst harmlose Android-Warnungen.
