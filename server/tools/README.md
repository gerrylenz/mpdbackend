# delete_marked_files

Batch-Tool zum Löschen von im **mpdbackend-Web-Player** markierten Titeln.

Während der Wiedergabe kann im Web-Player (mit Passwort) ein Titel über das rote Kreuz markiert werden. Der MPD-Dateipfad landet in `mark_for_delete.cfg` auf dem mpdbackend-Server. Dieses Skript holt die Liste per HTTP, löscht die Dateien auf dem Dateisystem und leert die Markierliste auf dem Server.

## Ablauf

```
Web-Player  →  POST /cmd/savefile  →  mark_for_delete.cfg (Server)
                                              ↓
Cron/CLI    →  delete_marked_files.py  →  GET /markfordelete
                                              ↓
                                     Dateien lokal löschen
                                              ↓
                                     POST /markfordelete/clear
```

## Löschlogik

Pro Eintrag in der Markierliste:

1. **Relativpfad** aus MPD auswerten (relativ zu `MUSIC_ROOT`)
2. **Suchwurzel** ermitteln — eine Ebene über dem unmittelbaren Ordner der Datei
3. Ab der Suchwurzel **rekursiv** nach dem **Dateinamen** suchen
4. **Alle Treffer** löschen (optional Cover-Cache mit entfernen)

### Suchwurzel

| Markierter Pfad | Suchwurzel (unter `MUSIC_ROOT`) |
|-----------------|----------------------------------|
| `Artist/Album1/Song.mp3` | `Artist/` |
| `Artist/Song.mp3` | `Artist/` |
| `Song.mp3` | `MUSIC_ROOT` (gesamte Wurzel) |

Regel bei mindestens drei Pfadteilen (`Ordner/…/Datei`): die letzten zwei Teile (Ordner der Datei + Dateiname) werden abgeschnitten — gesucht wird eine Ebene darüber.

### Beispiel: Künstler mit mehreren Alben

Markierung: `Artist/Album1/Song.mp3`

```
MUSIC_ROOT/
  Artist/                    ← Suche startet hier
    Album1/
      Song.mp3               ✓ gelöscht
    Album2/
      Song.mp3               ✓ gelöscht
  Other/
    Song.mp3                 ✗ bleibt (anderer Ordner)
```

### Beispiel: Mix-Ordner (`work/mixen`)

Markierung: `work/mixen/scl_daily/Artist - Titel.mp3`  
`MUSIC_ROOT=/home/musik/alben`

Suchwurzel: `/home/musik/alben/work/mixen/`

Gelöscht werden alle Dateien mit exakt dem Namen `Artist - Titel.mp3` in:

- `scl_daily/`
- `scl_daily_all/`
- `scl_weekly/`
- `scl_weekly_all/`
- `scl_yourmix1/`
- `scl_yourmix1_all/`
- … und allen weiteren Unterordnern von `mixen/`

Nicht gelöscht werden Kopien **außerhalb** von `work/mixen/` (z. B. in `work/anderes/`).

**Hinweis:** Es wird nur der **exakte Dateiname** verglichen — kein Abgleich über Tags oder Dateiinhalt.

## Voraussetzungen

- Python 3.10+
- Laufender **mpdbackend**-Server (HTTP-API)
- `MUSIC_ROOT` muss mit `MPDBACKEND_MUSIC_ROOT` auf dem Server übereinstimmen
- Das Skript läuft auf dem Rechner, der die Musikdateien löschen soll (lokal oder per gemountetem Pfad)
- Optional: `mpc` im PATH, wenn `MPD_UPDATE=true`

## Installation

Das Skript wird mit `install/install.sh` mitinstalliert. Konfiguration anlegen:

```bash
cd server/tools
cp delete_marked_files.env.example delete_marked_files.env
nano delete_marked_files.env
```

Alternativ kann die bestehende Server-Konfiguration wiederverwendet werden:

```bash
python3 delete_marked_files.py --config /etc/mpdbackend.env
```

## Konfiguration

Suchreihenfolge der Konfigurationsdatei:

1. `--config /pfad/…`
2. Umgebungsvariable `DELETE_MARKED_CONFIG`
3. `delete_marked_files.env` (neben dem Skript)
4. `../mpdbackend.env`
5. Eingebaute Defaults

