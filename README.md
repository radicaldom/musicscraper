# MusicScraper

A self-hosted music discovery app. Tell it what you like (or let it read your library), and it
asks a **local LLM** running in [Ollama](https://ollama.com) for albums you'd probably enjoy. It
checks each suggestion against **MusicBrainz** so made-up albums get dropped, skips anything you
already own, then finds the best copy on **Soulseek** through [slskd](https://github.com/slskd/slskd)
and queues the download.

All of it runs on your machine: no cloud AI, no accounts apart from Soulseek.

```
 your taste ─┐
             ├─► Ollama suggests albums ─► MusicBrainz verifies ─► skip owned / already seen
 library ────┘                                                              │
                                                                            ▼
 renamed folder ◄─ download tracked ◄─ slskd queue ◄─ best folder ◄─ Soulseek search
 (retried from another peer if it fails)       (FLAC first, 320 kbps MP3 fallback,
                                                free slot, short queue, fast peer)
```

## Features

- **Taste profile.** Combines genres, artists, songs, dislikes and free-text notes that you enter
  with the artists and genres it finds most often in your library.
- **Library scan.** Reads tags with [mutagen](https://mutagen.readthedocs.io). If files are
  untagged, it guesses artist and album from folder names such as `Artist/Album`,
  `Artist - Album (1999) [FLAC]` or `Artist - 1978 - Album`.
- **Guided runs.** Type a band name to get suggestions that sound like that band. If you own none
  of that band's albums, it first queues one of their best studio albums (taken from MusicBrainz
  and ranked by the LLM).
- **Hallucination filter.** Every suggestion is looked up on MusicBrainz and renamed to its
  official artist and album title. Suggestions that don't exist are marked `rejected`.
- **Folder picker.** Scores each folder in the search results by format, how well its name
  matches, free upload slot, peer queue length, upload speed and track count. Cover art is
  included.
- **Download tracking.** Every 20 seconds it reads download status from slskd. Failed downloads
  are queued again from a different peer, up to `max_retries` times.
- **Folder renaming.** When an album finishes, its folder is renamed using a template such as
  `Artist - Album - 1999`. Names are made safe for SMB, Windows and macOS shares.
- **Scheduling.** It can run discovery every N hours while the app is open.
- **Live config.** Edits to `config.toml` apply on the next run without a restart. The `[server]`
  section is the exception.

## Requirements

| What | Why |
|------|-----|
| Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/) | Runs the app |
| [Ollama](https://ollama.com) and a general-purpose model | Generates the suggestions |
| Docker + Docker Compose | Runs slskd |
| A Soulseek account | slskd creates one the first time it logs in with a new username |
| Internet access to musicbrainz.org | Checks that suggestions exist (can be turned off) |

## Setup

### 1. slskd (Soulseek client)

```bash
cp .env.example .env                         # set MUSIC_DIR and DOWNLOAD_DIR
cp slskd/slskd.example.yml slskd/slskd.yml   # Soulseek login, web login, API key
docker compose up -d
```

- `MUSIC_DIR` is your library. slskd shares it read-only, because many Soulseek users won't
  upload to people who share nothing.
- `DOWNLOAD_DIR` is where finished downloads go. **Put it inside your music folder** so the next
  library scan counts new albums as owned.
- Both must be **local paths**. Docker can't mount `smb://` URLs directly, so mount a network
  share first (for example with `/etc/fstab` or `mount -t cifs`) and give Docker the mount point.
- In `slskd.yml`, set the `musicscraper` API key to a long random string, for example from
  `openssl rand -hex 32`. You'll use the same value in `config.toml`.
- For better results, forward port **50300** on your router.

Open http://localhost:5030 and check that slskd is logged in to Soulseek.

### 2. Ollama

```bash
ollama pull qwen3:30b      # or qwen3:14b (fits in 16 GB VRAM), gemma3:12b, ...
```

General-purpose models know much more about music than coder models do. Bigger models
hallucinate less, but MusicBrainz catches most mistakes either way.

### 3. MusicScraper

```bash
cp config.example.toml config.toml
# edit: [library].paths, [ollama].model, [slskd].api_key, [downloads].local_dir
uv run musicscraper
```

This opens the web UI at http://127.0.0.1:8765. The status bar shows whether the config file,
Ollama, the model and slskd are all reachable.

## Using it

1. **Your taste**: enter genres, bands, songs and things to avoid, one per line, plus optional
   notes such as *"more 90s stuff, nothing with harsh vocals"*. Then click **Save taste**.
2. **Library**: click **Rescan library** to index the folders in `[library].paths`. Large
   libraries on a NAS can take a few minutes.
3. **Discover & queue**: starts one run. To steer it, type a band in the *Guide this run* box
   first. A run queues at most `max_albums_per_run` albums. It asks the model for extra
   suggestions (`overfetch`) and makes up to `max_rounds` attempts, because many suggestions get
   filtered out.
4. **Activity** shows the live log of the current run.
5. **Downloads** shows each queued folder in slskd with its progress and file states.
6. **History** lists every suggestion with the AI's reason and what happened to it:

| Status | Meaning |
|--------|---------|
| `queued` | Sent to slskd and waiting for the peer |
| `downloading` | Transfer in progress |
| `completed` | All files downloaded; folder renamed if that's enabled |
| `retrying` | Some files failed; it will try another peer |
| `failed` | All retries used up, or no other peer had a good copy |
| `not_found` | Nobody was sharing a good enough copy. It's tried again after 30 days |
| `rejected` | Not found on MusicBrainz, so the AI probably made it up |
| `error` | slskd returned an error. It can be suggested again on the next run |

Click **✕** on a row to forget that suggestion so it can come up again.

## Configuration

Everything is in `config.toml`. Comments in [`config.example.toml`](config.example.toml) explain
each option. The most important ones:

| Key | Default | Notes |
|-----|---------|-------|
| `library.paths` | `[]` | Folders to scan |
| `ollama.model` | `qwen3:30b` | Must already be pulled |
| `ollama.think` | `false` | Reasoning mode for qwen3 / deepseek-r1. Off is much faster |
| `ai.new_artists_only` | `true` | `false` also suggests missing albums by artists you own |
| `ai.overfetch` / `ai.max_rounds` | `3` / `3` | How hard each run tries to reach its quota |
| `musicbrainz.contact` | — | Email or URL. MusicBrainz asks for one in the User-Agent |
| `slskd.api_key` | — | Must match the key in `slskd/slskd.yml` |
| `downloads.max_albums_per_run` | `5` | |
| `downloads.prefer` | `flac` | `flac` (MP3 as fallback), `flac_only` or `mp3_only` |
| `downloads.min_mp3_bitrate` | `320` | MP3 folders below this are skipped |
| `downloads.min_tracks` / `max_tracks` | `4` / `40` | Skips singles and huge discographies |
| `downloads.max_retries` | `2` | Number of other peers to try after a failed download |
| `downloads.local_dir` | — | Where `DOWNLOAD_DIR` is on *this* machine. Needed for renaming |
| `downloads.folder_template` | `{artist} - {album} - {year}` | ` - {year}` is dropped when the year is unknown |
| `schedule.interval_hours` | `0` | `0` means runs only start when you click |

Environment variable overrides:

- `MUSICSCRAPER_CONFIG`: path to the config file (default `./config.toml`)
- `MUSICSCRAPER_DATA`: data directory (default `./data`)

## Data

Everything is stored in SQLite at `data/musicscraper.db`:

- `kv`: your saved taste profile
- `library`: the result of the last library scan
- `recs`: every suggestion, its status and where it was downloaded from
- `runs`: one row per discovery run

New columns are added automatically when the app starts. To start over, delete the file.

## Project layout

```
musicscraper/
  main.py         FastAPI app, REST API, scheduler and download-tracker loops
  pipeline.py     One discovery run: ask → verify → filter → search → queue
  llm.py          Ollama prompts with structured JSON output
  musicbrainz.py  Rate-limited MusicBrainz lookups (≈1 req/s)
  slskd.py        Small async slskd REST client
  matcher.py      Scores folders in search results and picks the best one
  tracking.py     Syncs download status, retries failures, renames finished folders
  library.py      Library scanner (tags first, folder names as fallback)
  taste.py        Builds the plain-text taste profile sent to the LLM
  text.py         Name normalisation and fuzzy matching
  db.py           SQLite wrapper and schema
  config.py       Loads config.toml on top of the defaults
  static/         Single-page web UI
```

### API

The UI uses a small JSON API, which you can also call from scripts:

| Method | Path | |
|--------|------|-|
| `GET` / `PUT` | `/api/taste` | Read or save the taste profile |
| `GET` | `/api/library` | Library stats, top artists and genres, scan state |
| `POST` | `/api/library/scan` | Start a library scan |
| `POST` | `/api/run` | Start a discovery run. Body: `{"guide": "Band name"}` (optional) |
| `GET` | `/api/status` | Whether a run is active, the recent log and the last run |
| `GET` | `/api/recs?status=` | Suggestion history, optionally filtered by status |
| `DELETE` | `/api/recs/{id}` | Forget a suggestion |
| `GET` | `/api/downloads` | Current slskd downloads with progress |
| `GET` | `/api/health` | Checks the config file, Ollama, the model and slskd |

Interactive docs are at http://127.0.0.1:8765/docs.

## Troubleshooting

- **"slskd is not logged in"**: check the Soulseek credentials in `slskd/slskd.yml` and run
  `docker logs slskd`.
- **"slskd rejected the API key"**: `[slskd].api_key` doesn't match `web.authentication.api_keys`
  in `slskd.yml`.
- **"model not pulled"**: run `ollama pull <model>`, or change `[ollama].model` to a model listed
  in the message.
- **Lots of `not_found`**: the album may be rare, or your filters may be too strict. Try
  `prefer = "flac"` instead of `flac_only`, lower `min_tracks`, or raise
  `slskd.search_timeout_s`.
- **Lots of `rejected`**: use a bigger or general-purpose model, or lower `ollama.temperature`.
- **Downloaded albums suggested again**: make sure `DOWNLOAD_DIR` is inside a folder listed in
  `[library].paths`, then rescan.
- **Folders not renamed**: set `downloads.local_dir` to the download folder's path on the machine
  running MusicScraper.

## A note on Soulseek

Soulseek is a peer-to-peer network. Only download music you have the right to, and share your own
library in return.
