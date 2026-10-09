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
        if i == 0:
            later = s["ts"]
            for t in p.get("open_threads") or []:
                threads.append(Row(scrub(f"{t} (from {label.lower()}, {d(s['ts'])})"), score=300, ts=later))
            if p.get("next_step") and not p.get("callback"):
                threads.append(Row(scrub(f"Next step agreed: {p['next_step']} ({label.lower()}, {d(s['ts'])})"),
                                   score=380, ts=later))
            if p.get("callback"):
                threads.append(Row(scrub(f"Callback promised: {p['callback']} (said on {label.lower()}, {d(s['ts'])})"),
                                   score=400, ts=later))
            for k in ("requirement", "quantity", "callback", "language"):
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
    if name or contact:
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
    events = store.events_for(glid, "seller")
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

    # Leads & Enquiries (fixed rows: counts, latest, buy-leads)
    enq = in_window(events, "enquiry")
    msgs = in_window(events, "enquiry_message")
    bls = in_window(events, "buylead")
    if enq:
        f.sources.add("enquiries")
        opened = sum(1 for e in enq if e["payload"].get("read"))
        replied = len({m["payload"]["query_id"] for m in msgs if m["payload"].get("from") == "seller"})
        prod = Counter(e["payload"].get("product") for e in enq)
        S["Leads & Enquiries"].append(Row(f"Enquiries (90d): {len(enq)} received, {opened} opened, "
                                          f"replied on {replied} (30d) · top: {', '.join(top(prod, 2))}"))
        e0 = enq[0]["payload"]
        S["Leads & Enquiries"].append(Row(
            f"Latest enquiry {d(enq[0]['ts'])}: {e0.get('product')} · a buyer from {e0.get('buyer_city') or 'India'}"
            f" · {'opened' if e0.get('read') else 'UNREAD'}", synthetic=bool(enq[0]["synthetic"])))
    if bls:
        f.sources.add("buyleads")
        kw = Counter(e["payload"].get("keyword") for e in bls)
        S["Leads & Enquiries"].append(Row(f"Buy-leads bought (45d): {len(bls)} · {', '.join(top(kw, 3))}"))

    # Responses & Calls (fixed rows: buyer calls, VANI, exec)
    calls = in_window(events, "buyer_call")
    if calls:
        f.sources.add("buyer_calls")
        conn = [c for c in calls if c["payload"].get("status") == "Connected"]
        avg = sum(c["payload"].get("talk_sec", 0) for c in conn) / len(conn) if conn else 0
        S["Responses & Calls"].append(Row(f"Buyer calls (90d): {len(calls)} received, {len(conn)} connected "
                                          f"({100 * len(conn) // len(calls)}%), avg talk {int(avg)}s"))
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
        "pending_requests_raised_by_seller": [r.text for r in threads if r.text.startswith("Pending")][:2],
        "last_vani": vani[0]["payload"].get("disposition") if vani else None,
    }
    return f


# ---------------------------------------------------------------- buyer

def _seller_names(texts):
    """Company names that WhatsApp templates mention: 'You just spoke with X', 'X (City) responded', 'X tried to reach you'."""
    import re
    pats = [r"spoke with (.+?)(?: 📞| Was|\n|$)",
            r"(?:^|, )([A-Z][\w&.'() -]{2,60}?) \([A-Za-z .]+\)(?: \(also deals in [^)]*\))? responded",
            r"^Hi! (.+?) tried to reach you"]
    names = []
    for t in texts:
        for pat in pats:
            m = re.search(pat, t)
            if m:
                names.append(m.group(1).strip(" *"))
    return names


def clean(v):
    """Treat placeholder values ('-', '--', 'NA') as missing."""
    v = (str(v) if v is not None else "").strip()
    return "" if v.strip("-") == "" or v.upper() in ("NA", "NONE", "NULL") else v


def strip_greeting(text):
    import re
    return re.sub(r"^(Hi|Hello|Namaste)!?\s+[^,!]{1,40}[,!]\s*", "", text or "")


