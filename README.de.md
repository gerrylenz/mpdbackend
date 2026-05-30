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
└─────────────┘                             │ Icecast / HTTP                     │ Wiedergabe
                                            ▼                                    ▼
                                     stream 0..2                           Chromecast / …
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
- **HTTP-API**: `/nowplaying`, `/cover`, `/stationlogo`, `/channels`, `/health`
- Chromecast-taugliche Metadaten-Updates (Playback-Sessions, Image-Cache)

## Projektstruktur

```
mpdbackend/
├── server/                    # Auf jedem MPD-/Workplayer-Host deployen
│   ├── mpdbackend.py          # MPD-Worker, Kanal-Registry
│   ├── mpdbackend_http.py     # HTTP-API
│   ├── mpdbackend_mqtt.py     # MQTT-Publish + MPD-Befehle
│   ├── mpdbackend_cover.py    # Cover-Extraktion und Cache
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
| `stream_url` | HTTP/Icecast-URL für die Wiedergabe in MA |
| `content_type` | `mp3`, `aac`, `ogg`, `flac` |
| `backend_url` | Optional; Metadaten-Server für diesen Kanal |

## Konfiguration (Server)

Einstellungen kommen aus **`mpdbackend.env`** (neben `mpdbackend.py` oder `/etc/mpdbackend.env` mit systemd). Zugangsdaten sind nicht im Python-Code hardcodiert.

Mit `MPDBACKEND_MQTT_ENABLED=false` läuft der Server nur mit HTTP (kein MQTT-Broker nötig).

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

Vollständige Liste: `server/systemd/mpdbackend.env.example`

## HTTP-API

| Endpoint | Beschreibung |
|----------|--------------|
| `GET /nowplaying` | Aktuelle MPD-Metadaten (JSON, siehe unten) |
| `GET /cover?name=cover_….jpg` | Gecachtes Cover |
| `GET /stationlogo?channel=0` | Senderlogo für Kanal-ID |
| `GET /channels` | Kanalregistry (`channels.json`) |
| `GET /health` | MPD-/MQTT-Verbindungsstatus |
| `GET /hash` | State-Hash zur Änderungserkennung |

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
  "cover_name": "cover_a1b2c3.jpg"
}
```

`duration` und `elapsed` sind Sekunden (Float). Der Music-Assistant-Provider nutzt `cover_name` für den Image-Proxy.

## MQTT

Bei `MPDBACKEND_MQTT_ENABLED=true` publiziert der Server Status und nimmt MPD-Steuerbefehle entgegen. Standard-Topic-Präfix: `mpdbackend/` (alle Topics über `MPDBACKEND_MQTT_TOPIC_*` konfigurierbar).

| Topic | Payload | Beschreibung |
|-------|---------|--------------|
| `mpdbackend/state` | JSON, retained | Track-Metadaten: `state`, `title`, `artist`, `album`, `duration` (`M:SS`), `cover_name`, `volume`, `lastloadedplaylist` |
| `mpdbackend/elapsed` | Text, retained | Aktuelle Position als `M:SS` oder `H:MM:SS` (Update alle `MPDBACKEND_MQTT_ELAPSED_INTERVAL` s) |
| `mpdbackend/current` | JSON, retained | Queue-Kontext: `playlist`, `pos`, `file` |
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
  "lastloadedplaylist": "Pop"
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

## Entwicklung

```bash
# Server-Tests
cd server && pytest tests/

# Provider-Tests
cd music_assistant && pytest tests/
```

## Lizenz

MIT License
