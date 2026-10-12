"""Pick the best album folder from slskd search responses."""

import re
from dataclasses import dataclass, field

from .text import token_coverage, tokens

# Words in folder names that describe the release/format rather than name it.
NOISE = {"ep", "lp", "album", "the", "a", "cd", "cd1", "cd2", "disc", "vinyl", "web", "flac", "mp3", "320",
         "v0", "16", "24", "bit", "khz", "44", "48", "96", "1", "remaster", "remastered", "deluxe", "edition",
         "expanded", "reissue", "lossless", "hi", "res", "kbps", "cbr", "vbr", "and", "of", "by"}


def _album_match(artist: str, album: str, folder: str, text: str) -> float:
    """How well a folder matches the album, without letting the artist's name count as the title."""
    artist_t = set(tokens(artist))
    title = [t for t in tokens(album) if t not in artist_t and t not in NOISE]
    if title:
        have_folder, have_text = set(tokens(folder)), set(tokens(text))
        folder_cov = sum(t in have_folder for t in title) / len(title)
        text_cov = sum(t in have_text for t in title) / len(title)
        return max(folder_cov, text_cov * 0.8)
    # Self-titled release: the folder must not name some other title.
    leaf = re.split(r"[\\/]", folder)[-1]
    extra = [t for t in tokens(leaf) if t not in artist_t and t not in NOISE and not re.fullmatch(r"\d{4}", t)]
    return 1.0 if not extra else 0.0

AUDIO = {"flac", "mp3", "m4a", "ogg", "opus", "wav", "aiff", "alac", "ape", "wv", "wma"}
ARTWORK = {"jpg", "jpeg", "png"}
MAX_ARTWORK_BYTES = 15 * 1024 * 1024


@dataclass
class Candidate:
    username: str
    folder: str
    format: str
    files: list[dict]
    score: float
    detail: dict = field(default_factory=dict)


def _split(filename: str) -> tuple[str, str]:
    parts = re.split(r"[\\/]", filename)
    return "\\".join(parts[:-1]), parts[-1]


def _ext(f: dict) -> str:
    ext = (f.get("extension") or "").lower().lstrip(".")
    if not ext:
        ext = f["filename"].rsplit(".", 1)[-1].lower() if "." in f["filename"] else ""
    return ext


def _kbps(f: dict) -> float:
    if f.get("bitRate"):
        return float(f["bitRate"])
    if f.get("length") and f.get("size"):
        return f["size"] * 8 / f["length"] / 1000
    return 0.0


def _pick_format(audio: list[dict], cfg: dict) -> tuple[str, list[dict]] | None:
    prefer = cfg["prefer"]
    flac = [f for f in audio if _ext(f) == "flac"]
    mp3 = [f for f in audio if _ext(f) == "mp3"]
    min_rate = cfg["min_mp3_bitrate"] * 0.95  # allow for rounding in reported/derived bitrates
    if prefer != "mp3_only" and len(flac) >= cfg["min_tracks"]:
        return "flac", flac
    if prefer != "flac_only" and len(mp3) >= cfg["min_tracks"] and all(_kbps(f) >= min_rate for f in mp3):
        return "mp3", mp3
    return None


def pick_best(responses: list[dict], artist: str, album: str, cfg: dict,
              exclude_users: set[str] = frozenset()) -> Candidate | None:
    best: Candidate | None = None
    for resp in responses:
        if resp.get("username") in exclude_users:
            continue
        folders: dict[str, list[dict]] = {}
        for f in resp.get("files", []):
            folder, _ = _split(f["filename"])
            folders.setdefault(folder, []).append(f)

        for folder, files in folders.items():
            audio = [f for f in files if _ext(f) in AUDIO]
            if not audio or len(audio) > cfg["max_tracks"]:
                continue
            picked = _pick_format(audio, cfg)
            if not picked:
                continue
            fmt, chosen = picked

            text = folder + " " + " ".join(_split(f["filename"])[1] for f in chosen)
            album_match = _album_match(artist, album, folder, text)
            artist_match = token_coverage(artist, text)
            if album_match < 0.6 or artist_match < 0.5:
                continue

            score = (100 if fmt == "flac" and cfg["prefer"] == "flac" else 60)
            score += album_match * 30 + artist_match * 10
            score += 20 if resp.get("hasFreeUploadSlot") else 0
            score -= min(resp.get("queueLength", 0), 50) * 0.5
            score += min(resp.get("uploadSpeed", 0) / 1_000_000, 10) * 2
            score += min(len(chosen), 20) * 0.5

            if best is None or score > best.score:
                extra = []
                if cfg["include_artwork"]:
                    extra = [f for f in files if _ext(f) in ARTWORK and f.get("size", 0) <= MAX_ARTWORK_BYTES]
                best = Candidate(
                    username=resp["username"], folder=folder, format=fmt,
                    files=chosen + extra, score=round(score, 1),
                    detail={"tracks": len(chosen), "free_slot": resp.get("hasFreeUploadSlot"),
                            "queue": resp.get("queueLength", 0)},
                )
    return best
