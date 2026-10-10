"""SQLite storage: users, mcats, events, threads, freshness. The md file is never stored: it is built from these
tables at query time (context.py)."""
import json
import sqlite3
import threading
import time
from datetime import datetime

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  glid TEXT, role TEXT,                          -- the same GLID can be a buyer and a seller
  name TEXT, company TEXT, city TEXT, state TEXT, tier TEXT, language TEXT, plan TEXT, kyc TEXT,
  flags TEXT,                                    -- JSON: dnd, asked_bot, frustrated, objection, has_executive
  synthetic INTEGER DEFAULT 0,
  PRIMARY KEY (glid, role)
);
CREATE TABLE IF NOT EXISTS mcats (mcat_id TEXT PRIMARY KEY, name TEXT, parent_id TEXT);
CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  glid TEXT, role TEXT, type TEXT, mcat_id TEXT, thread_id TEXT,
  counterparty TEXT,                             -- the other party's GLID: stored, never rendered to the other side
  ts TEXT, payload TEXT,
  map_method TEXT, map_confidence TEXT,
  received_at REAL, synthetic INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_events ON events(glid, role, ts);
CREATE TABLE IF NOT EXISTS threads (
  thread_id TEXT PRIMARY KEY,
  glid TEXT, role TEXT, mcat_id TEXT, title TEXT,
  kind TEXT,                                     -- requirement | problem | opportunity
  parent_thread_id TEXT,
  stage TEXT,
  facts TEXT,                                    -- JSON {name: {v, src, ts}}: newest wins, provenance kept
  sellers TEXT,                                  -- JSON [{id, name, status, ts}] (buyer side only)
  links TEXT,                                    -- JSON internal cross-side links, never rendered
  summary TEXT, next_step TEXT, last_channel TEXT,
  last_activity TEXT, created_at TEXT, n_events INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_threads ON threads(glid, role, last_activity);
CREATE TABLE IF NOT EXISTS freshness (event_id INTEGER, glid TEXT, role TEXT, received_at REAL, ready_at REAL, ms REAL);
"""
JSON_COLS = {"facts": {}, "sellers": [], "links": {}, "flags": {}, "payload": {}}


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None or getattr(_local, "path", None) != str(config.DB_PATH):
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        _local.c, _local.path = c, str(config.DB_PATH)
    return c


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _row(r):
    if r is None:
        return None
    d = dict(r)
    for k, default in JSON_COLS.items():
        if k in d:
            d[k] = json.loads(d[k]) if d[k] else type(default)()
    return d


def _dump(d):
    return {k: (json.dumps(v, ensure_ascii=False) if k in JSON_COLS else v) for k, v in d.items()}


# ---------------------------------------------------------------- users & mcats
def upsert_user(u):
    u = _dump({"flags": {}, "synthetic": 0, **u})
    cols = ",".join(u)
    conn().execute(f"INSERT OR REPLACE INTO users({cols}) VALUES ({','.join('?' * len(u))})", tuple(u.values()))
    conn().commit()


def user(glid, role):
    return _row(conn().execute("SELECT * FROM users WHERE glid=? AND role=?", (glid, role)).fetchone())


def any_user(glid):
    return _row(conn().execute("SELECT * FROM users WHERE glid=? LIMIT 1", (glid,)).fetchone())


def upsert_mcat(mcat_id, name, parent_id=None):
    conn().execute("INSERT OR REPLACE INTO mcats VALUES (?,?,?)", (mcat_id, name, parent_id))
    conn().commit()


def mcat(mcat_id):
    return _row(conn().execute("SELECT * FROM mcats WHERE mcat_id=?", (mcat_id,)).fetchone()) if mcat_id else None


def mcats():
    return [_row(r) for r in conn().execute("SELECT * FROM mcats")]


# ---------------------------------------------------------------- events
def insert_event(e):
    e = _dump({"received_at": time.time(), "synthetic": 0, **e})
    cols = ",".join(e)
    cur = conn().execute(f"INSERT INTO events({cols}) VALUES ({','.join('?' * len(e))})", tuple(e.values()))
    conn().commit()
    return cur.lastrowid


def events(glid, role=None, limit=200):
    q, args = "SELECT * FROM events WHERE glid=?", [glid]
    if role:
        q += " AND role=?"
        args.append(role)
    return [_row(r) for r in conn().execute(q + " ORDER BY ts, event_id LIMIT ?", (*args, limit))]


# ---------------------------------------------------------------- threads
def thread(tid):
    return _row(conn().execute("SELECT * FROM threads WHERE thread_id=?", (tid,)).fetchone())


def threads(glid, role):
    return [_row(r) for r in conn().execute(
        "SELECT * FROM threads WHERE glid=? AND role=? ORDER BY last_activity DESC", (glid, role))]


def thread_for(glid, role, mcat_id):
    return _row(conn().execute("SELECT * FROM threads WHERE glid=? AND role=? AND mcat_id=? AND kind!='problem' "
                               "ORDER BY last_activity DESC LIMIT 1", (glid, role, mcat_id)).fetchone())


def new_thread_id(glid):
    n = conn().execute("SELECT COUNT(*) FROM threads WHERE glid=?", (glid,)).fetchone()[0]
    return f"T{n + 1}-{glid}"


def save_thread(t):
    t = _dump(t)
    cols = ",".join(t)
    conn().execute(f"INSERT OR REPLACE INTO threads({cols}) VALUES ({','.join('?' * len(t))})", tuple(t.values()))
    conn().commit()


# ---------------------------------------------------------------- freshness
def log_freshness(event_id, glid, role, received_at):
    ready = time.time()
    ms = (ready - received_at) * 1000
    conn().execute("INSERT INTO freshness VALUES (?,?,?,?,?,?)", (event_id, glid, role, received_at, ready, ms))
    conn().commit()
    return ms


def freshness_stats():
    v = sorted(r[0] for r in conn().execute("SELECT ms FROM freshness"))
    pick = lambda p: round(v[min(len(v) - 1, int(len(v) * p))], 1) if v else None
    return {"n": len(v), "p50_ms": pick(.5), "p95_ms": pick(.95)}


def wipe(glids):
    c = conn()
    for g in glids:
        c.execute("DELETE FROM events WHERE glid=?", (g,))
        c.execute("DELETE FROM threads WHERE glid=?", (g,))
        c.execute("DELETE FROM freshness WHERE glid=?", (g,))
        c.execute("DELETE FROM users WHERE glid=?", (g,))
    c.commit()
