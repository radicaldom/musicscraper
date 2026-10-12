import asyncio
import logging
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, library, llm, tracking
from .db import DB
from .pipeline import Runner
from .slskd import Slskd, SlskdError
from .taste import get_taste, top_library_artists, top_library_genres

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # the tracker polls slskd constantly
STATIC = Path(__file__).parent / "static"

db = DB(config.DATA_DIR / "musicscraper.db")
runner = Runner(db)
scan_state = {"running": False, "message": ""}


async def _scheduler():
    while True:
        hours = float(config.load()["schedule"]["interval_hours"] or 0)
        await asyncio.sleep(max(hours, 0.25) * 3600 if hours > 0 else 300)
        if hours > 0 and not runner.running:
            runner.emit("Scheduled run starting")
            runner.start()


async def _tracker():
    while True:
        cfg = config.load()
        slsk = Slskd(cfg)
        try:
            await tracking.sync(db, slsk, int(cfg["downloads"]["max_retries"]))
            await tracking.retry_failed(db, slsk, cfg, runner.emit)
            await asyncio.to_thread(tracking.organise, db, cfg, runner.emit)
        except SlskdError:
            pass  # slskd down; try again next tick
        except Exception:
            logging.exception("download tracking failed")
        finally:
            await slsk.close()
        await asyncio.sleep(20)


@asynccontextmanager
async def lifespan(_app):
    tasks = [asyncio.create_task(_scheduler()), asyncio.create_task(_tracker())]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="MusicScraper", lifespan=lifespan)


class Taste(BaseModel):
    genres: list[str] = []
    artists: list[str] = []
    songs: list[str] = []
    avoid: list[str] = []
    notes: str = ""


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/taste")
def read_taste():
    return get_taste(db)


@app.put("/api/taste")
def write_taste(t: Taste):
    clean = lambda xs: [x.strip() for x in xs if x.strip()]
    db.set_kv("taste", {"genres": clean(t.genres), "artists": clean(t.artists), "songs": clean(t.songs),
                        "avoid": clean(t.avoid), "notes": t.notes})
    return get_taste(db)


@app.get("/api/library")
def library_stats():
    count = db.query("SELECT COUNT(*) AS albums, COUNT(DISTINCT artist_key) AS artists FROM library")[0]
    return {
        **count,
        "paths": config.load()["library"]["paths"],
        "top_artists": top_library_artists(db, 25),
        "top_genres": top_library_genres(db, 15),
        "scan": scan_state,
    }


@app.post("/api/library/scan")
async def library_scan():
    if scan_state["running"]:
        raise HTTPException(409, "scan already running")
    paths = config.load()["library"]["paths"]
    if not paths:
        raise HTTPException(400, "set [library].paths in config.toml first")

    def progress(msg):
        scan_state["message"] = msg

    def work():
        try:
            rows = library.scan(paths, progress)
            db.replace_library(rows)
            scan_state["message"] = f"Found {len(rows)} albums"
        except Exception as e:
            scan_state["message"] = f"Scan failed: {e}"
        finally:
            scan_state["running"] = False

    scan_state.update(running=True, message="Scanning…")
    threading.Thread(target=work, daemon=True).start()
    return scan_state


class RunRequest(BaseModel):
    guide: str = ""


@app.post("/api/run")
async def start_run(req: RunRequest | None = None):
    if not runner.start(req.guide if req else ""):
        raise HTTPException(409, "a run is already in progress")
    return {"started": True}


@app.get("/api/status")
def status():
    last = db.query("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    return {"running": runner.running, "log": list(runner.log)[-200:], "last_run": last[0] if last else None}


@app.get("/api/recs")
def recs(status: str | None = None, limit: int = 300):
    if status:
        return db.query("SELECT * FROM recs WHERE status = ? ORDER BY id DESC LIMIT ?", (status, limit))
    return db.query("SELECT * FROM recs ORDER BY id DESC LIMIT ?", (limit,))


@app.delete("/api/recs/{rec_id}")
def forget_rec(rec_id: int):
    db.execute("DELETE FROM recs WHERE id = ?", (rec_id,))
    return {"deleted": rec_id}


@app.get("/api/downloads")
async def downloads():
    slsk = Slskd(config.load())
    try:
        users = await slsk.downloads()
    except SlskdError as e:
        raise HTTPException(502, str(e))
    finally:
        await slsk.close()
    out = []
    for user in users:
        for d in user.get("directories", []):
            files = d.get("files", [])
            if not files:
                continue
            size = sum(f.get("size", 0) for f in files) or 1
            done = sum(f.get("bytesTransferred", 0) for f in files)
            status, _detail = tracking.summarise(files)
            out.append({"username": user.get("username"), "folder": d.get("directory"), "files": len(files),
                        "percent": round(100 * done / size, 1), "status": status,
                        "states": tracking.state_counts(files)})
    return out


@app.get("/api/health")
async def health():
    cfg = config.load()
    result = {"config_file": config.CONFIG_PATH.exists(), "model": cfg["ollama"]["model"]}
    try:
        models = await llm.list_models(cfg)
        result["ollama"] = "ok" if cfg["ollama"]["model"] in models else f"model not pulled (have: {', '.join(models)})"
    except Exception as e:
        result["ollama"] = f"unreachable: {e}"
    slsk = Slskd(cfg)
    try:
        await slsk.check()
        result["slskd"] = "ok"
    except SlskdError as e:
        result["slskd"] = str(e)
    finally:
        await slsk.close()
    return result


def run():
    import uvicorn

    s = config.load()["server"]
    url = f"http://{s['host']}:{s['port']}"
    if s["open_browser"]:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=s["host"], port=s["port"])
