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

Konfiguration: `config.json` im gleichen Ordner wie `MPD-Player.exe` bzw. `mpd_player.py` (Vorlage: `config.example.json`)

| Option | Bedeutung |
|--------|-----------|
| `minimize_to_tray` | X schließt ins Tray statt Beenden (Standard: an) |
| `autostart` | App beim Windows-Login starten |

## Taskleiste

- **Doppelklick / Anzeigen** — Fenster wiederherstellen
- **Einstellungen** — URL, Passwort, Tray, Autostart
- **Autostart** — Haken setzen/entfernen
- **Beenden** — App wirklich beenden

## „Diese Website unterstützt keine sichere Verbindung“

WebView2 zeigt das, wenn die App per **HTTPS** verbindet, der mpdbackend-Server aber nur **HTTP** spricht (typisch bei `https://127.0.0.1:4533`).

**Erscheint die Meldung kurz und verschwindet nach Wegklicken:** Edge/WebView2 stuft die Adresse zuerst auf HTTPS hoch. Neuere App-Versionen setzen dagegen WebView2-Flags automatisch (`build.bat` / EXE neu bauen bzw. `python mpd_player.py` aus dem aktuellen Stand).

**Lösung:**

1. In den Einstellungen (Tray → Einstellungen) oder in `config.json` neben der EXE die URL auf **`http://`** setzen, z. B.:
   - `http://127.0.0.1:4533` (Server auf demselben PC)
   - `http://192.168.1.10:4533` (Server im LAN)
2. Im normalen Browser testen: dieselbe `http://`-URL muss dort laden.
3. Server erreichbar? mpdbackend muss laufen und Port 4533 (oder `MPDBACKEND_HTTP_PORT`) offen sein.
4. Nur bei **HTTPS hinter Reverse-Proxy** mit gültigem Zertifikat `https://…` verwenden; bei selbstsigniertem Zertifikat ignoriert die App Zertifikatsfehler automatisch.

`MPDBACKEND_PUBLIC_BASE_URL=https://…` in der Server-`.env` ist nur für Cover-URLs (Handy/CarPlay) — **nicht** als Player-URL in der Windows-App verwenden, sofern kein echter HTTPS-Zugang zum Web-UI besteht.

## EXE bauen

```powershell
.\build.bat
```

Erzeugt `dist\MPD-Player.exe` mit Icon und eingebetteten `assets/`.

PyInstaller wird über `python -m PyInstaller` aufgerufen (nicht `pyinstaller` im PATH nötig).
pywebview bringt einen eigenen PyInstaller-Hook mit; `--collect-all webview` ist nicht nötig
und erzeugt sonst harmlose Android-Warnungen.

**Build auf Netzlaufwerk (`Y:\`, NAS):** `build.bat` schreibt die EXE zuerst nach
`%LOCALAPPDATA%\mpdbackend-player-build` (lokale Festplatte) und kopiert sie danach nach
`dist\`. So entfallen die Warnungen `EndUpdateResourceW` / „Zugriff verweigert“.

Falls der Build trotzdem scheitert:

- **MPD-Player.exe beenden** (Taskleiste → Beenden), dann erneut `build.bat`
- Kein Explorer-Fenster mit geöffneter `dist\MPD-Player.exe`
- Antivirus kurz pausieren, falls die EXE beim Schreiben blockiert wird
