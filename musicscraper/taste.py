"""Build a plain-text taste profile from the user's seeds and their library."""

import re
from collections import Counter

from .db import DB

EMPTY_TASTE = {"genres": [], "artists": [], "songs": [], "avoid": [], "notes": ""}


def get_taste(db: DB) -> dict:
    return {**EMPTY_TASTE, **db.get_kv("taste", {})}


def top_library_artists(db: DB, limit: int) -> list[dict]:
    return db.query(
        "SELECT artist, COUNT(*) AS albums FROM library "
        "WHERE artist_key NOT IN ('various artists', 'various', 'va', 'unknown artist', '') "
        "GROUP BY artist_key "
        "ORDER BY albums DESC, artist LIMIT ?",
        (limit,),
    )


def top_library_genres(db: DB, limit: int) -> list[tuple[str, int]]:
    counts = Counter()
    for row in db.query("SELECT genres FROM library WHERE genres != ''"):
        for g in re.split(r"[;,/]", row["genres"]):
            g = g.strip().lower()
            if g:
                counts[g] += 1
    return counts.most_common(limit)


def build_profile(db: DB) -> str:
    taste = get_taste(db)
    lines = []
    if taste["genres"]:
        lines.append("Favourite genres: " + ", ".join(taste["genres"]))
    if taste["artists"]:
        lines.append("Favourite artists: " + ", ".join(taste["artists"]))
    if taste["songs"]:
        lines.append("Favourite songs: " + "; ".join(taste["songs"]))
    artists = top_library_artists(db, 40)
    if artists:
        lines.append(
            "Most-collected artists in their library (albums owned): "
            + ", ".join(f"{a['artist']} ({a['albums']})" for a in artists)
        )
    genres = top_library_genres(db, 20)
    if genres:
        lines.append("Common genres in their library: " + ", ".join(g for g, _ in genres))
    if taste["avoid"]:
        lines.append("Dislikes / avoid: " + ", ".join(taste["avoid"]))
    if taste["notes"].strip():
        lines.append("Extra notes from the listener: " + taste["notes"].strip())
    return "\n".join(lines)
