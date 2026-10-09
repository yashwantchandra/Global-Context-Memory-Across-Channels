"""Pick demo GLIDs with the most COMPLETE data. Prints ids, scores and which checks passed, never content.

A good demo customer has every section of the file filled from real data, at least two live (non-stale)
threads with different statuses, and history on several channels, so the bot has something to resume.
"""
import json
from collections import Counter

from . import config, store, threads


def _sources(glid, role):
    return Counter(r[0] for r in store.conn().execute(
        "SELECT source FROM events WHERE glid=? AND role=? AND synthetic=0", (glid, role)))


def seller_checks(g):
    m = threads.seller_memory(g)
    p = store.profile(g) or {}
    src = _sources(g, "seller")
    live = [t for t in m.threads]
    c = {
        "company": bool(p.get("company_name")),
        "city": bool(p.get("seller_city")),
        "categories": bool(p.get("top_category_1")),
        "vani_history": src.get("bot_call", 0) >= 2,
        "vani_summary": any(e["payload"].get("summary") for e in store.events(g, "seller") if e["source"] == "bot_call"),
        "enquiries_30d": any(t.status == "LEADS_PENDING" for t in live),
        "callback_or_meeting": any(t.status in ("CALLBACK_DUE", "MEETING_FIXED") for t in live),
        "3_threads": len(live) >= 3,
        "4+_channels": len(src) >= 4,
        "guardrail": len(m.guardrails) >= 1,
    }
    return c, m, src


def buyer_checks(g):
    m = threads.buyer_memory(g)
    src = _sources(g, "buyer")
    evs = store.events(g, "buyer")
    enq = [e for e in evs if e["source"] == "enquiry"]
    live = [t for t in m.threads if t.status != "STALE"]
    c = {
        "city": any(e["payload"].get("buyer_city") for e in enq),
        "company_or_role": any(e["payload"].get("buyer_company") or e["payload"].get("buyer_designation") for e in enq),
        "requirement_text": sum(1 for e in enq if len(e["payload"].get("message") or "") > 40) >= 1,
        "product_names": all(t.title and t.title != "requirement" for t in m.threads),
        "seller_names": any("sellers:" in t.detail for t in m.threads),
        "2_live_threads": len(live) >= 2,
        "distinct_statuses": len({t.status for t in live}) >= 2,
        "3+_channels": len(src) >= 3,
        "recent_14d": bool(m.threads) and (config.as_of() - max(t.last_touch for t in m.threads)).days <= 14,
    }
    return c, m, src


def _best(cands, fn, top):
    rows = []
    for g in cands:
        c, m, src = fn(g)
        rows.append((sum(c.values()), len(c), g, [t.status for t in m.threads], dict(src), [k for k, v in c.items() if not v]))
    rows.sort(key=lambda r: (r[0], sum(r[4].values())), reverse=True)
    # diversity: prefer different top-thread statuses among the picks
    picked, seen = [], set()
    for r in rows:
        if len(picked) >= top:
            break
        if r[3] and r[3][0] in seen and len(rows) > top * 3:
            continue
        picked.append(r)
        if r[3]:
            seen.add(r[3][0])
    return picked, rows


def run(top=3, save=True):
    c = store.conn()
    sellers = [r[0] for r in c.execute("SELECT glid FROM profiles WHERE synthetic=0")]
    s_pick, s_rows = _best(sellers, seller_checks, top)
    print("== SELLERS  (score/max, glid, threads, sources, missing checks)")
    for r in s_pick:
        print(f"  {r[0]}/{r[1]}  {r[2]}  {r[3]}  {r[4]}  missing={r[5]}")
    print(f"  ({sum(1 for r in s_rows if r[0] == r[1])} sellers pass every check)")

    buyers = [r[0] for r in c.execute(
        "SELECT glid FROM events WHERE role='buyer' AND synthetic=0 GROUP BY glid "
        "HAVING COUNT(DISTINCT source) >= 2 AND COUNT(*) >= 3")]
    b_pick, b_rows = _best(buyers, buyer_checks, top)
    print(f"== BUYERS  ({len(buyers)} candidates)")
    for r in b_pick:
        print(f"  {r[0]}/{r[1]}  {r[2]}  {r[3]}  {r[4]}  missing={r[5]}")
    print(f"  ({sum(1 for r in b_rows if r[0] == r[1])} buyers pass every check)")

    if save:
        out = {"seller": [r[2] for r in s_pick], "buyer": [r[2] for r in b_pick]}
        (config.ROOT / "data" / "demo.json").write_text(json.dumps(out))
        print("saved data/demo.json:", out)
