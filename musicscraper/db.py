import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS library (
    artist_key TEXT NOT NULL,
    album_key TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    genres TEXT NOT NULL DEFAULT '',
    tracks INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (artist_key, album_key)
);
CREATE TABLE IF NOT EXISTS recs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    created_at REAL NOT NULL,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    artist_key TEXT NOT NULL,
    album_key TEXT NOT NULL,
    year INTEGER,
    reason TEXT,
    status TEXT NOT NULL,
    detail TEXT,
    mb_artist_id TEXT,
    mb_release_group_id TEXT,
    username TEXT,
    folder TEXT,
    file_count INTEGER,
    format TEXT
);
CREATE INDEX IF NOT EXISTS recs_key ON recs (artist_key, album_key);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    finished_at REAL,
    queued INTEGER NOT NULL DEFAULT 0,
    summary TEXT
);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._add_columns("runs", {"guide": "TEXT"})
            self._add_columns("recs", {"attempts": "INTEGER NOT NULL DEFAULT 1", "tried_users": "TEXT", "local_path": "TEXT"})

    def _add_columns(self, table: str, columns: dict[str, str]) -> None:
        """Lightweight migration: add any columns missing from an existing database."""
        have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in have:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        self.conn.commit()

    def query(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def execute(self, sql: str, args=()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    def get_kv(self, key: str, default=None):
        rows = self.query("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_kv(self, key: str, value) -> None:
        self.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def replace_library(self, rows: list[dict]) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM library")
            self.conn.executemany(
                "INSERT OR REPLACE INTO library "
                "(artist_key, album_key, artist, album, genres, tracks, path) "
                "VALUES (:artist_key, :album_key, :artist, :album, :genres, :tracks, :path)",
                rows,
            )
            self.conn.commit()
