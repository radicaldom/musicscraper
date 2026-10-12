"""One discovery run: ask the AI, verify, filter, search Soulseek, queue the best folder."""

import asyncio
import logging
import time
from collections import deque

from . import config, llm, matcher
from .db import DB
from .musicbrainz import MusicBrainz
from .slskd import Slskd, SlskdError
from .taste import build_profile, get_taste, top_library_artists
from .text import norm, search_query

log = logging.getLogger("musicscraper")


class Runner:
    def __init__(self, db: DB):
        self.db = db
        self.running = False
        self.log: deque[dict] = deque(maxlen=500)
        self._task: asyncio.Task | None = None

    def emit(self, msg: str) -> None:
        log.info(msg)
        self.log.append({"t": time.time(), "msg": msg})

    def start(self, guide: str = "") -> bool:
        if self.running:
            return False
        self.running = True
        self._task = asyncio.create_task(self._run(guide.strip()))
        return True

    async def _run(self, guide: str) -> None:
        run_id = self.db.execute("INSERT INTO runs (started_at, guide) VALUES (?, ?)", (time.time(), guide or None))
        queued, summary = 0, ""
        try:
            queued = await self._discover(run_id, guide)
            summary = f"queued {queued} album(s)"
        except Exception as e:
            log.exception("run failed")
            summary = f"failed: {e}"
        finally:
            self.emit(f"Run finished: {summary}")
            self.db.execute(
                "UPDATE runs SET finished_at = ?, queued = ?, summary = ? WHERE id = ?",
                (time.time(), queued, summary, run_id),
            )
            self.running = False

    def _record(self, run_id: int, rec: dict, status: str, detail: str = "", **extra) -> None:
        self.db.execute(
            "INSERT INTO recs (run_id, created_at, artist, album, artist_key, album_key, year, reason, "
            "status, detail, mb_artist_id, mb_release_group_id, username, folder, file_count, format) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, time.time(), rec["artist"], rec["album"], norm(rec["artist"]), norm(rec["album"]),
             rec.get("year"), rec.get("reason"), status, detail,
             extra.get("mb_artist_id"), extra.get("mb_release_group_id"), extra.get("username"),
             extra.get("folder"), extra.get("file_count"), extra.get("format")),
        )

    async def _guide_context(self, guide: str, mb: MusicBrainz | None) -> tuple[str, str, str | None]:
        """Return (canonical name, description, MusicBrainz id) for the guide band."""
        if not mb:
            return guide, guide, None
        try:
            a = await mb.find_artist(guide)
        except Exception as e:
            self.emit(f"MusicBrainz lookup for '{guide}' failed: {e}")
            a = None
        if not a:
            self.emit(f"'{guide}' not found on MusicBrainz; guiding by name only")
            return guide, guide, None
        tags = [t["name"] for t in sorted(a.get("tags", []), key=lambda t: -t.get("count", 0))[:8]]
        facts = [a.get("disambiguation"), a.get("country"), (a.get("life-span") or {}).get("begin", "")[:4]]
        facts = ", ".join(f for f in facts if f)
        desc = f"{a['name']} ({facts})" if facts else a["name"]
        if tags:
            desc += " — tagged: " + ", ".join(tags)
        return a["name"], desc, a["id"]

    async def _queue_guide_album(self, run_id, cfg, guide, artist_id, owned, seen, mb, slsk) -> bool:
        """The guide band isn't on the NAS: queue one of its best studio albums (trying the top 3)."""
        albums = await mb.studio_albums(artist_id)
        if not albums:
            self.emit(f"No studio albums for {guide} on MusicBrainz")
            return False
        titles = [g["title"] for g in albums]
        try:
            ranked = await llm.rank_albums(cfg, guide, titles)
        except Exception as e:
            self.emit(f"Couldn't rank {guide} albums ({e}); using release order")
            ranked = titles
        years = {g["title"]: (g.get("first-release-date") or "")[:4] for g in albums}
        for title in ranked[:3]:
            rec = {"artist": guide, "album": title,
                   "year": int(years[title]) if years.get(title, "").isdigit() else None,
                   "reason": f"Starter album: you guided this run with {guide} and have none of their music"}
            if await self._process(run_id, cfg, rec, owned, set(), seen, False, mb, slsk):
                return True
        return False

    async def _discover(self, run_id: int, guide: str = "") -> int:
        cfg = config.load()
        limit = int(cfg["downloads"]["max_albums_per_run"])
        new_only = bool(cfg["ai"]["new_artists_only"])

        profile = build_profile(self.db)
        if not profile and not guide:
            raise RuntimeError("no taste data: add genres/artists/songs or scan your library first")

        owned = {(r["artist_key"], r["album_key"]) for r in self.db.query("SELECT artist_key, album_key FROM library")}
        owned_artists = {a for a, _ in owned} | {norm(a) for a in get_taste(self.db)["artists"]}
        # Errors and failed downloads can be retried immediately (likely from another peer);
        # albums nobody was sharing get another try after 30 days.
        seen = {(r["artist_key"], r["album_key"]) for r in self.db.query(
            "SELECT artist_key, album_key FROM recs WHERE status NOT IN ('error', 'failed') "
            "AND NOT (status = 'not_found' AND created_at < ?)", (time.time() - 30 * 86400,))}

        slsk = Slskd(cfg)
        mb = MusicBrainz(cfg["musicbrainz"]["contact"]) if cfg["musicbrainz"]["enabled"] else None
        queued = 0
        try:
            await slsk.check()
            self.emit("Connected to slskd")
            guide_desc = ""
            if guide:
                guide, guide_desc, guide_id = await self._guide_context(guide, mb)
                self.emit(f"Guiding this run with {guide_desc}")
                library_artists = {a for a, _ in owned}
                getting = self.db.query(
                    "SELECT 1 FROM recs WHERE artist_key = ? AND status IN "
                    "('queued', 'downloading', 'retrying', 'completed') LIMIT 1", (norm(guide),))
                if guide_id and norm(guide) not in library_artists and not getting and queued < limit:
                    self.emit(f"No {guide} on your NAS yet: picking one of their albums first")
                    if await self._queue_guide_album(run_id, cfg, guide, guide_id, owned, seen, mb, slsk):
                        queued += 1
                owned_artists = owned_artists | {norm(guide)}  # the AI suggests similar bands, not the guide itself
            for round_no in range(1, int(cfg["ai"]["max_rounds"]) + 1):
                if queued >= limit:
                    break
                ask = (limit - queued) * int(cfg["ai"]["overfetch"])
                exclude = self._exclusions(new_only)
                self.emit(f"Round {round_no}: asking {cfg['ollama']['model']} for {ask} suggestions…")
                recs = await llm.recommend(cfg, profile, ask, exclude, new_only, guide_desc)
                self.emit(f"AI suggested {len(recs)} albums")
                for rec in recs:
                    if queued >= limit:
                        break
                    if await self._process(run_id, cfg, rec, owned, owned_artists, seen, new_only, mb, slsk):
                        queued += 1
        finally:
            await slsk.close()
            if mb:
                await mb.close()
        return queued

    def _exclusions(self, new_only: bool) -> list[str]:
        """Names to tell the model to avoid. The full filter is applied in code afterwards."""
        rows = self.db.query("SELECT artist, album FROM recs ORDER BY id DESC LIMIT 150")
        out = [f"{r['artist']} - {r['album']}" for r in rows]
        if new_only:
            out += [f"anything by {a['artist']}" for a in top_library_artists(self.db, 100)]
        return out

    async def _process(self, run_id, cfg, rec, owned, owned_artists, seen, new_only, mb, slsk) -> bool:
        label = f"{rec['artist']} – {rec['album']}"

        def blocked(r) -> str:
            key = (norm(r["artist"]), norm(r["album"]))
            if key in owned:
                return "already in library"
            if key in seen:
                return "already suggested before"
            if new_only and key[0] in owned_artists:
                return "artist already in library"
            return ""

        if reason := blocked(rec):
            self.emit(f"Skip {label}: {reason}")
            return False

        ids = {}
        original = rec
        if mb:
            try:
                verified, why = await mb.verify(rec["artist"], rec["album"])
            except Exception as e:
                verified, why = None, f"MusicBrainz error: {e}"
            if not verified:
                self.emit(f"Reject {label}: {why}")
                self._record(run_id, rec, "rejected", why)
                seen.add((norm(rec["artist"]), norm(rec["album"])))
                return False
            rec = {**rec, "artist": verified["artist"], "album": verified["album"],
                   "year": verified["year"] or rec.get("year")}
            ids = {"mb_artist_id": verified["artist_id"], "mb_release_group_id": verified["release_group_id"]}
            label = f"{rec['artist']} – {rec['album']}"
            if reason := blocked(rec):
                self.emit(f"Skip {label}: {reason}")
                return False
        seen.add((norm(rec["artist"]), norm(rec["album"])))

        # Search with the canonical names first, then the AI's wording if it differed.
        variants = [(rec["artist"], rec["album"])]
        if norm(original["album"]) != norm(rec["album"]) or norm(original["artist"]) != norm(rec["artist"]):
            variants.append((original["artist"], original["album"]))
        try:
            best, responses = None, []
            for artist, album in variants:
                query = search_query(artist, album)
                self.emit(f"Searching Soulseek: {query}")
                responses = await slsk.search(query)
                best = matcher.pick_best(responses, artist, album, cfg["downloads"])
                if best:
                    break
            if not best:
                self.emit(f"No suitable folder for {label} ({len(responses)} peers responded)")
                self._record(run_id, rec, "not_found", f"{len(responses)} peers responded", **ids)
                return False
            await slsk.enqueue(best.username, best.files)
        except SlskdError as e:
            self.emit(f"slskd error for {label}: {e}")
            self._record(run_id, rec, "error", str(e), **ids)
            return False

        self.emit(f"Queued {label} [{best.format}, {best.detail['tracks']} tracks] from {best.username}")
        self._record(run_id, rec, "queued", f"score {best.score}", username=best.username,
                     folder=best.folder, file_count=len(best.files), format=best.format, **ids)
        return True
