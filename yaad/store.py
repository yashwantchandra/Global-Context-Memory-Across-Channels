"""SQLite event store. One row per event per (GLID, role). add_event() rebuilds that GLID's file inline."""
import json
import sqlite3
import threading
import time
from datetime import datetime

from . import config

_local = threading.local()
_rebuild_hooks = []  # callables(glid, role, event_ts) registered by pipeline

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  glid TEXT NOT NULL, role TEXT NOT NULL,           -- role: buyer | seller (whose file this feeds)
  source TEXT NOT NULL, channel TEXT NOT NULL,       -- source: lookback key; channel: human label
  ts TEXT NOT NULL,                                  -- when it happened (ISO)
  counterparty_glid TEXT,                            -- never rendered into the other party's file
  thread_key TEXT,                                   -- product / mcat grouping
  payload TEXT NOT NULL,                             -- JSON, already minimised
  extracted TEXT,                                    -- JSON from LLM extraction (cached)
  synthetic INTEGER NOT NULL DEFAULT 0,
  ingested_at REAL NOT NULL                          -- epoch seconds when it entered the store
);
CREATE INDEX IF NOT EXISTS ix_events_glid ON events(glid, role, ts);
CREATE TABLE IF NOT EXISTS profiles (glid TEXT PRIMARY KEY, data TEXT NOT NULL, synthetic INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS names (glid TEXT PRIMARY KEY, company TEXT, city TEXT);  -- seller companies, for buyer.md
CREATE TABLE IF NOT EXISTS renders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, glid TEXT, role TEXT, path TEXT,
  trigger_event_id INTEGER, event_ingested_at REAL, written_at REAL, freshness_ms REAL,
  tokens INTEGER, last_event_ts TEXT
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        _local.c = c
    return c


def on_event(hook):
    _rebuild_hooks.append(hook)


def add_event(glid, role, source, channel, ts, payload, *, counterparty=None, thread_key=None,
              extracted=None, synthetic=False, rebuild=True):
    """Append an event. With rebuild=True (live channels), the GLID's file is rebuilt before returning."""
    ts = ts if isinstance(ts, str) else ts.strftime("%Y-%m-%d %H:%M:%S")
    c = conn()
    cur = c.execute(
        "INSERT INTO events(glid, role, source, channel, ts, counterparty_glid, thread_key, payload, extracted, synthetic, ingested_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (str(glid), role, source, channel, ts, counterparty, thread_key, json.dumps(payload, ensure_ascii=False),
         json.dumps(extracted, ensure_ascii=False) if extracted else None, int(synthetic), time.time()))
    c.commit()
    eid = cur.lastrowid
    if rebuild:
        for hook in _rebuild_hooks:
            hook(str(glid), role, eid)
    return eid


def events(glid, role):
    rows = conn().execute("SELECT * FROM events WHERE glid=? AND role=? ORDER BY ts", (str(glid), role)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        d["extracted"] = json.loads(d["extracted"]) if d["extracted"] else None
        d["dt"] = datetime.fromisoformat(d["ts"][:19])
        out.append(d)
    return out


def event(eid):
    r = conn().execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
    return dict(r) if r else None


def set_extracted(eid, data):
    conn().execute("UPDATE events SET extracted=? WHERE id=?", (json.dumps(data, ensure_ascii=False), eid))
    conn().commit()


def profile(glid):
    r = conn().execute("SELECT data, synthetic FROM profiles WHERE glid=?", (str(glid),)).fetchone()
    if not r:
        return None
    d = json.loads(r["data"])
    d["_synthetic"] = bool(r["synthetic"])
    return d


def seller_name(glid):
    r = conn().execute("SELECT company, city FROM names WHERE glid=?", (str(glid),)).fetchone()
    return dict(r) if r else None


def log_render(**kw):
    cols = ",".join(kw)
    conn().execute(f"INSERT INTO renders({cols}) VALUES ({','.join('?' * len(kw))})", tuple(kw.values()))
    conn().commit()


def roles_for(glid):
    return [r[0] for r in conn().execute("SELECT DISTINCT role FROM events WHERE glid=?", (str(glid),))]
