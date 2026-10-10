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
  activity TEXT,                                 -- JSON: 90-day counts for the customer card (enquiries, pns, bls…)
  interests TEXT,                                -- JSON list: products of interest (buyer) / products sold (seller)
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
CREATE TABLE IF NOT EXISTS chat_turns (          -- every chat message as it happens: a closed tab loses nothing
  chat_id TEXT, glid TEXT, role TEXT, channel TEXT, speaker TEXT, text TEXT, ts TEXT, ended INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_chat_turns ON chat_turns(glid, role, ended);
CREATE TABLE IF NOT EXISTS requests (            -- things the customer asked IndiaMART to do: a queue for the team
  request_id INTEGER PRIMARY KEY AUTOINCREMENT,
  glid TEXT, role TEXT, type TEXT,               -- post_requirement | send_enquiry | catalogue_update | price_update | callback
  product TEXT, details TEXT,                    -- JSON {qty, price, location, note}
  channel TEXT, thread_id TEXT, status TEXT DEFAULT 'new', ts TEXT, synthetic INTEGER DEFAULT 0
);
"""
JSON_COLS = {"facts": {}, "sellers": [], "links": {}, "flags": {}, "payload": {}, "activity": {}, "interests": [],
             "details": {}}


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None or getattr(_local, "path", None) != str(config.DB_PATH):
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        have = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        for col in ("activity", "interests"):  # migrate databases created before the customer card existed
            if col not in have:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
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
    u = _dump({"flags": {}, "activity": {}, "interests": [], "synthetic": 0, **u})
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
        for table in ("events", "threads", "freshness", "users", "chat_turns", "requests"):
            c.execute(f"DELETE FROM {table} WHERE glid=?", (g,))
    c.commit()


# ---------------------------------------------------------------- chat turns (saved as they happen)
def add_turn(chat_id, glid, role, channel, speaker, text):
    conn().execute("INSERT INTO chat_turns (chat_id, glid, role, channel, speaker, text, ts) VALUES (?,?,?,?,?,?,?)",
                   (chat_id, glid, role, channel, speaker, text, now()))
    conn().commit()


def unfinished_chats(glid, role):
    """{chat_id: {channel, turns}} for chats whose end was never written back (tab closed, server restarted)."""
    out = {}
    for r in conn().execute("SELECT * FROM chat_turns WHERE glid=? AND role=? AND ended=0 ORDER BY rowid", (glid, role)):
        c = out.setdefault(r["chat_id"], {"channel": r["channel"], "turns": []})
        c["turns"].append((r["speaker"], r["text"]))
    return out


def end_chat(chat_id):
    conn().execute("UPDATE chat_turns SET ended=1 WHERE chat_id=?", (chat_id,))
    conn().commit()


# ---------------------------------------------------------------- requests for the IndiaMART team
def add_request(r):
    cur = conn().execute(
        "INSERT INTO requests (glid, role, type, product, details, channel, thread_id, ts, synthetic) VALUES (?,?,?,?,?,?,?,?,?)",
        (r["glid"], r["role"], r["type"], r.get("product"), json.dumps(r.get("details") or {}, ensure_ascii=False),
         r.get("channel"), r.get("thread_id"), r.get("ts") or now(), int(r.get("synthetic", 0))))
    conn().commit()
    return cur.lastrowid


def requests(glid=None, role=None, status=None):
    q, args = "SELECT * FROM requests WHERE 1=1", []
    for col, v in (("glid", glid), ("role", role), ("status", status)):
        if v:
            q += f" AND {col}=?"
            args.append(v)
    return [_row(r) for r in conn().execute(q + " ORDER BY request_id DESC", args)]
