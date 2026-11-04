"""SQLite storage (aiosqlite, WAL mode). Timestamps are UTC epoch seconds."""

from __future__ import annotations

import json
import os
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_snapshots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        INTEGER NOT NULL,
    exchange  TEXT    NOT NULL,
    side      TEXT    NOT NULL,          -- BUY = user buys USDT, SELL = user sells USDT
    best      REAL    NOT NULL,
    avg5      REAL    NOT NULL,
    ads       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_snap_ex_side_ts ON rate_snapshots(exchange, side, ts);
CREATE INDEX IF NOT EXISTS ix_snap_ts ON rate_snapshots(ts);

CREATE TABLE IF NOT EXISTS rate_ohlc_1h (
    bucket    INTEGER NOT NULL,
    exchange  TEXT    NOT NULL,
    side      TEXT    NOT NULL,
    open      REAL, high REAL, low REAL, close REAL,
    avg5      REAL,
    samples   INTEGER,
    PRIMARY KEY (bucket, exchange, side)
);

CREATE TABLE IF NOT EXISTS alert_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange      TEXT    NOT NULL,      -- binance | bybit | bitget | any
    side          TEXT    NOT NULL,      -- BUY | SELL
    metric        TEXT    NOT NULL,      -- best | avg5
    op            TEXT    NOT NULL,      -- <= | >=
    threshold     REAL    NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    cooldown_sec  INTEGER NOT NULL DEFAULT 300,
    last_fired_at INTEGER,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id    INTEGER,
    ts         INTEGER NOT NULL,
    exchange   TEXT NOT NULL,
    side       TEXT NOT NULL,
    metric     TEXT NOT NULL,
    op         TEXT NOT NULL,
    price      REAL NOT NULL,
    threshold  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON alert_events(ts);

