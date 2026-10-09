"""Freshness and size evidence from the render log (for the slide and samples/)."""
from . import store


def _pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))] if v else None


def summary():
    c = store.conn()
    fresh = [r[0] for r in c.execute("SELECT freshness_ms FROM renders WHERE freshness_ms IS NOT NULL")]
    toks = {role: [r[0] for r in c.execute("SELECT tokens FROM renders WHERE role=?", (role,))] for role in ("buyer", "seller")}
    return {
        "event_to_file_ms": {"n": len(fresh), "p50": _pct(fresh, .5), "p95": _pct(fresh, .95), "max": max(fresh) if fresh else None},
        "tokens": {r: {"n": len(v), "p50": _pct(v, .5), "max": max(v) if v else None} for r, v in toks.items()},
    }


def run():
    import json
    print(json.dumps(summary(), indent=2))
