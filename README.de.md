# MPD Backend

[English](README.md) · **Deutsch**

Brücke zwischen **MPD** (Music Player Daemon) und **[Music Assistant](https://music-assistant.io/)**.  
MPD spielt lokale Dateien oder Playlists; ein Icecast-/HTTP-Stream sendet das Audio aus. Dieses Projekt liefert **Live-Metadaten, Cover und Senderlogos**, damit Music Assistant und Player (Chromecast, Sonos, …) anzeigen, was auf MPD wirklich läuft — nicht nur generische ICY-Tags aus dem Stream.

## Architektur

```
┌─────────────┐
│   MPD #0    │──┐
└─────────────┘  │
┌─────────────┐  │  idle/events    ┌──────────────────┐     HTTP/MQTT    ┌─────────────────┐
│   MPD #1    │──┼────────────────►│  mpdbackend.py   │◄─────────────────│ Music Assistant │
└─────────────┘  │                 │  (server/)       │    /nowplaying   │  (provider/)    │
┌─────────────┐  │                 └────────┬─────────┘    /cover        └───────┬─────────┘
│   MPD #2    │──┘                          │                                    │
└─────────────┘              ┌──────────────┼──────────────┐                     │ Wiedergabe
                             │              │              │                     ▼
                      Browser│         Icecast / HTTP      │              Chromecast / …
                      :4533/ │              │              │
                             ▼              ▼              ▼
                        Web-Player    stream 0..2    MQTT (optional)
                                        ▲
                                        └── Audio-Stream (getrennt von Metadaten)
```

| Komponente | Aufgabe |
|------------|---------|
| **`server/`** | Pro MPD-Host eine Instanz: liest MPD-Status, extrahiert Cover, stellt HTTP-API bereit |
| **`music_assistant/`** | Music-Assistant-Provider: lädt Kanäle, holt Metadaten, aktualisiert Player |

Pro **MPD-Host** läuft eine eigene **mpdbackend-Server-Instanz**. Eine gemeinsame **`channels.json`** listet alle Radiosender (`0`, `1`, `2`, …); jeder Kanal hat eigene `stream_url` und optional `backend_url`.

## Funktionen

- Live **Titel, Künstler, Album** von MPD (nicht nur ICY-Stream-Tags)
- **Cover** aus eingebetteten Tags (ID3/APIC), ffmpeg-Video-Stream oder Ordnerbildern (`cover.jpg`)
- **Senderlogos** pro Kanal (`channel_0.png`, `channel_1.png`, …)
- **Dynamische Kanalliste** über `channels.json` (Reload ohne Neustart)
- **MQTT**-Status und **Fernsteuerung von MPD** (optional, z. B. Home Assistant)
- **Track-Dauer** und **abgelaufene Zeit** per MQTT (Format `M:SS`)
- **Web-Player** unter `http://host:4533/` (responsive für Mobilgeräte): Cover, Metadaten, Stream, MPD-Steuerung, Playlist-Auswahl
- **HTTP-API**: Metadaten, Cover, Steuerung, Playlists, „Zum Löschen markieren“
- Chromecast-taugliche Metadaten-Updates (Playback-Sessions, Image-Cache)

## Projektstruktur

```
mpdbackend/
├── server/                    # Auf jedem MPD-/Workplayer-Host deployen
│   ├── mpdbackend.py          # MPD-Worker, Kanal-Registry
│   ├── mpdbackend_http.py     # HTTP-API
│   ├── mpdbackend_mqtt.py     # MQTT-Publish + MPD-Befehle
│   ├── mpdbackend_cover.py    # Cover-Extraktion und Cache
│   ├── web/                   # Web-Player (HTML/CSS/JS)
│   ├── home_assistant/        # Beispiele für MQTT-Integration (HA)
│   ├── channels.json.example
│   ├── install/
│   │   ├── install.sh
│   │   └── requirements.txt
│   └── systemd/
└── music_assistant/           # In Music Assistant deployen
    └── mpdbackend/
        ├── provider.py
        ├── manifest.json
        └── …
```

## Voraussetzungen

**Server**

- Python 3.10+
- MPD mit Unix-Socket
- ffmpeg
- Python-Pakete: `Pillow`, `python-mpd2`, `paho-mqtt`

**Music Assistant**

- Music Assistant 2.8+ (2.9 empfohlen für Image-Proxy)

## Server-Installation

```bash
cd server
chmod +x install/install.sh
./install/install.sh --systemd
```

Konfiguration anpassen:

```bash
nano mpdbackend.env      # MQTT, MPD-Socket, Pfade
nano channels.json       # Radiosender (Kopie von channels.json.example)
```

Logos:

```text
data/logos/channel_0.png
data/logos/channel_1.png
```

Starten / prüfen:

```bash
sudo systemctl restart mpdbackend
curl http://127.0.0.1:4533/health
curl http://127.0.0.1:4533/channels
```

Web-Player im Browser: `http://127.0.0.1:4533/`

## Music-Assistant-Provider

Provider in den Music-Assistant-Container (oder `custom_components`-Pfad) kopieren und in der MA-Oberfläche hinzufügen.

Beispiel (Deploy aus dem Music-Assistant-Hauptrepo):

```bash
docker cp music_assistant/mpdbackend music-assistant:/app/venv/lib/python3.14/site-packages/music_assistant/providers/
docker restart music-assistant
```

In Music Assistant **eine Backend-URL** konfigurieren (Standard-Metadaten-Server). Kanäle mit eigenem `backend_url` in `channels.json` unterstützen Multi-Standort-Setups.

## Kanal-Konfiguration

`channels.json` definiert die in Music Assistant sichtbaren Radiosender:

```json
{
  "0": {
    "name": "Store Radio",
    "description": "Hauptbereich",
    "stream_url": "https://example.com:8000/stream.mp3",
    "content_type": "mp3",
    "backend_url": "http://mpd-host:4533"
  }
}
```

| Feld | Bedeutung |
|------|-----------|
| `name` | Anzeigename in Music Assistant |
| `stream_url` | HTTP/Icecast-URL für Audio (Music Assistant **und** Web-Player) |
| `content_type` | `mp3`, `aac`, `ogg`, `flac` |
| `backend_url` | Optional; Metadaten-Server für diesen Kanal |

**Wichtig:** `stream_url` liefert das **Audio**, `backend_url` bzw. `:4533` liefert **Metadaten und Steuerung**. Beides wird für den Web-Player benötigt.

## Konfiguration (Server)

Einstellungen kommen aus **`mpdbackend.env`** (neben `mpdbackend.py` oder `/etc/mpdbackend.env` mit systemd). Zugangsdaten sind nicht im Python-Code hardcodiert.

Mit `MPDBACKEND_MQTT_ENABLED=false` läuft der Server nur mit HTTP (kein MQTT-Broker nötig). Steuerung ist dann über die **HTTP-POST-Endpunkte** (`/cmd/player`, `/cmd/volume`, `/cmd/playlist`, `/cmd/savefile`) und den **Web-Player** möglich — funktional äquivalent zu den MQTT-`cmd/*`-Topics.

Bei aktivem MQTT sind Pflichtfelder:

```bash
MPDBACKEND_MQTT_ENABLED=true
MPDBACKEND_MQTT_BROKER=mqtt.example.com
MPDBACKEND_MQTT_USERNAME=user
MPDBACKEND_MQTT_PASSWORD=geheim
```

Häufige Optionen:

| Variable | Zweck |
|----------|-------|
| `MPDBACKEND_MQTT_ENABLED` | MQTT-Publish und Steuerung aktivieren (Standard: `true`) |
| `MPDBACKEND_MQTT_TOPIC_*` | Topic-Namen (siehe [MQTT](#mqtt) unten) |
| `MPDBACKEND_MQTT_ELAPSED_INTERVAL` | Intervall für elapsed-Publish in Sekunden (Standard: `1`) |
| `MPDBACKEND_MPD_SOCKET` | MPD-Socket (Standard: `/run/mpd/socket`) |
| `MPDBACKEND_PLAYLIST_DIR` | MPD-Playlist-Verzeichnis (optional; sonst aus MPD-Config) |
| `MPDBACKEND_MUSIC_ROOT` | Musik-Bibliothek für Cover-Extraktion |
| `MPDBACKEND_COVER_DIR` | Cover-Cache (JPEG) |
| `MPDBACKEND_STATION_LOGO_DIR` | Senderlogos |
| `MPDBACKEND_CHANNELS_FILE` | Pfad zur `channels.json` |
| `MPDBACKEND_PUBLIC_BASE_URL` | Öffentliche URL dieses Backends |
| `MPDBACKEND_HTTP_PORT` | HTTP-Port (Standard: `4533`) |
| `MPDBACKEND_WEB_DIR` | Pfad zum Web-Player (Standard: `server/web/`) |
| `MPDBACKEND_MARKED_FOR_DELETE` | Zieldatei für „Zum Löschen markieren“ (Standard: `data/mark_for_delete.cfg`) |

Vollständige Liste: `server/systemd/mpdbackend.env.example`

## Web-Player

Unter **`http://host:4533/`** liefert mpdbackend eine responsive Web-Oberfläche — Layout und Schriftgrößen passen sich der Viewport-Größe des aufrufenden Geräts an (`clamp`, `vmin`, `dvh`; Portrait, Querformat, verschiedene Displaygrößen):

- **Anzeige:** Cover, Titel, Künstler, Album, Fortschritt (elapsed/duration), Track-Position in der Playlist
- **Sender:** Kanalauswahl über `channels.json` (Logo + `stream_url`)
- **Playlist:** aktive Playlist und Auswahl aller MPD-Playlists
- **Steuerung:** Play/Stop, Next/Back, Lautstärke (über HTTP → MPD)
- **Stream:** Audio über `stream_url` aus `channels.json` (getrennt von Metadaten)
- **Markieren:** rotes Kreuz → `POST /cmd/savefile` → schreibt MPD-Dateipfad nach `MPDBACKEND_MARKED_FOR_DELETE`

Audio kommt vom **HTTP-Stream** (MPD `httpd`/Icecast), Steuerung und Metadaten vom **mpdbackend-Port 4533**.

### Mark for delete

`POST /cmd/savefile` überschreibt die konfigurierte Datei (Standard: `data/mark_for_delete.cfg`) mit **einer Zeile** — dem relativen MPD-Dateipfad unter `MPDBACKEND_MUSIC_ROOT`, z. B.:

```text
Künstler/Album/Titel.mp3
```

Ein externer Job kann diese Datei auslesen und den Eintrag verarbeiten (z. B. löschen, verschieben, in eine Queue schreiben).

## HTTP-API

| Endpoint | Beschreibung |
|----------|--------------|
| `GET /` | Web-Player (statische Dateien aus `web/`) |
| `GET /nowplaying` | Aktuelle MPD-Metadaten (JSON, siehe unten) |
| `GET /playlists` | Verfügbare MPD-Playlists und aktive Playlist |
| `GET /cover?name=cover_….jpg` | Gecachtes Cover |
| `GET /stationlogo?channel=0` | Senderlogo für Kanal-ID |
| `GET /channels` | Kanalregistry (`channels.json`) |
| `GET /health` | MPD-/MQTT-Verbindungsstatus |
| `GET /hash` | State-Hash zur Änderungserkennung |
| `POST /cmd/player` | MPD-Transport: Plain-Text `play`, `stop`, `next`, `back` |
| `POST /cmd/volume` | Lautstärke setzen: Plain-Text `0`–`100` |
| `POST /cmd/playlist` | Playlist laden und abspielen: Plain-Text z. B. `Pop.m3u` |
| `POST /cmd/savefile` | MPD-Dateipfad des aktuellen Titels nach `MPDBACKEND_MARKED_FOR_DELETE` schreiben |

**Antwort von `/nowplaying`** (Beispiel):

```json
{
  "state": "play",
  "title": "Songtitel",
  "artist": "Künstler",
  "album": "Album",
  "songid": "42",
  "duration": 245.0,
  "elapsed": 83.5,
  "cover_name": "cover_a1b2c3.jpg",
  "volume": 45,
  "playlist": "Pop.m3u",
  "pos": 3,
  "playlist_length": 12,
  "file": "Künstler/Album/Titel.mp3"
}
```

**Antwort von `/playlists`** (Beispiel):

```json
{
  "playlists": ["Pop.m3u", "Rock.m3u"],
  "active": "Pop.m3u"
}
```

**Antwort von `POST /cmd/savefile`** (Beispiel):

```json
{
  "ok": true,
  "file": "Künstler/Album/Titel.mp3",
  "path": "/opt/mpdbackend/data/mark_for_delete.cfg"
}
```

`duration` und `elapsed` sind Sekunden (Float). `pos` ist 1-basiert. Der Music-Assistant-Provider nutzt `cover_name` für den Image-Proxy.

## MQTT

Bei `MPDBACKEND_MQTT_ENABLED=true` publiziert der Server Status und nimmt MPD-Steuerbefehle entgegen. Standard-Topic-Präfix: `mpdbackend/` (alle Topics über `MPDBACKEND_MQTT_TOPIC_*` konfigurierbar).

| Topic | Payload | Beschreibung |
|-------|---------|--------------|
| `mpdbackend/state` | JSON, retained | Track-Metadaten: `state`, `title`, `artist`, `album`, `duration` (`M:SS`), `cover_name`, `volume`, `lastloadedplaylist` |
| `mpdbackend/elapsed` | Text, retained | Aktuelle Position als `M:SS` oder `H:MM:SS` (Update alle `MPDBACKEND_MQTT_ELAPSED_INTERVAL` s) |
| `mpdbackend/current` | JSON, retained | Queue: `playlist`, `pos`, `file`, optional `volume` |
| `mpdbackend/playlists` | JSON, retained | Verfügbare Playlists: `playlists` (Array) |
| `mpdbackend/cover` | JPEG-Binärdaten, retained | Aktuelles Cover-Bild |
| `mpdbackend/connected` | `online` / `offline`, retained | Verfügbarkeit (LWT für Home Assistant) |
| `mpdbackend/cmd/volume` | Text subscribe | Lautstärke setzen: `45` (0–100); `volume` erscheint in `state` |
| `mpdbackend/cmd/player` | Text subscribe | MPD-Transport: `play`, `stop`, `next`, `back` |
| `mpdbackend/cmd/playlist` | Text subscribe | Playlist laden (Payload = Dateiname) |

**Beispiel `mpdbackend/state`:**

```json
{
  "state": "play",
  "title": "Songtitel",
  "artist": "Künstler",
  "album": "Album",
  "duration": "4:05",
  "cover_name": "cover_a1b2c3.jpg",
  "volume": 45,
  "lastloadedplaylist": "Pop.m3u"
}
```

**Steuerung** — Plain-Text publizieren:

| Topic | Payload |
|-------|---------|
| `mpdbackend/cmd/player` | `play` |
| `mpdbackend/cmd/player` | `stop` |
| `mpdbackend/cmd/player` | `next` |
| `mpdbackend/cmd/player` | `back` |
| `mpdbackend/cmd/playlist` | `Pop.m3u` |

**Lautstärke setzen** — Plain-Text auf `mpdbackend/cmd/volume`:

```
45
```

Nach dem Laden einer Playlist meldet `mpdbackend/current` den aktiven Playlist-Namen (`playlist`). Die Liste aller Playlists steht auf `mpdbackend/playlists`.

Beispiel-Konfigurationen für Home Assistant liegen unter `server/home_assistant/` (`mqtt.yaml_example`, `status_card.example`, …).

## Entwicklung

```bash
# Server-Tests
cd server && pytest tests/

# Provider-Tests
cd music_assistant && pytest tests/
```

## Lizenz

MIT License
