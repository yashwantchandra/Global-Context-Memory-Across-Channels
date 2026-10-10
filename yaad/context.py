"""GET /v1/context lands here: snapshot + ranked threads from the tables -> JSON + the md file (built at query time).

Ranking: open problem > pending promise / callback > fresh activity (24h) > everything else by recency; stale last.
Firewall: a seller's memory only ever holds aggregates about buyers (city, product, qty); buyer identities and
complaints never reach it. guard.check redacts phones/emails/counterparty ids as a second line of defence.
"""
import re
from datetime import datetime
from types import SimpleNamespace

from . import config, db, guard

TIER1 = {"delhi", "new delhi", "mumbai", "bengaluru", "bangalore", "chennai", "kolkata", "hyderabad", "pune",
         "ahmedabad", "noida", "gurgaon", "gurugram", "thane", "ghaziabad"}
TIER2 = {"jaipur", "lucknow", "surat", "kanpur", "nagpur", "indore", "bhopal", "vadodara", "ludhiana", "agra",
         "nashik", "rajkot", "coimbatore", "kochi", "patna", "chandigarh", "faridabad", "meerut", "varanasi", "raipur"}
WHERE = {  # "where are we" in one line, per stage
    "SEARCHING": "searching, no requirement posted yet", "BL_POSTED": "requirement posted, waiting for sellers",
    "SELLERS_CONNECTED": "sellers connected, no real conversation yet", "IN_TALKS": "talking to sellers",
    "PROMISED": "a next step was agreed with us", "CLOSED": "done", "OPEN": "open complaint, with support",
    "LEAD_BOUGHT": "bought a fresh buy-lead, not yet acted on", "ENQUIRY_UNREAD": "buyer enquiries waiting unread",
    "CALLBACK_DUE": "asked us to call back", "CATALOG_ISSUE": "catalog upload stuck", "RESPONDED": "responded",
    "CONTACTED": "spoke to VANI recently",
}


def _hours(ts):
    try:
        return (datetime.now() - datetime.fromisoformat(ts[:19])).total_seconds() / 3600
    except Exception:
        return 1e9


def _d(ts):
    try:
        return datetime.fromisoformat(ts[:19]).strftime("%-d %b")
    except Exception:
        return ""


def _age_days(ts):
    return _hours(ts or "") / 24


def tier(city):
    c = (city or "").strip().lower()
    return "Tier 1" if c in TIER1 else "Tier 2" if c in TIER2 else ("Tier 3" if c else "")


def rank(t):
    if t["kind"] == "problem" and t["stage"] == "OPEN":
        return 0
    if t["stage"] == "CLOSED":
        return 9
    if _hours(t["last_activity"]) > config.STALE_DAYS * 24:
        return 8
    if t["stage"] in ("PROMISED", "CALLBACK_DUE"):
        return 1
    if _hours(t["last_activity"]) <= config.FRESH_HOURS:
        return 2
    return 3


def _v(t, k):
    return (t["facts"].get(k) or {}).get("v")


def _spoken(text):
    """Units the way a person says them in the opening line: '₹240/pc' -> '₹240 per piece', '200 pcs' -> '200 piece'."""
    if not text:
        return text
    s = re.sub(r"\s*/\s*(pcs|pc|piece)\b", " per piece", str(text), flags=re.I)
    return re.sub(r"\b(pcs|pc)\b", "piece", s, flags=re.I)


def _known(t):
    labels = {"qty": "qty", "spec": "spec", "deadline": "needed by", "city": "deliver to", "buyer_city": "buyer city", "price": "price",
              "also_needs": "also needs", "unread_enquiries": "unread enquiries", "missed_calls": "missed buyer calls",
              "callback_asked": "callback asked", "last_vani_outcome": "last VANI outcome", "issue": "issue",
              "about_seller": "about seller"}
    out = []
    for k, f in t["facts"].items():
        if t["kind"] == "problem" and k == "issue":
            continue  # the summary already states the issue
        if k in labels and f.get("v") not in (None, ""):
            src = f.get("src")
            out.append(f"{labels[k]} {f['v']}" + (f" ({src}, {_d(f.get('ts', ''))})" if src == "conversation" else ""))
    return out


