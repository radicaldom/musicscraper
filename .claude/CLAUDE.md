# MusicScraper: notes for Claude

FastAPI app that asks a local Ollama model for album suggestions, checks them against
MusicBrainz, and queues the best Soulseek copy through slskd. See `README.md` for what users
see. This file covers what you need to know to change the code.

## Commands

```bash
uv run musicscraper                       # start the app (http://127.0.0.1:8765, opens a browser)
uv run python -m compileall -q musicscraper   # quick syntax check; there are no tests or linters yet
docker compose up -d                      # slskd (needs .env and slskd/slskd.yml)
```

Importing `musicscraper.main` creates `data/musicscraper.db` as a side effect.

## Architecture

- `main.py`: FastAPI routes, plus two background loops started in `lifespan`:
  - `_scheduler`: starts a run every `schedule.interval_hours`.
  - `_tracker`: every 20 s it calls `tracking.sync`, then `retry_failed`, then `organise`.
- `pipeline.Runner`: one discovery run as an asyncio task. It sends log lines to the UI through
  `emit()`. Order: guide band (optional) → LLM rounds → `_process` for each suggestion
  (block check → MusicBrainz verify → block check again on the canonical names → slskd search →
  `matcher.pick_best` → enqueue → `_record`).
- Library scans run in a plain thread (`threading.Thread`), not on the event loop.
- `static/index.html` is the whole UI: vanilla JS, no build step. It polls `/api/status` every 2 s.

## Conventions and gotchas

- **Config is reloaded on every use** with `config.load()`, which is how edits apply without a
  restart. Don't cache the config at module level. When you add a key, also add it to `DEFAULTS`
  in `config.py` and to `config.example.toml`, with a comment.
- **Schema changes**: add new columns to `DB._add_columns(...)` in `db.py`, not only to `SCHEMA`.
  `CREATE TABLE IF NOT EXISTS` won't change databases that already exist.
- **Name matching**: always compare `text.norm()` keys (`artist_key`, `album_key`), never raw
  names. `norm` removes accents, punctuation, bracketed suffixes and a leading "the".
- **Status lifecycle** for `recs.status`:
  `queued → downloading → completed`, or `→ retrying → queued` (another peer), ending in `failed`.
  A suggestion can also be `not_found`, `rejected` or `error` when the run happens.
  When rows count as "seen" (see `_discover`): `error` and `failed` can come back right away,
  `not_found` after 30 days, and everything else never.
- **MusicBrainz** allows about 1 request per second. `MusicBrainz._get` enforces 1.1 s between
  calls with a lock, so use one client per run. Always send the contact in the User-Agent.
- **slskd** uses REST API v0 with an `X-API-Key` header. Remote paths use backslashes. Downloads
  are matched back to rows by `(username, directory)`. `organise` assumes slskd saves to
  `<downloads>/<last remote folder name>`.
- **Ollama** uses `/api/chat` with a JSON-schema `format` for structured output. If the model
  rejects the `think` option with a 400, the call is retried without it, and `<think>` blocks are
  stripped. Keep this behaviour in any new LLM call.
- Code style: short modules with a one-line module docstring, type hints, f-strings, few
  comments (only where the reason isn't obvious), no classes unless they hold state.
  Match this style.

## Privacy

`config.toml`, `.env`, `slskd/slskd.yml`, `data/` and `downloads/` are gitignored and hold the
user's own paths, hosts and credentials. Never copy their values into tracked files, docs or
commit messages. Use the placeholders from the `*.example` files instead.
