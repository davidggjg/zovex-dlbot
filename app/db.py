from __future__ import annotations

import datetime as dt

import aiosqlite

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_seen  TEXT NOT NULL,
    blocked     INTEGER NOT NULL DEFAULT 0,
    sub_lang    TEXT
);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL,
    day     TEXT    NOT NULL,
    bytes   INTEGER NOT NULL DEFAULT 0,
    jobs    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    url     TEXT,
    title   TEXT,
    bytes   INTEGER,
    ok      INTEGER,
    error   TEXT,
    at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS history_user ON history(user_id, at);
"""

_db: aiosqlite.Connection | None = None


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


async def init() -> None:
    global _db
    _db = await aiosqlite.connect(config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.executescript(_SCHEMA)
    await _db.commit()


async def close() -> None:
    if _db is not None:
        await _db.close()


async def touch_user(user_id: int, username: str | None) -> None:
    await _db.execute(
        "INSERT INTO users (user_id, username, first_seen) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET username = excluded.username",
        (user_id, username, _now()),
    )
    await _db.commit()


async def is_blocked(user_id: int) -> bool:
    cur = await _db.execute("SELECT blocked FROM users WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    return bool(row and row["blocked"])


async def set_blocked(user_id: int, blocked: bool) -> None:
    await _db.execute(
        "INSERT INTO users (user_id, first_seen, blocked) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET blocked = excluded.blocked",
        (user_id, _now(), int(blocked)),
    )
    await _db.commit()


async def get_usage(user_id: int) -> tuple[int, int]:
    """(bytes, jobs) used today."""
    cur = await _db.execute(
        "SELECT bytes, jobs FROM usage WHERE user_id = ? AND day = ?",
        (user_id, _today()),
    )
    row = await cur.fetchone()
    return (row["bytes"], row["jobs"]) if row else (0, 0)


async def add_usage(user_id: int, nbytes: int, jobs: int = 1) -> None:
    await _db.execute(
        "INSERT INTO usage (user_id, day, bytes, jobs) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, day) DO UPDATE SET "
        "bytes = bytes + excluded.bytes, jobs = jobs + excluded.jobs",
        (user_id, _today(), nbytes, jobs),
    )
    await _db.commit()


async def log_history(
    user_id: int, url: str, title: str | None, nbytes: int, ok: bool, error: str | None
) -> None:
    await _db.execute(
        "INSERT INTO history (user_id, url, title, bytes, ok, error, at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, url, title, nbytes, int(ok), error, _now()),
    )
    await _db.commit()


async def set_sub_lang(user_id: int, lang: str | None) -> None:
    await _db.execute(
        "INSERT INTO users (user_id, first_seen, sub_lang) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET sub_lang = excluded.sub_lang",
        (user_id, _now(), lang),
    )
    await _db.commit()


async def get_sub_lang(user_id: int) -> str | None:
    cur = await _db.execute("SELECT sub_lang FROM users WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    return row["sub_lang"] if row else None


async def stats() -> dict:
    out = {}
    for key, sql in (
        ("users", "SELECT COUNT(*) c FROM users"),
        ("jobs_today", f"SELECT COALESCE(SUM(jobs),0) c FROM usage WHERE day = '{_today()}'"),
        ("bytes_today", f"SELECT COALESCE(SUM(bytes),0) c FROM usage WHERE day = '{_today()}'"),
        ("jobs_total", "SELECT COUNT(*) c FROM history"),
        ("failed_total", "SELECT COUNT(*) c FROM history WHERE ok = 0"),
    ):
        cur = await _db.execute(sql)
        out[key] = (await cur.fetchone())["c"]
    return out