def _status(t):
    if t["role"] == "buyer" and t["sellers"]:
        conn = [s for s in t["sellers"]]

        def st(s):
            q = s.get("quote")
            if not q:
                return s["status"]
            when = f" on {_d(s['quote_ts'])}" if s.get("quote_ts") else ""
            what = f" (for {s['quote_qty']})" if s.get("quote_qty") else ""
            earlier = f"earlier quote {q}{when}{what}"
            return earlier if s["status"].startswith("quoted") else f"{earlier}, then {s['status']}"
        label = "1 seller" if len(conn) == 1 else f"{len(conn)} sellers"
        return f"{label}: " + "; ".join(f"{s['name']}: {st(s)}" for s in conn[:3])
    return WHERE.get(t["stage"], t["stage"].lower())


def view(t, i):
    return {"id": t["thread_id"].split("-")[0], "thread_id": t["thread_id"], "title": t["title"], "kind": t["kind"], "stage": t["stage"],
            "where_we_are": WHERE.get(t["stage"], ""), "known": _known(t), "status": _status(t),
            "summary": t["summary"], "next_step": t["next_step"], "last_activity": t["last_activity"],
            "last_channel": t["last_channel"], "parent_thread_id": t["parent_thread_id"], "mcat_id": t["mcat_id"],
            "rank": rank(t)}


def _snapshot(u, role, ths):
    if not u:
        return {}
    s = {k: u.get(k) for k in ("name", "company", "city", "state", "language", "plan", "kyc") if u.get(k)}
    s["tier"] = u.get("tier") or tier(u.get("city"))
    s["activity"] = u.get("activity") or {}
    s["interests"] = u.get("interests") or []
    lang = next((t["links"].get("language") for t in ths if t["links"].get("language")), None)
    if lang:
        s["language"] = lang
    return s


ACTIVITY_LABELS = {"enquiries": "enquiries", "pns": "PNS calls", "bls": "buy-leads",
                   "enquiries_received": "enquiries received", "bls_bought": "buy-leads bought",
                   "reply_rate": "reply rate"}


def card_lines(snap, role, max_interests=6):
    """The 3-line customer card at the top of every memory file."""
    if not snap:
        return []
    state = snap.get("state") if snap.get("state") != snap.get("city") else None
    place = ", ".join(x for x in [snap.get("city"), state] if x)
    place += f" ({snap['tier']})" if snap.get("tier") and place else ""
    who = " · ".join(x for x in [snap.get("company") if role == "seller" else None, snap.get("name"), place,
                                 snap.get("kyc"), snap.get("plan") if role == "seller" else None,
                                 snap.get("language")] if x)
    lines = [f"- {who}"]
    act = snap.get("activity") or {}
    if act:
        lines.append("- Activity (90 days): " + " · ".join(
            f"{v} {ACTIVITY_LABELS.get(k, k)}" if k != "reply_rate" else f"reply rate {v}" for k, v in act.items()))
    ints = snap.get("interests") or []
    if ints:
        more = f" (+{len(ints) - max_interests})" if len(ints) > max_interests else ""
        lines.append(("- Sells: " if role == "seller" else "- Interested in: ") + ", ".join(ints[:max_interests]) + more)
    return lines


def _guardrails(role, u, ths):
    g = []
    flags = (u or {}).get("flags") or {}
    if flags.get("dnd"):
        g.append("DND requested: no pitch; only help with what they ask")
    if any(t["kind"] == "problem" and t["stage"] == "OPEN" for t in ths):
        g.append("Open complaint: acknowledge it first, say support is on it; no new pitches until it is addressed")
    if any(t["stage"] == "PROMISED" for t in ths):
        g.append("Promised steps are pending: say they are in progress; never claim they are done or invent results")
    if flags.get("asked_bot"):
        g.append("Asked if talking to a bot before: say upfront you are IndiaMART's virtual assistant")
    if flags.get("frustrated"):
        g.append("Showed frustration before: be brief, get to the point")
    if flags.get("objection"):
        g.append(f"Past objection: {flags['objection']}")
    if flags.get("has_executive"):
        g.append("In touch with an IndiaMART executive: don't duplicate their pitch")
    g.append("Never reveal any buyer's name, company, phone or messages; share only city, product and quantity"
             if role == "seller" else "Never share a seller's phone or personal details; seller company names only")
    return g


