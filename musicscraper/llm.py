"""Album recommendations from a local Ollama model, using structured JSON output."""

import json
import re

import httpx

from .text import norm

SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "artist": {"type": "string"},
                    "album": {"type": "string"},
                    "year": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["artist", "album", "reason"],
            },
        }
    },
    "required": ["recommendations"],
}

SYSTEM = (
    "You are an expert music curator with encyclopedic knowledge of artists, albums, labels "
    "and scenes across every genre and era. You only recommend real, released albums and you "
    "give their exact official titles. Never invent artists or albums; if unsure, pick "
    "something you are certain exists. Respond only with JSON."
)


def _prompt(profile: str, n: int, exclude: list[str], new_artists_only: bool, guide: str = "") -> str:
    parts = ["Here is a listener's taste profile:", profile or "(no profile yet)", ""]
    if guide:
        parts += [
            f"For this session they want music similar to: {guide}",
            f"Recommend {n} albums by OTHER artists that sound like that band — same scene, era, "
            "sound or influences. Use the taste profile only as background to break ties.",
        ]
    else:
        parts += [
            f"Recommend {n} albums they would very likely love but do not own yet.",
            "Mix close matches to their taste with a few more adventurous picks from adjacent scenes.",
        ]
    parts += [
        "Prefer full-length studio albums, use at most two albums per artist, and give each "
        "a one-sentence reason that ties it to their taste.",
    ]
    if new_artists_only:
        parts.append("Only recommend artists that are NOT listed in their profile or library.")
    if exclude:
        parts += ["", "Do not recommend any of these (already owned or already suggested):"]
        parts += [f"- {e}" for e in exclude]
    return "\n".join(parts)


async def recommend(cfg: dict, profile: str, n: int, exclude: list[str], new_artists_only: bool,
                    guide: str = "") -> list[dict]:
    o = cfg["ollama"]
    body = {
        "model": o["model"],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": o["temperature"], "num_ctx": o["num_ctx"]},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _prompt(profile, n, exclude, new_artists_only, guide)},
        ],
    }
    if o.get("think") is not None:
        body["think"] = bool(o["think"])
    async with httpx.AsyncClient(timeout=o["timeout_s"]) as client:
        r = await client.post(f"{o['url'].rstrip('/')}/api/chat", json=body)
        if r.status_code == 400 and "think" in body and "think" in r.text:
            # Model doesn't support the think toggle; retry without it.
            del body["think"]
            r = await client.post(f"{o['url'].rstrip('/')}/api/chat", json=body)
        r.raise_for_status()
    content = r.json()["message"]["content"]
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    data = json.loads(content)
    out = []
    for item in data.get("recommendations", []):
        artist, album = str(item.get("artist", "")).strip(), str(item.get("album", "")).strip()
        if not artist or not album:
            continue
        try:
            year = int(item.get("year")) if item.get("year") else None
        except (TypeError, ValueError):
            year = None
        out.append({"artist": artist, "album": album, "year": year, "reason": str(item.get("reason", "")).strip()})
    return out


async def rank_albums(cfg: dict, artist: str, titles: list[str]) -> list[str]:
    """Order an artist's albums (from MusicBrainz) best starting point first. Never invents titles."""
    o = cfg["ollama"]
    body = {
        "model": o["model"],
        "stream": False,
        "format": {"type": "object", "properties": {"ranked": {"type": "array", "items": {"type": "string"}}},
                   "required": ["ranked"]},
        "options": {"temperature": 0.2, "num_ctx": o["num_ctx"]},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": (
                f"These are the studio albums by {artist}:\n" + "\n".join(f"- {t}" for t in titles)
                + "\n\nRank the 3 best starting points for a new listener (most acclaimed and representative). "
                "Copy the titles exactly from the list."
            )},
        ],
    }
    if o.get("think") is not None:
        body["think"] = bool(o["think"])
    async with httpx.AsyncClient(timeout=o["timeout_s"]) as client:
        r = await client.post(f"{o['url'].rstrip('/')}/api/chat", json=body)
        if r.status_code == 400 and "think" in body and "think" in r.text:
            del body["think"]
            r = await client.post(f"{o['url'].rstrip('/')}/api/chat", json=body)
        r.raise_for_status()
    content = re.sub(r"<think>.*?</think>", "", r.json()["message"]["content"], flags=re.S).strip()
    by_key = {norm(t): t for t in titles}
    ranked = [by_key[norm(t)] for t in json.loads(content).get("ranked", []) if norm(t) in by_key]
    # Anything the model skipped follows in release order.
    return list(dict.fromkeys(ranked + titles))


async def list_models(cfg: dict) -> list[str]:
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.get(f"{cfg['ollama']['url'].rstrip('/')}/api/tags")
        r.raise_for_status()
    return [m["name"] for m in r.json().get("models", [])]
