"""Keep history in sync with slskd: queued -> downloading -> completed / failed."""

import json
import re
from collections import Counter
from pathlib import Path

from . import matcher
from .db import DB
from .slskd import Slskd
from .text import search_query

ACTIVE = ("queued", "downloading")

# slskd transfer states -> short labels for the UI.
STATE_LABELS = {
    "InProgress": "downloading",
    "Initializing": "starting",
    "Requested": "requested",
    "Queued, Locally": "queued",
    "Queued, Remotely": "queued by peer",
    "Completed, Succeeded": "done",
    "Completed, Rejected": "rejected",
    "Completed, Errored": "errored",
    "Completed, TimedOut": "timed out",
    "Completed, Cancelled": "cancelled",
    "Completed, Aborted": "aborted",
}


def state_counts(files: list[dict]) -> list[dict]:
    """[{label, count, kind}] in a stable order: in-flight first, then done, then failures."""
    order = list(STATE_LABELS)
    counts = Counter(f.get("state", "") for f in files)
    out = []
    for state in sorted(counts, key=lambda s: order.index(s) if s in order else len(order)):
        if state == "Completed, Succeeded":
            kind = "ok"
        elif state.startswith("Completed"):
            kind = "bad"
        else:
            kind = "active"
        out.append({"label": STATE_LABELS.get(state, state.lower()), "count": counts[state], "kind": kind})
    return out


def summarise(files: list[dict]) -> tuple[str, str]:
    states = Counter(f.get("state", "") for f in files)
    total = len(files)
    ok = states["Completed, Succeeded"]
    finished = sum(n for s, n in states.items() if s.startswith("Completed"))
    if ok == total:
        return "completed", f"{total} files"
    if finished == total:
        bad = ", ".join(sorted(s.split(", ", 1)[-1] for s in states if s.startswith("Completed") and s != "Completed, Succeeded"))
        return "failed", f"{total - ok}/{total} files failed ({bad})"
    size = sum(f.get("size", 0) for f in files) or 1
    pct = round(100 * sum(f.get("bytesTransferred", 0) for f in files) / size)
    if ok or any(s.startswith(("InProgress", "Initializing")) for s in states):
        return "downloading", f"{pct}% ({ok}/{total} files)"
    return "queued", "waiting for peer" + (" (queued remotely)" if states["Queued, Remotely"] else "")


async def sync(db: DB, slsk: Slskd, max_retries: int) -> int:
    """Update active history rows from slskd's transfer list. Returns rows changed.

    Failed downloads become 'retrying' (picked up by retry_failed) until max_retries is used up.
    """
    rows = db.query(
        f"SELECT id, status, detail, username, folder, attempts FROM recs "
        f"WHERE status IN ({','.join('?' * len(ACTIVE))})",
        ACTIVE,
    )
    if not rows:
        return 0
    transfers = {}
    for user in await slsk.downloads():
        for d in user.get("directories", []):
            transfers[(user.get("username"), d.get("directory"))] = d.get("files", [])
    changed = 0
    for row in rows:
        files = transfers.get((row["username"], row["folder"]))
        if not files:
            continue  # cleared from slskd; keep the last known status
        status, detail = summarise(files)
        if status == "failed" and row["attempts"] <= max_retries:
            status = "retrying"
        if (status, detail) != (row["status"], row["detail"]):
            db.execute("UPDATE recs SET status = ?, detail = ? WHERE id = ?", (status, detail, row["id"]))
            changed += 1
    return changed


async def retry_failed(db: DB, slsk: Slskd, cfg: dict, emit) -> None:
    """Re-search each 'retrying' album and queue it from a peer that hasn't been tried yet."""
    for row in db.query("SELECT * FROM recs WHERE status = 'retrying'"):
        label = f"{row['artist']} – {row['album']}"
        tried = set(json.loads(row["tried_users"] or "[]")) | {row["username"]}
        emit(f"Retrying {label}: {row['username']} failed ({row['detail']})")
        responses = await slsk.search(search_query(row["artist"], row["album"]))
        best = matcher.pick_best(responses, row["artist"], row["album"], cfg["downloads"], exclude_users=tried)
        if not best:
            emit(f"Gave up on {label}: no other user has a suitable copy")
            db.execute(
                "UPDATE recs SET status = 'failed', detail = ?, tried_users = ? WHERE id = ?",
                (f"{row['detail']}; no other source found", json.dumps(sorted(tried)), row["id"]),
            )
            continue
        await slsk.enqueue(best.username, best.files)
        attempt = row["attempts"] + 1
        emit(f"Queued {label} [{best.format}, {best.detail['tracks']} tracks] from {best.username} (attempt {attempt})")
        db.execute(
            "UPDATE recs SET status = 'queued', detail = ?, username = ?, folder = ?, file_count = ?, "
            "format = ?, attempts = ?, tried_users = ? WHERE id = ?",
            (f"retry {attempt - 1}: {row['username']} failed", best.username, best.folder, len(best.files),
             best.format, attempt, json.dumps(sorted(tried)), row["id"]),
        )


def safe_name(s: str) -> str:
    """Make a string safe as a folder name on Samba/Windows/macOS shares."""
    s = s.replace(":", " -").replace("/", "-").replace("\\", "-")
    s = re.sub(r'[*?"<>|\x00-\x1f]', "", s)
    return re.sub(r"\s+", " ", s).strip().rstrip(". ")


def folder_name(template: str, artist: str, album: str, year: int | None) -> str:
    if not year:
        template = template.replace(" - {year}", "").replace(" ({year})", "").replace("{year}", "")
    return safe_name(template.format(artist=artist, album=album, year=year or ""))


def organise(db: DB, cfg: dict, emit) -> None:
    """Rename completed album folders in the download dir to the configured template."""
    d = cfg["downloads"]
    if not d["rename_folders"] or not d["local_dir"]:
        return
    base = Path(d["local_dir"])
    for row in db.query("SELECT id, artist, album, year, folder FROM recs WHERE status = 'completed' AND local_path IS NULL"):
        src = base / re.split(r"[\\/]", row["folder"])[-1]  # slskd saves to <downloads>/<remote leaf folder>
        if not src.is_dir():
            db.execute("UPDATE recs SET local_path = '' WHERE id = ?", (row["id"],))  # moved or deleted by hand
            continue
        name = folder_name(d["folder_template"], row["artist"], row["album"], row["year"])
        dest = base / name
        n = 2
        while dest.exists() and dest != src:
            dest = base / f"{name} ({n})"
            n += 1
        if dest != src:
            src.rename(dest)
            emit(f"Renamed '{src.name}' -> '{dest.name}'")
        db.execute("UPDATE recs SET local_path = ? WHERE id = ?", (str(dest), row["id"]))
