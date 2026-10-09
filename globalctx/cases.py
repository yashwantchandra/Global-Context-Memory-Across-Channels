"""Load case-study GLIDs (made-up IDs, all events labelled synthetic) into the event store.

  python -m globalctx.cases data/cases/buyer_910000101_gumboots.json [more.json ...]

A case file is JSON: glid, role, profile{}, contact{name/language}, buylead{ts,title,details,
suppliers_connected,unknown[]}, past_needs[{ts,item}], optional activity[{ts,type,category,keyword}].
The .md is then built by the normal pipeline, so it keeps the fixed schema and survives rebuilds.
"""
import json
import sys

from globalctx import refresh, store


def events_from_case(c):
    g, role = str(c["glid"]), c["role"]
    ev = lambda source, ts, ext, payload, channel=None: {  # noqa: E731
        "glid": g, "role": role, "source": source, "channel": channel, "ts": ts,
        "ext_id": f"case:{g}:{ext}", "payload": payload, "synthetic": 1}
    out = []
    if c.get("profile"):
        out.append(ev("profile", "2026-10-01T00:00:00", "profile", c["profile"]))
    if c.get("contact"):
        out.append(ev("contact", "2026-10-01T00:00:01", "contact", c["contact"], "manual"))
    if c.get("buylead"):
        b = c["buylead"]
        out.append(ev("buylead", b["ts"], f"bl:{b.get('id', 'bl')}", {k: v for k, v in b.items() if k != "ts"},
                      "marketplace"))
    for i, p in enumerate(c.get("past_needs") or []):
        out.append(ev("past_need", p["ts"], f"past:{i}", {"item": p["item"]}, "marketplace"))
    for i, a in enumerate(c.get("activity") or []):
        out.append(ev("buyer_activity", a["ts"], f"act:{i}", {k: v for k, v in a.items() if k != "ts"},
                      "marketplace"))
    return g, role, out


def load(path):
    c = json.loads(open(path, encoding="utf-8").read())
    g, role, evs = events_from_case(c)
    with store.connect() as conn:
        store.insert_events(conn, evs)
    out = refresh.rebuild_now(g, role, use_llm=True)
    return g, role, out


if __name__ == "__main__":
    store.init()
    for path in sys.argv[1:]:
        g, role, out = load(path)
        print(f"{role}/{g}.md built: {out['chars']} chars, opening by {out['opening_by']}")