def buyer_facts(glid) -> Facts:
    events = store.events_for(glid, "buyer")
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

    # Enquiries & Status (fixed: counts, latest, last buy-lead)
    # Activity log is fresher than the snapshot counters; show the counter only when it adds information
    enq90 = int(num(p.get("enq_90d"))) if p else 0
    if enq_acts or enq90:
        S["Enquiries & Status"].append(Row(f"{len(enq_acts)} enquiries in last 30d"
                                           + (f" · {enq90} in 90d" if enq90 > len(enq_acts) else "")))
    if enq_acts:
        a = enq_acts[0]["payload"]
        S["Enquiries & Status"].append(Row(f"Latest enquiry {d(enq_acts[0]['ts'])}: {clean(a.get('keyword')) or clean(a.get('category'))}"
                                           + (f" ({clean(a.get('category'))})" if clean(a.get('category')) else ""), synthetic=bool(enq_acts[0]["synthetic"])))
    elif p.get("last_enquiry_title"):
        S["Enquiries & Status"].append(Row(f"Last enquiry: {p['last_enquiry_title']}"))
    leads = in_window(events, "buylead")
    if leads:
        f.sources.add("buyleads")
        lp = leads[0]["payload"]
        extra = []
        if lp.get("suppliers_connected") is not None:
            extra.append(f"{lp['suppliers_connected']} suppliers connected")
        upd = next((e for e in in_window(events, "session") if e["payload"].get("kind") == "summary"
                    and e["ts"] >= leads[0]["ts"] and (e["payload"].get("quantity") or e["payload"].get("requirement"))),
                   None)
        if upd:  # the buyer confirmed or changed the requirement on one of our channels: that wins
            up = upd["payload"]
            label = {"voice": "voice", "phone": "phone", "chat": "chat"}.get(upd["channel"], upd["channel"])
            q, r = up.get("quantity") or "", up.get("requirement") or ""
            extra.append((r if q and q.split()[0] in r else " · ".join(x for x in (q, r) if x))
                         + f" (buyer confirmed on {label}, {d(upd['ts'])})")
        elif lp.get("details"):
            age = int(-recency(lp.get("confirmed_at") or leads[0]["ts"]))
            state = f"confirmed {age}d ago" if lp.get("confirmed_at") else f"unconfirmed for {age}d"
            extra.append(f"{lp['details']} ({state}" + ("; read back once" if age * 24 > 48 else "") + ")")
        conn = extra.pop(0) if extra and "suppliers connected" in extra[0] else None
        S["Enquiries & Status"].append(Row(f"Buy requirement posted {d(leads[0]['ts'])}: {lp.get('title')}"
                                           + (f" · {conn}" if conn else ""), synthetic=bool(leads[0]["synthetic"])))
        if extra:  # requirement details on their own row so they are never cut off
            S["Enquiries & Status"].append(Row("Details: " + " · ".join(extra), synthetic=bool(leads[0]["synthetic"])))
        f.known.append((f"requirement: {leads[0]['payload'].get('title')}", "Enquiries & Status"))

    # Categories Searched (top 3 on one line)
    cats = Counter(a["payload"].get("category") for a in acts if a["payload"].get("category") not in (None, "", "-"))
    past = in_window(events, "past_need")
    past_txt = ", ".join(f"{e['payload'].get('item')} ({datetime.fromisoformat(e['ts'][:19]).strftime('%b')})"
                         for e in past[:3])
    if cats or past:
        parts = [" · ".join(f"{c} ({n})" for c, n in cats.most_common(3))] if cats else []
        if past_txt:
            parts.append(f"earlier (12 mo): {past_txt}")
        S["Categories Searched"].append(Row(" · ".join(parts), synthetic=any(e["synthetic"] for e in past)))
        if cats:
            f.known.append((f"interested in: {', '.join(top(cats, 3))}", "Categories Searched"))

    # Sellers Contacted (company names only, never ids or numbers)
    contacted = {a["payload"].get("seller_glid") for a in acts
                 if a["payload"].get("type") in ("ENQ", "C2C") and a["payload"].get("seller_glid") not in (None, "0")}
    wab = in_window(events, "whatsapp_bot")
    names = list(dict.fromkeys(_seller_names(w["payload"].get("text", "") for w in wab)))
    if contacted or names:
        f.sources.add("sellers")
        S["Sellers Contacted"].append(Row(f"{len(contacted)} sellers contacted (30d)"
                                          + (f" · recent: {', '.join(names[:3])}" if names else "")))
        if names:
            f.known.append(("sellers already contacted", "Sellers Contacted"))

    # KYC
    if p:
        S["KYC"].append(Row(f"Mobile {p.get('mobile_verified') or 'unknown'} · email {p.get('email_verified') or 'not verified'}"
                            f" · GST {p.get('gst_status') or 'not available'}"))

    # Past Conversations
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