CREATE TABLE IF NOT EXISTS pattern_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        INTEGER NOT NULL,
    exchange  TEXT NOT NULL,
    side      TEXT NOT NULL,
    kind      TEXT NOT NULL,          -- spike | drop | trend_up | trend_down
    minutes   INTEGER NOT NULL,
    pct       REAL NOT NULL,
    start_price REAL NOT NULL,
    end_price   REAL NOT NULL,
    z         REAL
);
CREATE INDEX IF NOT EXISTS ix_pattern_ts ON pattern_events(ts);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    # ---- snapshots -------------------------------------------------------
    async def insert_snapshots(self, rows: list[tuple[int, str, str, float, float, int]]) -> None:
        if not rows:
            return
        await self.conn.executemany(
            "INSERT INTO rate_snapshots(ts, exchange, side, best, avg5, ads) VALUES (?,?,?,?,?,?)", rows
        )
        await self.conn.commit()

    async def history(self, exchange: str | None, side: str | None, start: int, end: int, bucket: int) -> list[dict]:
        """Bucketed history from raw snapshots plus hourly roll-ups (non-overlapping by construction)."""
        where, args = ["ts >= ? AND ts <= ?"], [start, end]
        if exchange:
            where.append("exchange = ?"); args.append(exchange)
        if side:
            where.append("side = ?"); args.append(side)
        w = " AND ".join(where)
        if bucket <= 0:
            sql = f"SELECT ts AS t, exchange, side, best, avg5 FROM rate_snapshots WHERE {w}"
            raw_args = args
        else:
            sql = (f"SELECT (ts / ?) * ? AS t, exchange, side, AVG(best) AS best, AVG(avg5) AS avg5 "
                   f"FROM rate_snapshots WHERE {w} GROUP BY t, exchange, side")
            raw_args = [bucket, bucket, *args]
        rows = [dict(r) for r in await (await self.conn.execute(sql, raw_args)).fetchall()]

        ow = w.replace("ts", "bucket")
        ohlc = await (await self.conn.execute(
            f"SELECT bucket AS t, exchange, side, close AS best, avg5 FROM rate_ohlc_1h WHERE {ow}", args
        )).fetchall()
        rows.extend(dict(r) for r in ohlc)
        rows.sort(key=lambda r: r["t"])
        return rows

    async def latest_snapshots(self) -> list[dict]:
        cur = await self.conn.execute(
            """SELECT s.* FROM rate_snapshots s
               JOIN (SELECT exchange, side, MAX(ts) AS mts FROM rate_snapshots GROUP BY exchange, side) m
                 ON s.exchange = m.exchange AND s.side = m.side AND s.ts = m.mts"""
        )
        return [dict(r) for r in await cur.fetchall()]

    # ---- retention -------------------------------------------------------
    async def rollup_and_prune(self, cutoff: int) -> int:
        """Roll raw rows older than `cutoff` (aligned to the hour) into rate_ohlc_1h, then delete them."""
        cutoff = (cutoff // 3600) * 3600
        await self.conn.execute(
            """
            INSERT OR REPLACE INTO rate_ohlc_1h(bucket, exchange, side, open, high, low, close, avg5, samples)
            SELECT g.b, g.exchange, g.side,
                   (SELECT best FROM rate_snapshots r WHERE r.exchange=g.exchange AND r.side=g.side
                      AND r.ts >= g.b AND r.ts < g.b + 3600 ORDER BY r.ts ASC LIMIT 1),
                   g.mx, g.mn,
                   (SELECT best FROM rate_snapshots r WHERE r.exchange=g.exchange AND r.side=g.side
                      AND r.ts >= g.b AND r.ts < g.b + 3600 ORDER BY r.ts DESC LIMIT 1),
                   g.a5, g.n
            FROM (SELECT (ts / 3600) * 3600 AS b, exchange, side, MAX(best) AS mx, MIN(best) AS mn,
                         AVG(avg5) AS a5, COUNT(*) AS n
                  FROM rate_snapshots WHERE ts < ? GROUP BY b, exchange, side) g
            """,
            (cutoff,),
        )
        cur = await self.conn.execute("DELETE FROM rate_snapshots WHERE ts < ?", (cutoff,))
        await self.conn.commit()
        return cur.rowcount

    # ---- alert rules -----------------------------------------------------
    async def list_rules(self, only_enabled: bool = False) -> list[dict]:
        sql = "SELECT * FROM alert_rules" + (" WHERE enabled = 1" if only_enabled else "") + " ORDER BY id"
        return [dict(r) for r in await (await self.conn.execute(sql)).fetchall()]

    async def add_rule(self, exchange: str, side: str, metric: str, op: str, threshold: float,
                       cooldown_sec: int, now: int) -> int:
        cur = await self.conn.execute(
            "INSERT INTO alert_rules(exchange, side, metric, op, threshold, cooldown_sec, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (exchange, side, metric, op, threshold, cooldown_sec, now),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def delete_rule(self, rule_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
        await self.conn.commit()
        return cur.rowcount > 0

    async def set_rule_enabled(self, rule_id: int, enabled: bool) -> bool:
        cur = await self.conn.execute("UPDATE alert_rules SET enabled = ? WHERE id = ?", (int(enabled), rule_id))
        await self.conn.commit()
        return cur.rowcount > 0

    async def mark_fired(self, rule_id: int, now: int) -> None:
        await self.conn.execute("UPDATE alert_rules SET last_fired_at = ? WHERE id = ?", (now, rule_id))
        await self.conn.commit()

    async def add_event(self, rule_id: int, ts: int, exchange: str, side: str, metric: str, op: str,
                        price: float, threshold: float) -> None:
        await self.conn.execute(
            "INSERT INTO alert_events(rule_id, ts, exchange, side, metric, op, price, threshold) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (rule_id, ts, exchange, side, metric, op, price, threshold),
        )
        await self.conn.commit()

    async def list_events(self, limit: int = 50) -> list[dict]:
        cur = await self.conn.execute("SELECT * FROM alert_events ORDER BY ts DESC, id DESC LIMIT ?", (limit,))
        return [dict(r) for r in await cur.fetchall()]

    # ---- pattern events ------------------------------------------------
    async def add_pattern_event(self, ts: int, exchange: str, side: str, kind: str, minutes: int,
                                pct: float, start: float, end: float, z: float | None) -> None:
        await self.conn.execute(
            "INSERT INTO pattern_events(ts, exchange, side, kind, minutes, pct, start_price, end_price, z) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, exchange, side, kind, minutes, pct, start, end, z),
        )
        await self.conn.commit()

    async def list_pattern_events(self, limit: int = 50) -> list[dict]:
        cur = await self.conn.execute("SELECT * FROM pattern_events ORDER BY ts DESC, id DESC LIMIT ?", (limit,))
        return [dict(r) for r in await cur.fetchall()]

    # ---- key/value settings ---------------------------------------------
    async def get_setting(self, key: str, default: Any = None) -> Any:
        row = await (await self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,))).fetchone()
        return json.loads(row["value"]) if row else default

    async def set_setting(self, key: str, value: Any) -> None:
        await self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        await self.conn.commit()
