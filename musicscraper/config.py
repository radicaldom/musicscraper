import copy
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("MUSICSCRAPER_CONFIG", ROOT / "config.toml"))
DATA_DIR = Path(os.environ.get("MUSICSCRAPER_DATA", ROOT / "data"))

DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 8765, "open_browser": True},
    "library": {"paths": []},
    "ollama": {
        "url": "http://localhost:11434",
        "model": "qwen3:30b",
        "think": False,
        "temperature": 0.8,
        "num_ctx": 8192,
        "timeout_s": 600,
    },
    "ai": {"overfetch": 3, "max_rounds": 3, "new_artists_only": True},
    "musicbrainz": {"enabled": True, "contact": "https://github.com/you/musicscraper"},
    "slskd": {
        "url": "http://localhost:5030",
        "api_key": "",
        "search_timeout_s": 20,
        "max_peer_queue": 50,
    },
    "downloads": {
        "max_albums_per_run": 5,
        "prefer": "flac",
        "min_mp3_bitrate": 320,
        "min_tracks": 4,
        "max_tracks": 40,
        "include_artwork": True,
        "max_retries": 2,
        "local_dir": "",
        "rename_folders": True,
        "folder_template": "{artist} - {album} - {year}",
    },
    "schedule": {"interval_hours": 0},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load() -> dict:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            return _merge(DEFAULTS, tomllib.load(f))
    return copy.deepcopy(DEFAULTS)
