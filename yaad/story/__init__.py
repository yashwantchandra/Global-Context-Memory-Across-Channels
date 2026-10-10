"""The two demo journeys (labelled synthetic). Each step is narration + one real event for POST /v1/event."""
import json
from datetime import datetime, timedelta
from pathlib import Path

from .. import db, updater

DIR = Path(__file__).parent
PERSONAS = ("amit", "rakesh")


def load(persona):
    return json.loads((DIR / f"{persona}.json").read_text())


def ts_from_ago(ago):
    """'9d', '3h', '0' -> a timestamp that many days/hours before now."""
    ago = str(ago or "0")
    n = float(ago[:-1] or 0) if ago[-1] in "dh" else 0.0
    delta = timedelta(days=n) if ago.endswith("d") else timedelta(hours=n)
    return (datetime.now() - delta).strftime("%Y-%m-%d %H:%M:%S")


def all_glids():
    g = set()
    for p in PERSONAS:
        s = load(p)
        g |= {u["glid"] for u in s["setup"]["users"]}
    return g


def reset(persona):
    """Wipe every story customer, then load this persona's setup (users, mcats, history events)."""
    s = load(persona)
    db.wipe(all_glids())
    for mid, name, parent in s["setup"]["mcats"]:
        db.upsert_mcat(mid, name, parent)
    for u in s["setup"]["users"]:
        db.upsert_user(u)
    for e in sorted(s["setup"]["events"], key=lambda e: ts_from_ago(e.get("ago"))):
        body = {k: v for k, v in e.items() if k != "ago"}
        updater.apply({**body, "ts": ts_from_ago(e.get("ago")), "synthetic": 1})
    return s


def event_body(persona, step):
    """The exact JSON body the demo UI posts to /v1/event for a story step."""
    s = load(persona)
    e = s["steps"][step]["event"]
    return {"glid": e.get("glid", s["glid"]), "role": e.get("role", s["role"]), "type": e["type"],
            "mcat_id": e.get("mcat_id"), "counterparty": e.get("counterparty"), "payload": e.get("payload", {}),
            "ts": ts_from_ago(s["steps"][step].get("ago")), "synthetic": 1}
