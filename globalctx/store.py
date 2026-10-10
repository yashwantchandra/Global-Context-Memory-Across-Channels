"""SQLite event store keyed by GLID, plus the latest rendered profile per GLID."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from globalctx import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    glid TEXT NOT NULL,
    role TEXT NOT NULL,          -- buyer | seller
    source TEXT NOT NULL,        -- enquiry, vani_call, session, ...
    channel TEXT,                -- voice, whatsapp, chat, web, phone ...
    ts TEXT NOT NULL,            -- when it happened (ISO)
    ext_id TEXT,                 -- source id, for dedupe
    payload TEXT NOT NULL,       -- JSON
    synthetic INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_events_src ON events(source, ext_id) WHERE ext_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_events_glid ON events(glid, role, source, ts);

CREATE TABLE IF NOT EXISTS profiles (
    glid TEXT NOT NULL,
    role TEXT NOT NULL,
    md TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    last_event_at TEXT,
    freshness_ms INTEGER,
    opening TEXT,
    opening_basis TEXT,
    PRIMARY KEY (glid, role)
);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


@contextmanager
def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    with connect() as c:
        c.executescript(SCHEMA)


def insert_events(conn, rows):
    """rows: iterable of dicts with glid, role, source, channel, ts, ext_id, payload, synthetic."""
    ing = now_iso()
    conn.executemany(
        "INSERT OR IGNORE INTO events(glid, role, source, channel, ts, ext_id, payload, synthetic, ingested_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                str(r["glid"]), r["role"], r["source"], r.get("channel"), r["ts"], r.get("ext_id"),
                json.dumps(r.get("payload", {}), ensure_ascii=False), int(r.get("synthetic", 0)), ing,
            )
            for r in rows
        ],
    )


def add_event(glid, role, source, channel, payload, ts=None, ext_id=None, synthetic=False):
    """Insert one live event and return its row (used by channels and the simulate endpoint)."""
    ts = ts or now_iso()
    with connect() as c:
        cur = c.execute(
            "INSERT INTO events(glid, role, source, channel, ts, ext_id, payload, synthetic, ingested_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (str(glid), role, source, channel, ts, ext_id, json.dumps(payload, ensure_ascii=False),
             int(synthetic), now_iso()),
        )
        return dict(c.execute("SELECT * FROM events WHERE id=?", (cur.lastrowid,)).fetchone())


def events_for(glid, role, source=None, since=None):
    q = "SELECT * FROM events WHERE glid=? AND role=?"
    args = [str(glid), role]
    if source:
        q += " AND source=?"
        args.append(source)
    if since:
        q += " AND ts>=?"
        args.append(since.isoformat() if isinstance(since, datetime) else since)
    q += " ORDER BY ts DESC"
    with connect() as c:
        out = []
        for r in c.execute(q, args):
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out


# sources whose lookback is longer than the event windows (state, not activity)
LONG_LIVED = ("profile", "contact", "past_need")


def events_in_lookback(glid, role):
    """Only what any section can use: events inside the longest activity lookback window, plus long-lived
    state (profile, contact, past needs). Keeps build time flat for users with years of history."""
    from datetime import timedelta
    longest = max(v for k, v in config.LOOKBACK_DAYS.items() if k not in LONG_LIVED)
    since = (config.as_of() - timedelta(days=longest)).isoformat()
    q = (f"SELECT * FROM events WHERE glid=? AND role=? AND (ts>=? OR source IN ({','.join('?' * len(LONG_LIVED))}))"
         " ORDER BY ts DESC")
    with connect() as c:
        out = []
        for r in c.execute(q, (str(glid), role, since, *LONG_LIVED)):
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out


def latest_event(glid, role):
    with connect() as c:
        r = c.execute(
            "SELECT ts, ingested_at FROM events WHERE glid=? AND role=? AND source!='profile'"
            " ORDER BY ingested_at DESC, ts DESC LIMIT 1",
            (str(glid), role),
        ).fetchone()
        return dict(r) if r else None


def save_profile(glid, role, md, generated_at, last_event_at, freshness_ms, opening, opening_basis):
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO profiles VALUES (?,?,?,?,?,?,?,?)",
            (str(glid), role, md, generated_at, last_event_at, freshness_ms, opening, opening_basis),
        )


def get_profile(glid, role):
    with connect() as c:
        r = c.execute("SELECT * FROM profiles WHERE glid=? AND role=?", (str(glid), role)).fetchone()
        return dict(r) if r else None


def known_glids(role):
    with connect() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT glid FROM events WHERE role=?", (role,))]


def purge(older_than_days=None):
    """Retention: drop events older than the longest lookback + 7 days (profile snapshots excluded)."""
    days = older_than_days or max(v for k, v in config.LOOKBACK_DAYS.items() if k != "profile") + 7
    from datetime import timedelta
    cutoff = (config.as_of() - timedelta(days=days)).isoformat()
    with connect() as c:
        return c.execute("DELETE FROM events WHERE source!='profile' AND ts<?", (cutoff,)).rowcount
