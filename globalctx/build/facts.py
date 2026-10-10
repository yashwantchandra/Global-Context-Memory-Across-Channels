"""Deterministic facts: turn a GLID's events into candidate rows per section.

No LLM here. Each section gets a list of Row candidates; select.py later
applies caps, ranking, closure and the size budget.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from globalctx import config, store
from globalctx.build.identity import display_name, preferred_language
from globalctx.build.privacy import scrub


@dataclass
class Row:
    text: str
    score: float = 0.0
    ts: str = ""
    channel: str = ""
    pinned: bool = False
    synthetic: bool = False


@dataclass
class Facts:
    glid: str
    role: str
    sections: dict = field(default_factory=dict)       # section -> [Row]
    known: list = field(default_factory=list)          # Do-Not-Ask candidates (fact, section it depends on)
    sources: set = field(default_factory=set)
    synthetic: bool = False
    data_quality: str = "ok"
    last_event_at: str = ""
    opening_context: dict = field(default_factory=dict)  # small summary handed to the LLM
    contact: str = None                                  # the person's own name, if they told us


def d(ts):
    """'2026-09-11T17:14:40' -> '11 Sep'"""
    try:
        return datetime.fromisoformat(ts[:19]).strftime("%d %b")
    except ValueError:
        return ts[:10]


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def flag(v):
    return num(v) >= 1


def recency(ts):
    """Higher for newer: days before AS_OF, negated."""
    try:
        return -(config.as_of() - datetime.fromisoformat(ts[:19])).total_seconds() / 86400
    except ValueError:
        return -9999


def in_window(events, source):
    start = config.window_start(source).isoformat()
    return [e for e in events if e["source"] == source and e["ts"] >= start]


def top(counter, n=3):
    return [k for k, _ in counter.most_common(n) if k and k != "-"]


def _sessions(events):
    """Our own channel sessions (summaries written at session end), newest first."""
    return [e for e in in_window(events, "session") if e["payload"].get("kind") == "summary"]


def _session_rows(f, sessions):
    """Pinned latest session + its open threads + its Do-Not-Ask facts. Shared by both roles."""
    past, threads = [], []
    for i, s in enumerate(sessions):
        p = s["payload"]
        label = {"voice": "Voice call", "phone": "Phone call", "chat": "Chat"}.get(s["channel"], s["channel"])
        text = f"{d(s['ts'])} · {label} (our bot): {p.get('summary', '')}"
        past.append(Row(scrub(text), score=recency(s["ts"]) + 1000 * (i == 0), ts=s["ts"],
                        channel=s["channel"], pinned=(i == 0), synthetic=bool(s["synthetic"])))
        # threads and known facts come from the latest conversation that actually happened (not a voicemail)
        real = [x for x in sessions if x["payload"].get("disposition") != "no_conversation"]
        if real and s is real[0]:
            later = s["ts"]
            for t in p.get("open_threads") or []:
                threads.append(Row(scrub(f"{t} (from {label.lower()}, {d(s['ts'])})"), score=300, ts=later))
            if p.get("next_step") and not p.get("callback"):
                threads.append(Row(scrub(f"Next step agreed: {p['next_step']} ({label.lower()}, {d(s['ts'])})"),
                                   score=380, ts=later))
            if p.get("callback"):
                threads.append(Row(scrub(f"Callback promised: {p['callback']} (said on {label.lower()}, {d(s['ts'])})"),
                                   score=400, ts=later))
            for k in ("requirement", "quantity", "callback"):  # language comes from Identity
                if p.get(k):
                    f.known.append((f"{k}: {p[k]}", "Past Conversations"))
    return past, threads


REQUEST_LABEL = {"catalogue_update": "Catalogue update", "price_update": "Price update",
                 "requirement": "Requirement to post", "enquiry": "Enquiry to send"}


def _identity_and_requests(f, S, events, p, threads):
    """Identity row (name + preferred language) and pending requests raised on our channels."""
    name = display_name(p, f.role)
    lang, src = preferred_language(events, p, f.role)
    # newest wins: a name set by the team (source 'contact') or one the user said in a conversation
    contact = next((e["payload"]["contact_name"] for e in events
                    if (e["source"] == "contact" or (e["source"] == "session" and e["payload"].get("kind") == "summary"))
                    and e["payload"].get("contact_name")), None)
    label = "Business" if f.role == "seller" else "Name"
    who = f"{label}: {name or 'unknown'}"
    if contact and (f.role == "seller" or not name):
        who += f" · Contact: {contact}"
    elif f.role == "seller":
        who += " · Contact: not known yet (ask once)"
    S["Identity"].append(Row(f"{who} · Preferred language: {lang} ({src})"))
    if f.role == "seller":
        if name:
            f.known.append(("business name", "Identity"))
        if contact:
            f.known.append(("contact name", "Identity"))
    elif name or contact:
        f.known.append(("name", "Identity"))
    f.contact = contact
    f.known.append((f"preferred language: {lang}", "Identity"))
    for r in [e for e in events if e["source"] == "request" and e["payload"].get("status", "pending") == "pending"]:
        rp = r["payload"]
        what = " · ".join(x for x in (rp.get("product"), rp.get("details"), rp.get("quantity"), rp.get("price")) if x)
        threads.append(Row(scrub(f"Pending {REQUEST_LABEL.get(rp.get('type'), 'request').lower()}: {what} "
                                 f"(raised {d(r['ts'])})"), score=390, ts=r["ts"], synthetic=bool(r["synthetic"])))
    return name, lang


# ---------------------------------------------------------------- seller

def seller_facts(glid) -> Facts:
    events = store.events_in_lookback(glid, "seller")
    f = Facts(str(glid), "seller")
    profiles = [e for e in events if e["source"] == "profile"]
    p = profiles[0]["payload"] if profiles else {}
    live = [e for e in events if e["source"] != "profile"]
    f.synthetic = any(e["synthetic"] for e in events)
    f.last_event_at = max((e["ts"] for e in live), default="")
    S = defaultdict(list)

    # Snapshot (fixed rows)
    if p:
        f.sources.add("profile")
        place = ", ".join(x for x in (p.get("seller_city"), p.get("seller_state")) if x)
        S["Snapshot"].append(Row(" · ".join(x for x in (p.get("company_name"), place, p.get("business_type"),
                                                        p.get("annual_turnover") and f"turnover {p['annual_turnover']}") if x)))
        cats = [c for c in (p.get("top_category_1"), p.get("top_category_2"), p.get("top_category_3")) if c]
        acct = "paid" if p.get("is_paid") == "1" else "free"
        S["Snapshot"].append(Row(f"Categories: {', '.join(cats) or 'unknown'} · {acct} listing · "
                                 f"GST {'verified' if p.get('gst_verified_flag') == '1' else 'not verified'} · "
                                 f"mobile {'verified' if p.get('mobile_verified_flag') == '1' else 'not verified'}"))
        if place:
            f.known.append((f"city: {place}", "Snapshot"))
        if cats:
            f.known.append((f"categories: {', '.join(cats)}", "Snapshot"))
        if p.get("business_type"):
            f.known.append(("business type", "Snapshot"))
        f.known.append(("GST status", "Snapshot"))

    # Buyer Demand by Product: enquiries, buyer calls and BuyLeads grouped per product, then a totals row
    enq = in_window(events, "enquiry")
    msgs = in_window(events, "enquiry_message")
    bls = in_window(events, "buylead")
    calls = in_window(events, "buyer_call")
    for src, rows in (("enquiries", enq), ("buyleads", bls), ("buyer_calls", calls)):
        if rows:
            f.sources.add(src)
    # buyer calls only carry an mcat id: name it after the product most enquired under that mcat
    per_mcat = defaultdict(Counter)
    for e in enq:
        if clean(e["payload"].get("mcat_id")) and clean(e["payload"].get("product")):
            per_mcat[e["payload"]["mcat_id"]][e["payload"]["product"]] += 1
    mcat_label = {m: c.most_common(1)[0][0] for m, c in per_mcat.items()}
    groups = {}

    def group(label):
        return groups.setdefault(label, {"enq": [], "calls": [], "bls": []})

    for e in enq:
        ep = e["payload"]
        label = mcat_label.get(ep.get("mcat_id")) or _label(ep.get("product")) or _label(ep.get("keyword"))
        if label:
            group(label)["enq"].append(e)
    for c in calls:
        if c["payload"].get("mcat_id") in mcat_label:  # unmatched calls are only in the totals
            group(mcat_label[c["payload"]["mcat_id"]])["calls"].append(c)
    for b in bls:
        kw = _label(b["payload"].get("keyword"))
        if kw:
            group(next((g for g in groups if _overlap(g, kw)), kw))["bls"].append(b)
    ranked = sorted(groups.items(), key=lambda kv: (len(kv[1]["enq"]) + len(kv[1]["calls"]) + len(kv[1]["bls"]),
                                                    max(x["ts"] for x in kv[1]["enq"] + kv[1]["calls"] + kv[1]["bls"])),
                    reverse=True)
    for label, g in ranked[:3]:
        parts = []
        if g["enq"]:
            e0, unread_n = g["enq"][0], sum(1 for e in g["enq"] if not e["payload"].get("read"))
            parts.append(f"{len(g['enq'])} enquir{'y' if len(g['enq']) == 1 else 'ies'}"
                         + (f" ({unread_n} unread)" if unread_n else "")
                         + f", latest {d(e0['ts'])} from {e0['payload'].get('buyer_city') or 'India'}")
        if g["calls"]:
            ans = sum(1 for c in g["calls"] if c["payload"].get("status") == "Connected")
            parts.append(f"{len(g['calls'])} buyer call{'s' * (len(g['calls']) > 1)} ({ans} answered)")
        if g["bls"]:
            parts.append(f"{len(g['bls'])} BuyLead{'s' * (len(g['bls']) > 1)} bought")
        S["Buyer Demand by Product"].append(Row(f"{label[:45]}: " + " · ".join(parts),
                                                synthetic=any(x["synthetic"] for x in g["enq"][:1])))
    totals = []
    if enq:
        opened = sum(1 for e in enq if e["payload"].get("read"))
        replied = len({m["payload"]["query_id"] for m in msgs if m["payload"].get("from") == "seller"})
        totals.append(f"{len(enq)} enquiries, {opened} opened, {replied} replied")
    if calls:
        conn = [c for c in calls if c["payload"].get("status") == "Connected"]
        avg = sum(c["payload"].get("talk_sec", 0) for c in conn) / len(conn) if conn else 0
        totals.append(f"{len(calls)} buyer calls, {100 * len(conn) // len(calls)}% answered, avg {int(avg)}s")
    if bls:
        totals.append(f"{len(bls)} BuyLeads (45d)")
    if totals:
        S["Buyer Demand by Product"].append(Row("Totals (90d): " + " · ".join(totals), pinned=True))

    # Responses & Calls (fixed rows: VANI, exec)
    vani = in_window(events, "vani_call")
    if vani:
        f.sources.add("vani_calls")
        disp = Counter(v["payload"].get("disposition") for v in vani)
        S["Responses & Calls"].append(Row(
            f"VANI bot calls (90d): {len(vani)} answered · " + ", ".join(f"{k} {n}" for k, n in disp.most_common(3))
            + f" · last: {vani[0]['payload'].get('disposition')} ({d(vani[0]['ts'])})"))
    ex = in_window(events, "exec_call")
    if ex:
        f.sources.add("exec_calls")
        ans = sum(1 for x in ex if x["payload"].get("status") == "Answered")
        S["Responses & Calls"].append(Row(f"Executive calls (60d): {len(ex)}, {ans} answered · "
                                          f"last {d(ex[0]['ts'])} {ex[0]['payload'].get('status')}"))

    # Past Conversations (ranked; our latest session pinned)
    sessions = _sessions(events)
    past, threads = _session_rows(f, sessions)
    if sessions:
        f.sources.add("sessions")
    for v in vani:
        s = v["payload"].get("summary")
        if s and num(v["payload"].get("duration")) >= 15:
            past.append(Row(scrub(f"{d(v['ts'])} · VANI call: {s}"), score=recency(v["ts"]), ts=v["ts"], channel="voice"))
    wab = in_window(events, "whatsapp_bot")
    if wab:
        f.sources.add("whatsapp")
    seen = set()
    for w in wab:  # one row per WhatsApp session, newest message that the seller typed
        pw = w["payload"]
        if pw.get("session_id") in seen or not pw.get("user_message"):
            continue
        seen.add(pw.get("session_id"))
        past.append(Row(scrub(f"{d(w['ts'])} · WhatsApp bot: seller wrote \"{pw['user_message'][:60]}\""
                              + (f" ({pw['intent']})" if pw.get("intent") else "")),
                        score=recency(w["ts"]), ts=w["ts"], channel="whatsapp"))
    for x in in_window(events, "pns"):
        px = x["payload"]
        if px.get("role_on_call") == "SELLER" and px.get("products"):
            f.sources.add("pns")
            past.append(Row(scrub(f"{d(x['ts'])} · Buyer call: discussed {px['products']}"
                                  + (f", price {px['prices']}" if px.get("prices") else "")),
                            score=recency(x["ts"]), ts=x["ts"], channel="phone"))

    # Open Threads (state-based): VANI call-later, unread enquiries
    last_contact = max([s["ts"] for s in sessions] + [v["ts"] for v in vani], default="")
    if vani and vani[0]["payload"].get("disposition") == "Call Later / Busy" and vani[0]["ts"] >= last_contact:
        age_h = -recency(vani[0]["ts"]) * 24
        if age_h <= 48:
            threads.append(Row(f"Asked VANI to call later on {d(vani[0]['ts'])}", score=350, ts=vani[0]["ts"]))
        else:  # closed by age: keep as history, not as an open thread
            past.append(Row(f"{d(vani[0]['ts'])} · VANI call: seller asked to call later, no callback since",
                            score=recency(vani[0]["ts"]), ts=vani[0]["ts"], channel="voice"))
    unread = [e for e in in_window(events, "enquiry") if not e["payload"].get("read")
              and e["ts"] >= config.window_start("enquiry_message").isoformat()]
    if unread:
        threads.append(Row(f"{len(unread)} unread enquir{'y' if len(unread) == 1 else 'ies'} (30d), latest: "
                           f"{unread[0]['payload'].get('product')} ({d(unread[0]['ts'])})", score=200, ts=unread[0]["ts"],
                           synthetic=bool(unread[0]["synthetic"])))

    # Engagement Signals (risk flags rank above metrics)
    eng = []
    if p:
        risks = [name for key, name in (("do_not_call_requested", "asked not to be called"),
                                         ("showed_frustration", "showed frustration on a past call"),
                                         ("asked_if_talking_to_bot", "asked if talking to a bot"),
                                         ("already_in_touch_with_executive", "already in touch with an executive"))
                 if flag(p.get(key))]
        if p.get("past_objections"):
            risks.append("past objections: " + p["past_objections"].split(";")[0])
        if risks:
            eng.append(Row("Caution: " + "; ".join(risks), score=100))
    wam = in_window(events, "whatsapp_msg")
    sent = [w for w in wam if w["payload"].get("sender") == "API"]
    metrics = []
    if p and p.get("eng_pickup_ratio_90d"):
        metrics.append(f"pickup ratio {num(p['eng_pickup_ratio_90d']):.0f}% (90d)")
    if sent:
        metrics.append(f"WhatsApp read rate {100 * sum(w['payload'].get('read') for w in sent) // len(sent)}% (30d)")
    replies_by_seller = sum(1 for w in wam if w["payload"].get("sender") == "USER")
    if replies_by_seller:
        metrics.append(f"{replies_by_seller} WhatsApp replies (30d)")
    if metrics:
        eng.append(Row(" · ".join(metrics), score=10))

    name, lang = _identity_and_requests(f, S, events, p, threads)
    S["Past Conversations"] = past
    S["Open Threads"] = threads
    S["Engagement Signals"] = eng
    f.sections = dict(S)
    f.data_quality = "cold_start" if not events else ("partial" if len(f.sources) < 3 else "ok")
    f.opening_context = {
        "name": name or None,
        "contact": f.contact,
        "language": lang,
        "categories": [c for c in (p.get("top_category_1"), p.get("top_category_2")) if c] if p else [],
        "latest_session": sessions[0]["payload"] if sessions else None,
        # enquiries are FROM buyers TO this seller (leads he can sell to), never his own needs
        "latest_enquiry_from_a_buyer": enq[0]["payload"].get("product") if enq else None,
        "unread_enquiries_from_buyers": len(unread),
        "pending_requests_raised_by_seller": [r.text for r in threads if r.text.startswith(
            ("Pending catalogue update:", "Pending price update:"))][:2],
        "last_vani": vani[0]["payload"].get("disposition") if vani else None,
    }
    return f


# ---------------------------------------------------------------- buyer

GENERIC = {"machine", "machines", "product", "products", "price", "with", "for", "and", "best", "quality", "half", "length"}


def _singular(w):
    return w[:-2] if w.endswith("sses") else w[:-1] if w.endswith("s") and not w.endswith("ss") else w


def _words(text):
    return {_singular(w) for w in "".join(ch if ch.isalnum() else " " for ch in text.lower()).split()
            if len(w) >= 4 and w not in GENERIC}


def _overlap(a, b):
    """Same need when the labels share 2 specific words, or 1 if a label has only one
    ('Gumboots' ~ 'Rubber gumboots', 'Blow Molding Machines' ~ '3 Phase Blow Molding Machine', but not
    'Plastic Containers' ~ 'Plastic Bottle Making Machine')."""
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) >= min(2, len(wa), len(wb))


def _label(text):
    """Search keywords can be long listing titles: keep the part before the first comma."""
    import re
    from urllib.parse import unquote_plus
    return re.sub(r"\s*\([^)]*\)", "", unquote_plus(clean(text)).split(",")[0]).strip(" :-")[:45]


def clean(v):
    """Treat placeholder values ('-', '--', 'NA') as missing."""
    v = (str(v) if v is not None else "").strip()
    return "" if v.strip("-") == "" or v.upper() in ("NA", "NONE", "NULL") else v


def strip_greeting(text):
    import re
    return re.sub(r"^(Hi|Hello|Namaste)!?\s+[^,!]{1,40}[,!]\s*", "", text or "")


def buyer_facts(glid) -> Facts:
    events = store.events_in_lookback(glid, "buyer")
    f = Facts(str(glid), "buyer")
    profiles = [e for e in events if e["source"] == "profile"]
    p = profiles[0]["payload"] if profiles else {}
    live = [e for e in events if e["source"] != "profile"]
    f.synthetic = any(e["synthetic"] for e in events)
    f.last_event_at = max((e["ts"] for e in live), default="")
    S = defaultdict(list)

    if p:
        f.sources.add("profile")
        S["Snapshot"].append(Row(" · ".join(x for x in (
            p.get("city") and f"{p['city']}, {p.get('country') or 'India'}",
            clean(p.get("customer_type")) and f"{clean(p['customer_type'])} buyer") if x)))
        S["Snapshot"].append(Row(" · ".join(x for x in (
            p.get("registered_since") and f"on IndiaMART {p['registered_since']}",
            p.get("rating") and f"rating {p['rating']} ({p.get('rating_count') or 0})") if x) or "Profile details limited"))
        if p.get("city"):
            f.known.append((f"city: {p['city']}", "Snapshot"))

    acts = in_window(events, "buyer_activity")
    if acts:
        f.sources.add("buyer_activity")
    enq_acts = [a for a in acts if a["payload"].get("type") in ("ENQ", "BL")]

    # Buying Needs: one row per product/category (searches, enquiries, sellers called, requirement), then totals
    groups = {}
    for a in acts:  # newest first, so each group's first item is its latest action
        ap = a["payload"]
        label = _label(ap.get("category")) or _label(ap.get("keyword"))
        if label and ap.get("type") in ("Search", "Browse", "ENQ", "C2C", "BL"):
            key = next((g for g in groups if g.lower() == label.lower() or _overlap(g, label)), label)
            groups.setdefault(key, []).append(a)
    leads = in_window(events, "buylead")
    req_label = upd = None
    # requirements the buyer stated on our channels, newest first
    said = [e for e in in_window(events, "session") if e["payload"].get("kind") == "summary"
            and (e["payload"].get("quantity") or e["payload"].get("requirement"))]
    if leads:
        f.sources.add("buyleads")
        req_label = _label(leads[0]["payload"].get("title")) or "Requirement"
        merged = [x for g in [g for g in groups if _overlap(g, req_label)] for x in groups.pop(g)]
        groups[req_label] = sorted(merged, key=lambda x: x["ts"], reverse=True)

    def need_row(label, items, head=(), skip_bl=False):
        """Stats are dropped least-important first (searches, then last date) so the row fits in MAX_ROW_CHARS."""
        types = Counter(x["payload"].get("type") for x in items)
        called = {x["payload"].get("seller_glid") for x in items if x["payload"].get("type") == "C2C"
                  and x["payload"].get("seller_glid") not in (None, "0")}
        parts = [(9, h) for h in head]  # (priority, text): higher survives longer
        if types.get("BL") and not skip_bl:
            parts.append((8, "requirement posted" + (f" {types['BL']}x" if types["BL"] > 1 else "")))
        if types.get("ENQ"):
            parts.append((7, f"{types['ENQ']} enquir{'y' if types['ENQ'] == 1 else 'ies'}"))
        if called:
            parts.append((6, f"{len(called)} seller{'s' * (len(called) > 1)} called"))
        if types.get("Search") or types.get("Browse"):
            parts.append((4, f"searched {types['Search'] + types['Browse']}x"))
        if items and not head:
            parts.append((5, f"last {d(items[0]['ts'])}"))
        text = lambda: f"{label}: " + (" · ".join(t for _, t in parts) or "no activity in 30d")
        while len(text()) > config.MAX_ROW_CHARS - 12 and len(parts) > 1:  # 12 = room for " (synthetic)"
            parts.remove(min(parts, key=lambda x: x[0]))
        return text()

    if req_label:  # the posted requirement is the need to talk about: always first, details on their own row
        lp = leads[0]["payload"]
        conn = lp.get("suppliers_connected")
        head = [f"requirement posted {d(leads[0]['ts'])}"] + ([f"{conn} suppliers connected"] if conn is not None else [])
        S["Buying Needs"].append(Row(need_row(req_label, groups[req_label], head, skip_bl=True),
                                     synthetic=bool(leads[0]["synthetic"])))
        detail = []
        upd = next((e for e in said if e["ts"] >= leads[0]["ts"]
                    and _overlap(e["payload"].get("requirement") or req_label, req_label)), None)
        if upd:  # the buyer confirmed or changed the requirement on one of our channels: that wins
            up = upd["payload"]
            q, r = up.get("quantity") or "", up.get("requirement") or ""
            detail.append((r if q and q.split()[0] in r else " · ".join(x for x in (q, r) if x))
                          + f" (buyer confirmed on {upd['channel']}, {d(upd['ts'])})")
        elif lp.get("details"):
            age = int(-recency(lp.get("confirmed_at") or leads[0]["ts"]))
            state = f"confirmed {age}d ago" if lp.get("confirmed_at") else f"unconfirmed for {age}d"
            detail.append(f"{lp['details']} ({state}" + ("; read back once" if age * 24 > 48 else "") + ")")
        if detail:
            S["Buying Needs"].append(Row("Details: " + " · ".join(detail), pinned=True,
                                         synthetic=bool(leads[0]["synthetic"])))
        f.known.append((f"requirement: {req_label}", "Buying Needs"))
    # a requirement stated on our channels that is not the posted one is its own need
    spoken = next((e for e in said if e is not upd and e["payload"].get("requirement")
                   and not (req_label and _overlap(e["payload"]["requirement"], req_label))), None)
    if spoken:
        sp = spoken["payload"]
        need = _label(sp["requirement"])
        S["Buying Needs"].append(Row(f"{need[:1].upper() + need[1:]}: said on {spoken['channel']} {d(spoken['ts'])}"
                                     + (f" · {sp['quantity']}" if sp.get("quantity") else ""),
                                     synthetic=bool(spoken["synthetic"])))
    others = sorted(((g, items) for g, items in groups.items() if g != req_label),
                    key=lambda kv: (kv[1][0]["ts"], len(kv[1])), reverse=True)
    shown = others[: 3 - bool(req_label) - bool(spoken)]
    for label, items in shown:
        S["Buying Needs"].append(Row(need_row(label, items), synthetic=bool(items[0]["synthetic"])))
    if not groups and p.get("last_enquiry_title"):
        S["Buying Needs"].append(Row(f"{p['last_enquiry_title'][:45]}: last enquiry (profile)"))
    past = in_window(events, "past_need")
    if past:
        S["Buying Needs"].append(Row("Earlier (12 mo): " + ", ".join(
            f"{e['payload'].get('item')} ({datetime.fromisoformat(e['ts'][:19]).strftime('%b')})" for e in past[:3]),
            synthetic=any(e["synthetic"] for e in past)))
    contacted = {a["payload"].get("seller_glid") for a in acts
                 if a["payload"].get("type") in ("ENQ", "C2C") and a["payload"].get("seller_glid") not in (None, "0")}
    enq90 = int(num(p.get("enq_90d"))) if p else 0
    if enq_acts or enq90 or contacted:
        if contacted:
            f.sources.add("sellers")
        # activity log is fresher than the snapshot counter; show the counter only when it adds information
        S["Buying Needs"].append(Row(f"Totals (30d): {len(enq_acts)} enquir{'y' if len(enq_acts) == 1 else 'ies'}"
                                     + (f" ({enq90} in 90d)" if enq90 > len(enq_acts) else "")
                                     + f" · {len(contacted)} seller{'s' * (len(contacted) != 1)} contacted",
                                     pinned=True))
    cats = Counter({g: len(items) for g, items in shown})
    if cats:
        f.known.append((f"interested in: {', '.join(top(cats, 3))}", "Buying Needs"))

    # KYC
    if p:
        S["KYC"].append(Row(f"Mobile {p.get('mobile_verified') or 'unknown'} · email {p.get('email_verified') or 'not verified'}"
                            f" · GST {p.get('gst_status') or 'not available'}"))

    # Past Conversations
    wab = in_window(events, "whatsapp_bot")
    sessions = _sessions(events)
    past, threads = _session_rows(f, sessions)
    if sessions:
        f.sources.add("sessions")
    if wab:
        f.sources.add("whatsapp")
    for w in wab:
        past.append(Row(scrub(f"{d(w['ts'])} · WhatsApp: {strip_greeting(w['payload'].get('text', ''))[:90]}"),
                        score=recency(w["ts"]), ts=w["ts"], channel="whatsapp"))
    if p.get("last_call_summary"):
        past.append(Row(scrub(f"Last call with IndiaMART: {p['last_call_summary'][:100]}"), score=-500, channel="phone"))

    # Open Threads: posted requirement with no follow-up session since
    if leads and (not sessions or sessions[0]["ts"] < leads[0]["ts"]):
        threads.append(Row(f"Requirement '{leads[0]['payload'].get('title')}' posted {d(leads[0]['ts'])}, "
                           f"no confirmation of a supplier yet", score=250, ts=leads[0]["ts"]))

    if leads and leads[0]["payload"].get("unknown"):
        threads.append(Row("Still unknown, ask only if needed: " + ", ".join(leads[0]["payload"]["unknown"]),
                           score=150, ts=leads[0]["ts"], synthetic=bool(leads[0]["synthetic"])))

    # Engagement Signals
    eng = []
    if acts:
        types = Counter(a["payload"].get("type") for a in acts)
        eng.append(Row(f"{len(acts)} actions in 30d: " + ", ".join(f"{k} {n}" for k, n in types.most_common(4)), score=10))
    if p.get("buying_behaviour"):
        eng.insert(0, Row(f"Buys like this: {p['buying_behaviour']}", score=50))
    if p.get("negative_feedback_90d"):
        eng.insert(0, Row(f"Caution: {p['negative_feedback_90d']} negative feedback ticket(s) in 90d", score=100))

    name, lang = _identity_and_requests(f, S, events, p, threads)
    S["Past Conversations"] = past
    S["Open Threads"] = threads
    S["Engagement Signals"] = eng
    f.sections = dict(S)
    f.data_quality = "cold_start" if not events else ("partial" if not acts and not sessions else "ok")
    f.opening_context = {
        "name": name or None,
        "contact": f.contact,
        "language": lang,
        "top_categories": top(cats, 2),
        "latest_session": sessions[0]["payload"] if sessions else None,
        "latest_enquiry": (clean(enq_acts[0]["payload"].get("keyword")) if enq_acts else p.get("last_enquiry_title")) if p else None,
        "latest_requirement": leads[0]["payload"].get("title") if leads else None,
        "requirement_posted": d(leads[0]["ts"]) if leads else None,
        "requirement_details": leads[0]["payload"].get("details") if leads else None,
        "suppliers_connected": leads[0]["payload"].get("suppliers_connected") if leads else None,
    }
    return f


def facts_for(glid, role) -> Facts:
    return seller_facts(glid) if role == "seller" else buyer_facts(glid)