def _opening(role, u, top, ranked):
    first = ((u or {}).get("name") or "").split(" ")[0]
    hi = f"Namaste {first} ji!" if (role == "buyer" and first) else "Namaste ji!"
    if role == "seller" and (u or {}).get("company"):
        hi = f"Namaste ji, {u['company']} se baat ho rahi hai?"
    if not top:
        return "Namaste! Main IndiaMART ki virtual assistant bol rahi hoon. Aaj main aapki kya madad kar sakti hoon?"
    prod, st = top["title"], top["stage"]
    if role == "buyer":
        if top["kind"] == "problem":
            nxt = next((t for t in ranked if t["kind"] != "problem"), None)
            tail = f" Aur aapki {nxt['title']} requirement ka update bhi dena tha, baat karein?" if nxt else \
                " Kya main aur kisi cheez mein madad kar sakti hoon?"
            return f"{hi} Pehle aapki complaint ki baat: humari support team us par kaam kar rahi hai.{tail}"
        if st == "PROMISED":
            return f"{hi} Pichli baar {top['last_channel']} pe aapki {prod} requirement ki baat hui thi. Usi ka update dene ke liye sampark kiya hai. Baat karein?"
        if (top["links"].get("last_search") and _hours(top["links"]["last_search"]) <= config.FRESH_HOURS
                and _age_days(top["created_at"]) >= 2):
            # returning buyer: a fresh search on a thread we already know
            qty = _spoken(_v(top, "qty"))
            quoted = next((s for s in top["sellers"] if s.get("quote")), None)
            past = f" Pichli baar {qty} ki requirement thi" if qty else " Pichli baar bhi aapne ye dekha tha"
            past += f", aur {quoted['name']} ne {_spoken(quoted['quote'])} quote kiya tha." if quoted else "."
            return f"{hi} Aap phir se {prod} dekh rahe hain.{past} Kya abhi bhi wahi requirement hai?"
        if st == "SELLERS_CONNECTED":
            n = len(top["sellers"])
            jude = f"{top['sellers'][0]['name']} jude the" if n == 1 else f"{n} sellers jude the"
            return f"{hi} Aapki {prod} requirement pe {jude}, par baat aage nahi badhi. Kya main aur responsive sellers se connect karwa doon?"
        if st == "BL_POSTED":
            return f"{hi} Aapne {prod} ki requirement daali hai. Kya main abhi achhe sellers se connect karwa doon?"
        if st == "IN_TALKS":
            return f"{hi} {prod} ke liye sellers se baat chal rahi thi. Deal ho gayi ya aur quotes chahiye?"
        return f"{hi} Dekha ki aap {prod} dhoondh rahe hain. Kya main 2-3 achhe sellers se connect karwa doon?"
    hot = next((t for t in ranked if t["stage"] in ("LEAD_BOUGHT", "ENQUIRY_UNREAD") and t is not top), None)
    hot_line = (f" Aur {_v(hot, 'buyer_city') or 'ek buyer'} se {hot['title']} ki fresh requirement bhi aayi hai."
                if hot and _hours(hot["last_activity"]) <= config.FRESH_HOURS else "")
    if st == "CALLBACK_DUE":
        return f"{hi} Aapne dobara call karne ko kaha tha, isliye call kiya hai.{hot_line}"
    if st == "LEAD_BOUGHT":
        q = f", {_v(top, 'qty')}" if _v(top, "qty") else ""
        return f"{hi} {_v(top, 'buyer_city') or 'Ek buyer'} se {prod} ki fresh requirement aayi hai{q}. Quote bhejenge?"
    if st == "ENQUIRY_UNREAD":
        n = _v(top, "unread_enquiries") or 0
        what = "ek enquiry abhi unread hai" if n == 1 else f"{n or 'kuch'} enquiries abhi unread hain"
        return f"{hi} Aapki {prod} ki {what}. Kya main WhatsApp pe bhej doon?"
    if st == "CATALOG_ISSUE":
        return f"{hi} Aapne WhatsApp pe product photo bheji thi jo catalog mein add nahi ho payi. Main madad kar doon?"
    if st == "PROMISED":
        return f"{hi} Pichli baar {top['last_channel']} pe {prod} ki baat hui thi. Usi ka update lene ke liye call kiya hai."
    return f"{hi} Aaj main aapki kya madad kar sakti hoon?"


