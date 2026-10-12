"""Verify AI suggestions against MusicBrainz and return canonical names.

MusicBrainz allows ~1 request/second per client, which this module enforces.
"""

import asyncio
import re
import time

import httpx

from .text import norm, similarity

API = "https://musicbrainz.org/ws/2"
_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def _quote(s: str) -> str:
    return '"' + _LUCENE_SPECIAL.sub(r"\\\1", s) + '"'


class MusicBrainz:
    def __init__(self, contact: str):
        self.client = httpx.AsyncClient(
            timeout=20,
            headers={"User-Agent": f"MusicScraper/0.1 ( {contact} )", "Accept": "application/json"},
        )
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def close(self):
        await self.client.aclose()

    async def _get(self, path: str, params: dict) -> dict:
        async with self._lock:
            for attempt in range(3):
                wait = self._last + 1.1 - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last = time.monotonic()
                r = await self.client.get(f"{API}/{path}", params={**params, "fmt": "json"})
                if r.status_code == 503:  # rate limited
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                r.raise_for_status()
                return r.json()
        raise RuntimeError("MusicBrainz kept rate-limiting requests")

    async def find_artist(self, name: str) -> dict | None:
        data = await self._get("artist", {"query": f"artist:{_quote(name)}", "limit": 10})
        best, best_sim = None, 0.0
        for a in data.get("artists", []):
            names = [a["name"]] + [al["name"] for al in a.get("aliases", [])]
            sim = max(similarity(name, n) for n in names)
            # Prefer exact-ish matches with a high search score; ties go to the more relevant result.
            if sim > best_sim and int(a.get("score", 0)) >= 70:
                best, best_sim = a, sim
        return best if best_sim >= 0.85 else None

    async def find_album(self, artist_id: str, album: str) -> dict | None:
        data = await self._get(
            "release-group",
            {"query": f"releasegroup:{_quote(album)} AND arid:{artist_id}", "limit": 10},
        )
        groups = data.get("release-groups", [])
        if not groups:
            # Fall back to browsing all of the artist's release groups (handles odd punctuation).
            data = await self._get("release-group", {"artist": artist_id, "limit": 100})
            groups = data.get("release-groups", [])
        groups = [g for g in groups if g.get("primary-type") in ("Album", "EP")]  # not singles
        best, best_sim = None, 0.0
        for g in groups:
            sim = similarity(album, g["title"])
            if norm(album) == norm(g["title"]):
                sim = 1.0
            if sim > best_sim:
                best, best_sim = g, sim
        return best if best_sim >= 0.8 else None

    async def studio_albums(self, artist_id: str) -> list[dict]:
        """The artist's studio albums (no live/compilation/remix secondary types), oldest first."""
        data = await self._get("release-group", {"artist": artist_id, "type": "album", "limit": 100})
        albums = [g for g in data.get("release-groups", []) if not g.get("secondary-types")]
        return sorted(albums, key=lambda g: g.get("first-release-date") or "9999")

    async def verify(self, artist: str, album: str) -> tuple[dict | None, str]:
        """Return ({artist, album, year, artist_id, release_group_id}, '') or (None, reason)."""
        a = await self.find_artist(artist)
        if not a:
            return None, f"artist '{artist}' not found on MusicBrainz"
        g = await self.find_album(a["id"], album)
        if not g:
            return None, f"album '{album}' not found for {a['name']} on MusicBrainz"
        date = g.get("first-release-date") or ""
        return {
            "artist": a["name"],
            "album": g["title"],
            "year": int(date[:4]) if date[:4].isdigit() else None,
            "artist_id": a["id"],
            "release_group_id": g["id"],
        }, ""
