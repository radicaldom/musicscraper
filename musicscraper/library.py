"""Scan local music folders into (artist, album) rows.

Reads tags from up to two files per directory (albums almost always live in one folder),
falling back to the Artist/Album folder layout when files are untagged.
"""

import os
import re
from collections import Counter
from pathlib import Path

import mutagen

from .text import norm, token_coverage

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".wma", ".ape", ".wv"}
SAMPLE_FILES_PER_DIR = 2


def _first(tags, *keys) -> str:
    for key in keys:
        value = tags.get(key) if tags else None
        if value:
            return str(value[0]).strip()
    return ""


def _read_tags(path: Path) -> tuple[str, str, str]:
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        return "", "", ""
    tags = f.tags if f is not None else None
    return (
        _first(tags, "albumartist", "artist"),
        _first(tags, "album"),
        _first(tags, "genre"),
    )


def _guess_from_folder(d: Path) -> tuple[str, str]:
    """Untagged files: guess from folder names.

    Handles 'Artist - Album (year) [FLAC]', 'Artist - 1978 - Album',
    '! Albums - Genre_Artist_Artist - Album' and plain Artist/Album layouts.
    """
    name = re.sub(r"\s*[\(\[\{].*$", "", d.name).strip()
    if " - " not in name:
        return d.parent.name, d.name
    artist, album = (part.strip() for part in name.split(" - ", 1))
    if not artist[:1].isalnum():  # a label like '! Albums', not an artist
        if album.count("_") >= 2:
            _genre, artist, album = (part.strip() for part in album.split("_", 2))
        else:
            artist = d.parent.name
    # Drop leading 'Artist - ' or 'YYYY - ' segments left in the album part.
    while " - " in album:
        head, rest = (part.strip() for part in album.split(" - ", 1))
        if re.fullmatch(r"\d{4}", head) or token_coverage(head, artist) >= 0.5 or token_coverage(artist, head) >= 0.5:
            album = rest
        else:
            break
    return artist, album


def scan(paths: list[str], progress=None) -> list[dict]:
    albums: dict[tuple[str, str], dict] = {}
    for base in paths:
        base_path = Path(base).expanduser()
        if not base_path.is_dir():
            if progress:
                progress(f"Library path not found: {base_path}")
            continue
        for dirpath, _dirs, files in os.walk(base_path):
            audio = sorted(f for f in files if Path(f).suffix.lower() in AUDIO_EXTS)
            if not audio:
                continue
            d = Path(dirpath)
            artists, titles, genres = Counter(), Counter(), Counter()
            for name in audio[:SAMPLE_FILES_PER_DIR]:
                artist, album, genre = _read_tags(d / name)
                if artist:
                    artists[artist] += 1
                if album:
                    titles[album] += 1
                if genre:
                    genres[genre] += 1
            guess_artist, guess_album = _guess_from_folder(d)
            artist = artists.most_common(1)[0][0] if artists else guess_artist
            album = titles.most_common(1)[0][0] if titles else guess_album
            key = (norm(artist), norm(album))
            if not key[0] or not key[1]:
                continue
            row = albums.setdefault(key, {
                "artist_key": key[0], "album_key": key[1], "artist": artist, "album": album,
                "genres": "", "tracks": 0, "path": str(d),
            })
            row["tracks"] += len(audio)
            if genres and not row["genres"]:
                row["genres"] = "; ".join(g for g, _ in genres.most_common())
            if progress and len(albums) % 200 == 0:
                progress(f"Scanned {len(albums)} albums…")
    return list(albums.values())