CLI-Argumente **überschreiben** die Datei.

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `URL` | `http://127.0.0.1:4533` | mpdbackend-Basis-URL |
| `CHANNEL` | `0` | Kanal für `?channel=` (Multi-MPD-Proxy) |
| `MUSIC_ROOT` | `/home/musik/alben` | Wurzel der Musikbibliothek |
| `COVER_DIR` | leer | Optional: Cover-Cache mit löschen |
| `PASSWORD` | leer | Web-Passwort (`?password=`), wenn `MPDBACKEND_WEB_PASSWORD` gesetzt |
| `MPD_UPDATE` | `false` | Nach erfolgreichem Lauf `mpc update` ausführen |
| `KEEP_LIST_ON_ERROR` | `false` | Bei Fehlern Markierliste auf dem Server behalten |
| `DRY_RUN` | `false` | Nur anzeigen, nichts löschen |

### Aliase aus `mpdbackend.env`

| mpdbackend.env | Wird übernommen als |
|----------------|---------------------|
| `MPDBACKEND_PUBLIC_BASE_URL` | `URL` |
| `MPDBACKEND_MUSIC_ROOT` | `MUSIC_ROOT` |
| `MPDBACKEND_COVER_DIR` | `COVER_DIR` |
| `MPDBACKEND_WEB_PASSWORD` | `PASSWORD` |


## Verwendung

```bash
# Erst testen (empfohlen)
python3 delete_marked_files.py --dry-run

# Mit eigener Konfiguration
python3 delete_marked_files.py --config delete_marked_files.env

# Einzelne Werte überschreiben
python3 delete_marked_files.py --music-root /home/musik/alben --mpd-update
```

### CLI-Optionen

| Option | Beschreibung |
|--------|--------------|
| `--config` | Pfad zur Konfigurationsdatei |
| `--url` | mpdbackend-URL |
| `--music-root` | Musikbibliothek |
| `--channel` | Kanal-ID für Multi-MPD |
| `--password` | Web-Passwort |
| `--cover-dir` | Cover-Cache-Verzeichnis |
| `--mpd-update` | MPD-Datenbank aktualisieren |
| `--keep-list-on-error` | Markierliste bei Fehlern nicht leeren |
| `--dry-run` | Simulation ohne Löschen |

## Exit-Codes

| Code | Bedeutung |
|------|-----------|
| `0` | Erfolg |
| `1` | Laufzeitfehler (HTTP, Löschen, Clear) |
| `2` | Konfigurations- oder Pfadfehler |

## Verhalten bei Fehlern

| Situation | Verhalten |
|-----------|-----------|
| Ungültiger Pfad (`..`, außerhalb `MUSIC_ROOT`) | Fehler, zählt zu `errors` |
| Keine Datei gefunden | `missing`, Markierliste wird **trotzdem** geleert |
| `unlink` schlägt fehl | `errors`, Abbruch mit Code `1` |
| `errors` + `--keep-list-on-error` | Markierliste bleibt erhalten |
| Leere Markierliste | Nur `clear` auf dem Server |

## Cron / systemd timer

Beispiel (täglich 03:00):

```cron
0 3 * * * /opt/mpdbackend/venv/bin/python /opt/mpdbackend/tools/delete_marked_files.py >> /var/log/delete_marked_files.log 2>&1
```

Vor dem ersten produktiven Lauf immer `--dry-run` prüfen.

## Beispiel-Ausgabe

```
2026-07-06 03:00:01 === delete_marked_files ===
2026-07-06 03:00:01 Konfiguration: /opt/mpdbackend/tools/delete_marked_files.env
2026-07-06 03:00:01 Musik-Wurzel: /home/musik/alben
2026-07-06 03:00:01 Lade Markierliste von: http://127.0.0.1:4533/markfordelete?channel=0
2026-07-06 03:00:01 Markierliste enthält 1 Eintrag/Einträge
2026-07-06 03:00:01 [1/1] Löschen: work/mixen/scl_daily/Artist - Titel.mp3
2026-07-06 03:00:01 [1/1] Suche ab: /home/musik/alben/work/mixen (Dateiname: Artist - Titel.mp3)
2026-07-06 03:00:01 [1/1] Ziel: /home/musik/alben/work/mixen/scl_daily/Artist - Titel.mp3
2026-07-06 03:00:01 [1/1] gelöscht: ...
2026-07-06 03:00:01 [1/1] Ziel: /home/musik/alben/work/mixen/scl_daily_all/Artist - Titel.mp3
2026-07-06 03:00:01 [1/1] gelöscht: ...
```

## Tests

```bash
cd server
pytest tests/test_delete_marked_files_config.py tests/test_delete_marked_files_search.py
```

## Siehe auch

- [Haupt-README](../../README.md) — Web-Player, Mark-for-delete, HTTP-API
- `delete_marked_files.env.example` — Konfigurationsvorlage
