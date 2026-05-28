# MPD Backend

**English** · [Deutsch](README.de.md)

Bridge between **MPD** (Music Player Daemon) and **[Music Assistant](https://music-assistant.io/)**.  
MPD plays local files or playlists; an Icecast/HTTP stream broadcasts the audio. This project supplies **live metadata, cover art, and station logos** so Music Assistant and players (Chromecast, Sonos, etc.) show what is actually playing on MPD — not generic radio tags from the stream.

## Architecture

Multi-site setup: **one mpdbackend server per MPD host**. Music Assistant loads all channels from a central `channels.json`; each channel references its own stream and metadata backend.

```
                         ┌─────────────────────────────────────────────────────────────┐
                         │              Music Assistant (one instance)               │
                         │  ┌─────────────────────────────────────────────────────┐  │
                         │  │           music_assistant/ provider                 │  │
                         │  │  channels.json  →  browse / play / metadata sync    │  │
                         │  └───────────┬─────────────────────┬───────────────────┘  │
                         └──────────────┼─────────────────────┼──────────────────────┘
                                        │                     │
                           stream_url   │                     │  backend_url
                           (playback)   │                     │  /nowplaying, /cover
                                        │                     │
          ┌─────────────────────────────┼─────────────────────┼─────────────────────────────┐
          │                             │                     │                             │
          ▼                             ▼                     ▼                             ▼
   ┌──────────────┐              ┌──────────────┐      ┌──────────────┐              ┌──────────┐
   │  Icecast /   │              │ mpdbackend   │      │  Icecast /   │              │mpdbackend│
   │  HTTP stream │◄─────────────│  server :4533│      │  HTTP stream │◄─────────────│server    │
   │  (channel 0) │   local      │  (site A)    │      │  (channel 1) │   local      │(site B)  │
   └──────▲───────┘   broadcast  └──────▲───────┘      └──────▲───────┘   broadcast  └────▲─────┘
          │                             │                     │                             │
          │                             │ idle/events         │                             │
          │                      ┌──────┴───────┐        ┌──────┴───────┐                      │
          │                      │     MPD      │        │     MPD      │                      │
          │                      │   site A     │        │   site B     │                      │
          │                      └──────────────┘        └──────────────┘                      │
          │         Store / POS ························· Branch 2 ····························│
          └─────────────────────────────────────────────────────────────────────────────────────┘

                                        ▼
                                 Chromecast / Sonos / …
```

| Component | Role |
|-----------|------|
| **`server/`** | One instance **per MPD host**: reads local MPD state, extracts covers, exposes HTTP API (`/nowplaying`, `/cover`, …) |
| **`music_assistant/`** | Single provider in MA: reads **`channels.json`**, plays each `stream_url`, fetches metadata from each channel's `backend_url` |
| **`channels.json`** | Central channel registry (same file on each backend, or one copy on MA side via `/channels` of the default backend) |

Example: channel `0` → stream from site A, metadata from `http://site-a:4533`; channel `1` → stream from site B, metadata from `http://site-b:4533`. Each mpdbackend only talks to **its local MPD**; MA merges everything into one radio library.

## Features

- Live **title, artist, album** from MPD (not only ICY stream tags)
- **Cover art** from embedded tags (ID3/APIC), ffmpeg video stream, or folder images (`cover.jpg`)
- **Station logos** per channel (`channel_0.png`, `channel_1.png`, …)
- **Dynamic channel list** via `channels.json` (reload without restart)
- **MQTT** state publishing (optional)
- **HTTP API**: `/nowplaying`, `/cover`, `/stationlogo`, `/channels`, `/health`
- Chromecast-friendly metadata updates (playback sessions, image cache warming)

## Project structure

```
mpdbackend/
├── server/                    # Deploy on each MPD / workplayer host
│   ├── mpdbackend.py
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
| `stream_url` | HTTP/Icecast URL played by MA |
| `content_type` | `mp3`, `aac`, `ogg`, `flac` |
| `backend_url` | Optional; metadata server for this channel |

## Configuration (server)

Settings are read from **`mpdbackend.env`** (next to `mpdbackend.py` or `/etc/mpdbackend.env` with systemd). No credentials are hardcoded in the Python source.

Required:

```bash
MPDBACKEND_MQTT_BROKER=mqtt.example.com
MPDBACKEND_MQTT_USERNAME=user
MPDBACKEND_MQTT_PASSWORD=secret
```

Common options:

| Variable | Purpose |
|----------|---------|
| `MPDBACKEND_MPD_SOCKET` | MPD socket (default: `/run/mpd/socket`) |
| `MPDBACKEND_MUSIC_ROOT` | Music library root for cover extraction |
| `MPDBACKEND_COVER_DIR` | Cached cover JPEGs |
| `MPDBACKEND_STATION_LOGO_DIR` | Station logo files |
| `MPDBACKEND_CHANNELS_FILE` | Path to `channels.json` |
| `MPDBACKEND_PUBLIC_BASE_URL` | Public URL of this backend |
| `MPDBACKEND_HTTP_PORT` | HTTP port (default: `4533`) |

See `server/systemd/mpdbackend.env.example` for the full list.

## HTTP API

| Endpoint | Description |
|----------|-------------|
| `GET /nowplaying` | Current MPD track metadata (JSON) |
| `GET /cover?name=cover_….jpg` | Cached cover image |
| `GET /stationlogo?channel=0` | Station logo for channel ID |
| `GET /channels` | Channel registry (`channels.json`) |
| `GET /health` | MPD/MQTT connection status |
| `GET /hash` | State hash for change detection |

## Development

```bash
# Server tests
cd server && pytest tests/

# Provider tests
cd music_assistant && pytest tests/
```

## License

Private / use at your own discretion. Adjust before publishing if needed.
