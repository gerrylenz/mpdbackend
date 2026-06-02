# MPD Backend

**English** · [Deutsch](README.de.md)

Bridge between **MPD** (Music Player Daemon) and **[Music Assistant](https://music-assistant.io/)**.  
MPD plays local files or playlists; an Icecast/HTTP stream broadcasts the audio. This project supplies **live metadata, cover art, and station logos** so Music Assistant and players (Chromecast, Sonos, etc.) show what is actually playing on MPD — not generic radio tags from the stream.

## Architecture

**Rule:** Each mpdbackend instance connects to **exactly one** MPD socket (`MPDBACKEND_MPD_SOCKET`). Multiple MPD processes → multiple mpdbackend processes (separate socket, HTTP port, and env per instance).

```
                         ┌──────────────────────────────────────┐
                         │   Music Assistant (1× provider)      │
                         │   channels from channels.json          │
                         └─────────────────┬────────────────────┘
                                           │ /nowplaying, /cover …
              ┌────────────────────────────┼────────────────────────────┐
              │                            │                            │
              ▼                            ▼                            ▼
       ┌─────────────┐              ┌─────────────┐              ┌─────────────┐
       │ mpdbackend  │              │ mpdbackend  │              │ mpdbackend  │
       │ :4533       │              │ :4534       │              │ :4535       │
       │ web player  │              │ web player  │              │ web player  │
       └──────┬──────┘              └──────┬──────┘              └──────┬──────┘
              │ 1:1                       │ 1:1                       │ 1:1
              ▼                           ▼                           ▼
       ┌─────────────┐              ┌─────────────┐              ┌─────────────┐
       │  MPD #0     │              │  MPD #1     │              │  MPD #2     │
       │  (Pop)      │              │  (Rock)     │              │  (Chill)    │
       └──────┬──────┘              └──────┬──────┘              └──────┬──────┘
              │ stream_url 0             │ stream_url 1             │ stream_url 2
              ▼                          ▼                          ▼
         HTTP stream                 HTTP stream                 HTTP stream
      (MPD httpd / Icecast)      (MPD httpd / Icecast)      (MPD httpd / Icecast)
              ▲                          ▲                          ▲
              └── audio separate from metadata (mpdbackend = display/control only)
```

| Component | Role |
|-----------|------|
| **`server/`** | **1× per MPD instance:** reads one MPD state, extracts covers, exposes HTTP API and web player |
| **`music_assistant/`** | **1× total:** provider loads `channels.json` and fetches metadata per channel from each `backend_url` |

### One MPD = one mpdbackend

| Scenario | mpdbackend instances |
|----------|----------------------|
| 1 machine, **1 MPD** | **1×** (typical setup) |
| 1 machine, **multiple MPD** (e.g. Pop/Rock/Chill, separate sockets) | **1× per MPD** — different `MPDBACKEND_MPD_SOCKET`, `MPDBACKEND_HTTP_PORT`, own `mpdbackend.env` / systemd unit |
| **Multiple machines**, 1 MPD each | **1× per machine** |

A shared **`channels.json`** (e.g. on the Music Assistant host) lists all stations (`0`, `1`, `2`, …). Each channel uses `stream_url` for **audio** and `backend_url` for the **mpdbackend** tied to that MPD — even when several MPD run on the same host (then e.g. `:4533`, `:4534`, `:4535`).

**Not required:** a separate mpdbackend per browser, listener, or Music Assistant player.

## Features

- Live **title, artist, album** from MPD (not only ICY stream tags)
- **Cover art** from embedded tags (ID3/APIC), ffmpeg video stream, or folder images (`cover.jpg`)
- **Station logos** per channel (`channel_0.png`, `channel_1.png`, …)
- **Dynamic channel list** via `channels.json` (reload without restart)
- **MQTT** publishing and **remote MPD control** (optional, e.g. Home Assistant)
- **Track duration** and **elapsed time** via MQTT (`M:SS` format)
- **Web player** at `http://host:4533/` (responsive for mobile devices): cover, metadata, stream, MPD control, playlist picker
- **HTTP API**: metadata, cover art, control, playlists, mark-for-delete
- Chromecast-friendly metadata updates (playback sessions, image cache warming)

## Project structure

```
mpdbackend/
├── server/                    # Deploy per MPD instance (1× mpdbackend per MPD socket)
│   ├── mpdbackend.py          # MPD worker, channel registry
│   ├── mpdbackend_http.py     # HTTP API
│   ├── mpdbackend_mqtt.py     # MQTT publish + MPD commands
│   ├── mpdbackend_cover.py    # Cover extraction and cache
│   ├── web/                   # Web player (HTML/CSS/JS)
│   ├── home_assistant/        # MQTT integration examples (HA)
│   ├── channels.json.example
│   ├── install/
│   │   ├── install.sh
│   │   └── requirements.txt
│   └── systemd/
└── music_assistant/           # Deploy into Music Assistant
    └── mpdbackend/
        ├── provider.py
        ├── manifest.json
        └── …
```

## Requirements

**Server**

- Python 3.10+
- MPD with Unix socket
- ffmpeg
- Python packages: `Pillow`, `python-mpd2`, `paho-mqtt`

**Music Assistant**

- Music Assistant 2.8+ (2.9 recommended for image proxy)

## Server installation

```bash
cd server
chmod +x install/install.sh
./install/install.sh --systemd
```

Edit configuration:

```bash
nano mpdbackend.env      # MQTT, MPD socket, paths
nano channels.json       # radio channels (copy from channels.json.example)
```

Logos:

```text
data/logos/channel_0.png
data/logos/channel_1.png
```

Start / check:

```bash
sudo systemctl restart mpdbackend
curl http://127.0.0.1:4533/health
curl http://127.0.0.1:4533/channels
```

Web player in the browser: `http://127.0.0.1:4533/`

## Music Assistant provider

Copy the provider into the Music Assistant container (or `custom_components` path), then add the integration in the MA UI.

Example deploy script (from the parent Music Assistant repo):

```bash
docker cp music_assistant/mpdbackend music-assistant:/app/venv/lib/python3.14/site-packages/music_assistant/providers/
docker restart music-assistant
```

In Music Assistant, configure **one backend URL** (default metadata server). Channels with their own `backend_url` in `channels.json` are supported for multi-site setups.

## Channel configuration

`channels.json` defines the radio stations shown in Music Assistant:

```json
{
  "0": {
    "name": "Store Radio",
    "description": "Main floor",
    "stream_url": "https://example.com:8000/stream.mp3",
    "content_type": "mp3",
    "backend_url": "http://mpd-host:4533"
  }
}
```

| Field | Description |
|-------|-------------|
| `name` | Display name in Music Assistant |
| `stream_url` | HTTP/Icecast URL for audio (Music Assistant **and** web player) |
| `content_type` | `mp3`, `aac`, `ogg`, `flac` |
| `backend_url` | Optional; metadata server for this channel |

**Note:** `stream_url` carries **audio**; `backend_url` or `:4533` carries **metadata and control**. The web player needs both.

### Multiple MPD on one machine

Example (see also `server/install/mpd.conf_example`):

| Channel | MPD socket | mpdbackend port | `backend_url` |
|---------|------------|-----------------|---------------|
| `"0"` Pop | `/run/mpd-pop/socket` | `4533` | `http://192.168.1.10:4533` |
| `"1"` Rock | `/run/mpd-rock/socket` | `4534` | `http://192.168.1.10:4534` |
| `"2"` Chill | `/run/mpd-chill/socket` | `4535` | `http://192.168.1.10:4535` |

Per instance: own `mpdbackend.env` (socket, port, paths) and own systemd service — or separate install directories when running manually.

## Configuration (server)

Settings are read from **`mpdbackend.env`** (next to `mpdbackend.py` or `/etc/mpdbackend.env` with systemd). No credentials are hardcoded in the Python source.

Set `MPDBACKEND_MQTT_ENABLED=false` to run HTTP-only (no broker required). Control is then available via **HTTP POST endpoints** (`/cmd/player`, `/cmd/volume`, `/cmd/playlist`, `/cmd/savefile`) and the **web player** — functionally equivalent to the MQTT `cmd/*` topics.

When MQTT is enabled, these are required:

```bash
MPDBACKEND_MQTT_ENABLED=true
MPDBACKEND_MQTT_BROKER=mqtt.example.com
MPDBACKEND_MQTT_USERNAME=user
MPDBACKEND_MQTT_PASSWORD=secret
```

Common options:

| Variable | Purpose |
|----------|---------|
| `MPDBACKEND_MQTT_ENABLED` | Enable MQTT publish and control (default: `true`) |
| `MPDBACKEND_MQTT_TOPIC_*` | Topic names (see [MQTT](#mqtt) below) |
| `MPDBACKEND_MQTT_ELAPSED_INTERVAL` | Elapsed publish interval in seconds (default: `1`) |
| `MPDBACKEND_MPD_SOCKET` | MPD socket (default: `/run/mpd/socket`) |
| `MPDBACKEND_PLAYLIST_DIR` | MPD playlist directory (optional; else from MPD config) |
| `MPDBACKEND_MUSIC_ROOT` | Music library root for cover extraction |
| `MPDBACKEND_COVER_DIR` | Cached cover JPEGs |
| `MPDBACKEND_STATION_LOGO_DIR` | Station logo files |
| `MPDBACKEND_CHANNELS_FILE` | Path to `channels.json` |
| `MPDBACKEND_PUBLIC_BASE_URL` | Public URL of this backend |
| `MPDBACKEND_HTTP_PORT` | HTTP port (default: `4533`) |
| `MPDBACKEND_WEB_DIR` | Web player files (default: `server/web/`) |
| `MPDBACKEND_MARKED_FOR_DELETE` | Target file for mark-for-delete (default: `data/mark_for_delete.cfg`) |

See `server/systemd/mpdbackend.env.example` for the full list.

## Web player

At **`http://host:4533/`** mpdbackend serves a responsive web UI — layout and typography adapt to the caller’s viewport (`clamp`, `vmin`, `dvh`; portrait, landscape, various screen sizes):

- **Display:** cover, title, artist, album, progress (elapsed/duration), track position in playlist
- **Channel:** picker from `channels.json` (logo + `stream_url`)
- **Playlist:** active playlist and picker for all MPD playlists
- **Control:** play/stop, next/back, volume (HTTP → MPD)
- **Stream:** audio via `stream_url` from `channels.json` (separate from metadata)
- **Mark for delete:** red cross → `POST /cmd/savefile` → **appends** MPD file path to `MPDBACKEND_MARKED_FOR_DELETE`

Audio comes from the **HTTP stream** (MPD `httpd`/Icecast); control and metadata from **mpdbackend port 4533**.

### Mark for delete

`POST /cmd/savefile` **appends** to the configured file (default: `data/mark_for_delete.cfg`) — **one line per click** with the MPD file path relative to `MPDBACKEND_MUSIC_ROOT`, e.g.:

```text
Artist/Album/Track.mp3
```

An external job can read this file and process the entry (delete, move, enqueue, etc.).

## HTTP API

| Endpoint | Description |
|----------|-------------|
| `GET /` | Web player (static files from `web/`) |
| `GET /nowplaying` | Current MPD track metadata (JSON, see below) |
| `GET /playlists` | Available MPD playlists and active playlist |
| `GET /cover?name=cover_….jpg` | Cached cover image |
| `GET /stationlogo?channel=0` | Station logo for channel ID |
| `GET /channels` | Channel registry (`channels.json`) |
| `GET /health` | MPD/MQTT connection status |
| `GET /hash` | State hash for change detection |
| `POST /cmd/player` | MPD transport: plain text `play`, `stop`, `next`, `back` |
| `POST /cmd/volume` | Set volume: plain text `0`–`100` |
| `POST /cmd/playlist` | Load and play playlist: plain text e.g. `Pop.m3u` |
| `POST /cmd/savefile` | **Append** current MPD file path to `MPDBACKEND_MARKED_FOR_DELETE` |

**`/nowplaying` response** (example):

```json
{
  "state": "play",
  "title": "Song title",
  "artist": "Artist",
  "album": "Album",
  "songid": "42",
  "duration": 245.0,
  "elapsed": 83.5,
  "cover_name": "cover_a1b2c3.jpg",
  "volume": 45,
  "playlist": "Pop.m3u",
  "pos": 3,
  "playlist_length": 12,
  "file": "Artist/Album/Track.mp3"
}
```

**`/playlists` response** (example):

```json
{
  "playlists": ["Pop.m3u", "Rock.m3u"],
  "active": "Pop.m3u"
}
```

**`POST /cmd/savefile` response** (example):

```json
{
  "ok": true,
  "file": "Artist/Album/Track.mp3",
  "path": "/opt/mpdbackend/data/mark_for_delete.cfg"
}
```

`duration` and `elapsed` are seconds (float). `pos` is 1-based. The Music Assistant provider uses `cover_name` for the image proxy.

## MQTT

When `MPDBACKEND_MQTT_ENABLED=true`, the server publishes status and accepts MPD control commands. Default topic prefix: `mpdbackend/` (all topics configurable via `MPDBACKEND_MQTT_TOPIC_*`).

| Topic | Payload | Description |
|-------|---------|-------------|
| `mpdbackend/state` | JSON, retained | Track metadata: `state`, `title`, `artist`, `album`, `duration` (`M:SS`), `cover_name`, `volume`, `lastloadedplaylist` |
| `mpdbackend/elapsed` | Text, retained | Current position as `M:SS` or `H:MM:SS` (updated every `MPDBACKEND_MQTT_ELAPSED_INTERVAL` s) |
| `mpdbackend/current` | JSON, retained | Queue context: `playlist`, `pos`, `file` |
| `mpdbackend/playlists` | JSON, retained | Available playlists: `playlists` (array) |
| `mpdbackend/cover` | JPEG binary, retained | Current cover image |
| `mpdbackend/connected` | `online` / `offline`, retained | Availability (LWT for Home Assistant) |
| `mpdbackend/cmd/volume` | text subscribe | Set volume: `45` (0–100); `volume` is published on `state` |
| `mpdbackend/cmd/player` | text subscribe | MPD transport: `play`, `stop`, `next`, `back` |
| `mpdbackend/cmd/playlist` | text subscribe | Load playlist (payload = filename) |

**`mpdbackend/state` example:**

```json
{
  "state": "play",
  "title": "Song title",
  "artist": "Artist",
  "album": "Album",
  "duration": "4:05",
  "cover_name": "cover_a1b2c3.jpg",
  "volume": 45,
  "lastloadedplaylist": "Pop.m3u"
}
```

**Control** — publish plain text:

| Topic | Payload |
|-------|---------|
| `mpdbackend/cmd/player` | `play` |
| `mpdbackend/cmd/player` | `stop` |
| `mpdbackend/cmd/player` | `next` |
| `mpdbackend/cmd/player` | `back` |
| `mpdbackend/cmd/playlist` | `Pop.m3u` |

**Set volume** — plain text on `mpdbackend/cmd/volume`:

```
45
```

After loading a playlist, `mpdbackend/current` reports the active playlist name (`playlist`). All available playlists are on `mpdbackend/playlists`.

Home Assistant example configs live under `server/home_assistant/` (`mqtt.yaml_example`, `status_card.example`, …).

## Development

```bash
# Server tests
cd server && pytest tests/

# Provider tests
cd music_assistant && pytest tests/
```

## License

MIT License