def _md(glid, role, snap, views, problems, guardrails, opening, meta, keep_summary=True, n_threads=config.MAX_THREADS):
    head = ["---", f"glid: {glid}", f"role: {role}", f"generated_at: {meta['generated_at']}",
            f"last_event_at: {meta['last_event_at']}", f"language: {snap.get('language', 'Hinglish')}",
            f"cold_start: {str(meta['cold']).lower()}", f"synthetic: {str(meta['synthetic']).lower()}", "---",
            f"# {'Buyer' if role == 'buyer' else 'Seller'} memory · {snap.get('company') or snap.get('name') or glid}", ""]
    body = ["## Customer card"] + (card_lines(snap, role) or ["- (no profile on record)"]) + ["", "## Open problems"]
    body += [f"- {p['id']} · {p['title']} [OPEN] {_d(p['last_activity'])}: {p['summary']} → {p['next_step']}"
             for p in problems] or ["- none"]
    body += ["", "## Threads"]
    shown = [v for v in views if v["kind"] != "problem"][:n_threads]
    for v in shown:
        body.append(f"{v['id']}. **{v['title']}** [{v['stage']}] last {_d(v['last_activity'])} via {v['last_channel']}")
        if v["known"]:
            body.append("   - Known: " + " · ".join(v["known"]))
        body.append(f"   - Status: {v['status']}")
        if keep_summary and v["summary"]:
            body.append(f"   - Summary: {v['summary']}")
        if v["next_step"]:
            body.append(f"   - Next: {v['next_step']}")
    if not shown:
        body.append("- none open")
    body += ["", "## Guardrails"] + [f"- {g}" for g in guardrails] + ["", "## Suggested opening", f"> {opening}", ""]
    return "\n".join(head + body)


def build(glid, role):
    u = db.user(glid, role)
    ths = db.threads(glid, role)
    cold = not u and not ths
    ranked = sorted(ths, key=lambda t: (rank(t), -(datetime.fromisoformat(t["last_activity"][:19]).timestamp())))
    live = [t for t in ranked if rank(t) < 9]
    views = [view(t, i + 1) for i, t in enumerate(ranked)]  # closed threads stay visible, ranked last
    problems = [v for v in views if v["kind"] == "problem" and v["stage"] == "OPEN"]
    top = live[0] if live else None
    snap = _snapshot(u, role, ths)
    guardrails = _guardrails(role, u, ths)
    opening = _opening(role, u, top, live)
    evs = db.events(glid, role)
    meta = {"generated_at": db.now(), "last_event_at": max((e["ts"] for e in evs), default="none"), "cold": cold,
            "synthetic": bool((u or {}).get("synthetic") or any(e["synthetic"] for e in evs))}
    # firewall: every counterparty id + their names must never appear in this user's file
    forbidden = set()
    if role == "seller":
        for e in evs:
            if e["counterparty"]:
                forbidden.add(e["counterparty"])
                other = db.any_user(e["counterparty"]) or {}
                forbidden |= {x for x in (other.get("name"), other.get("company")) if x}
    budget = config.TOKEN_BUDGET[role]
    for keep_summary, n in ((True, config.MAX_THREADS), (False, config.MAX_THREADS), (False, 2), (False, 1)):
        md = _md(glid, role, snap, views, problems, guardrails, opening, meta, keep_summary, n)
        if guard.tokens(md) <= budget:
            break
    md, report = guard.check(md, SimpleNamespace(forbidden=forbidden))
    return {"glid": glid, "role": role, "cold_start": cold, "snapshot": snap, "threads": views, "problems": problems,
            "guardrails": guardrails, "opening": opening, "md": md, "tokens": report["tokens"],
            "redactions": report["redactions"], "last_event_at": meta["last_event_at"], "synthetic": meta["synthetic"]}
