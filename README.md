# MusicScraper

A desktop music discovery app. It learns your taste from genres, bands, songs and your current
library, asks a local AI (Ollama) for albums you'd like, checks each one is real on MusicBrainz,
and queues the best copy it can find in Soulseek through [slskd](https://github.com/slskd/slskd).

```
taste + library scan ─► Ollama suggestions ─► MusicBrainz check ─► skip owned / seen
                                                      │
                     slskd queue ◄─ best folder ◄─ Soulseek search
                     (FLAC first, 320 MP3 fallback, free slot, short queue)
```

## Setup

1. **slskd** (Soulseek client, runs in Docker):
   ```bash
   cp .env.example .env                      # set MUSIC_DIR and DOWNLOAD_DIR
   cp slskd/slskd.example.yml slskd/slskd.yml  # Soulseek login + API key
   docker compose up -d
   ```
   Open http://localhost:5030 to confirm it's logged in. Put `DOWNLOAD_DIR` inside your
   music folder so new downloads count as owned on the next library scan.

2. **Ollama**: any model works; general-purpose models know more about music than coder models.
   ```bash
   ollama pull qwen3:30b   # or qwen3:14b, gemma3:12b, ...
   ```

3. **The app**:
   ```bash
   cp config.example.toml config.toml   # library paths, model, slskd API key, limits
   uv run musicscraper                  # opens http://127.0.0.1:8765
   ```

## Using it

- Fill in your genres, bands and songs, then **Save taste**.
- **Rescan library** to read your collection (tags, or the Artist/Album folder layout if files are untagged).
- **Discover & queue** runs one round. Each run queues at most `max_albums_per_run` albums.
  Set `[schedule].interval_hours` to run automatically while the app is open.
- **History** lists every suggestion and what happened to it:
  `queued`, `not_found` (nobody sharing a good copy; retried after 30 days),
  `rejected` (not on MusicBrainz, so the AI probably made it up) or `error`.
  ✕ forgets a suggestion so it can come up again.

Data lives in `data/musicscraper.db` (SQLite).
