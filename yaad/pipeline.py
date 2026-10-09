"""rebuild(glid, role): events -> threads -> render -> file, with freshness measured per trigger event."""
import time

from . import config, render, store, threads

_listeners = []  # callables(dict) notified after every rebuild (web UI live updates)


def on_rebuild(fn):
    _listeners.append(fn)


def path_for(glid, role):
    return config.PROFILES_DIR / str(glid) / f"{role}.md"


def rebuild(glid, role, trigger_event_id=None):
    t0 = time.time()
    m = threads.build(glid, role)
    ev = store.event(trigger_event_id) if trigger_event_id else None
    # freshness = trigger event entering the store -> file on disk; estimate before writing, then log exact
    text, report = render.render(m, freshness_ms=((time.time() - ev["ingested_at"]) * 1000) if ev else None)
    p = path_for(glid, role)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    written = time.time()
    fresh = (written - ev["ingested_at"]) * 1000 if ev else None
    store.log_render(glid=str(glid), role=role, path=str(p), trigger_event_id=trigger_event_id,
                     event_ingested_at=ev["ingested_at"] if ev else None, written_at=written,
                     freshness_ms=fresh, tokens=report["tokens"], last_event_ts=m.last_event_ts)
    info = {"glid": str(glid), "role": role, "path": str(p), "freshness_ms": fresh, "build_ms": (written - t0) * 1000,
            "tokens": report["tokens"], "redactions": report["redactions"], "schema_ok": report["schema_ok"],
            "cold": m.cold, "threads": [t.status for t in m.threads], "trigger_event_id": trigger_event_id}
    for fn in _listeners:
        try:
            fn(info)
        except Exception:
            pass
    return info


def read(glid, role, build_if_missing=True):
    p = path_for(glid, role)
    if not p.exists() and build_if_missing:
        rebuild(glid, role)
    return p.read_text(encoding="utf-8")


# every new event rebuilds that GLID's file for that role, inline
store.on_event(lambda glid, role, eid: rebuild(glid, role, trigger_event_id=eid))
