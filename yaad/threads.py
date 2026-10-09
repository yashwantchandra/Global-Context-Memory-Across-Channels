"""Thread engine: events -> Memory (who, known facts, open threads, timeline, guardrails).

All rules are deterministic. The LLM only contributes cached `extracted` fields on free-text
events (one-line summary, next step, qty/spec); if those are missing we fall back to rules.
"""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from . import config, store

TIER1 = {"delhi", "new delhi", "mumbai", "bengaluru", "bangalore", "chennai", "kolkata", "hyderabad", "pune",
         "ahmedabad", "noida", "gurgaon", "gurugram", "navi mumbai", "thane", "ghaziabad"}
TIER2 = {"jaipur", "lucknow", "surat", "kanpur", "nagpur", "indore", "bhopal", "vadodara", "ludhiana", "agra",
         "nashik", "rajkot", "coimbatore", "kochi", "visakhapatnam", "patna", "chandigarh", "faridabad", "meerut",
         "varanasi", "amritsar", "jalandhar", "madurai", "jodhpur", "raipur", "ranchi", "guwahati", "vijayawada"}


@dataclass
class Thread:
    key: str
    title: str
    status: str
    detail: str
    next_step: str
    last_touch: object
    score: float
    channel: str = ""
    spoken: str = ""  # Hinglish phrasing of next_step for the opening
    facts: str = ""  # product/qty/spec the customer confirmed in our last conversation


@dataclass
class Memory:
    glid: str
    role: str
    who: list = field(default_factory=list)
    known: list = field(default_factory=list)
    threads: list = field(default_factory=list)
    timeline: list = field(default_factory=list)
    guardrails: list = field(default_factory=list)
    language: str = "Hinglish"
    opening: str = ""
    last_event_ts: str = ""
    synthetic_sources: list = field(default_factory=list)
    forbidden: set = field(default_factory=set)  # strings that must never appear (firewall)
    cold: bool = False


def _windowed(evs):
    out = []
    for e in evs:
        cut = config.cutoff(e["source"])
        if cut is None or e["dt"] >= cut:
            out.append(e)
    return out


def _d(dt):
    return dt.strftime("%-d %b")


def _age(dt):
    return max(0, (config.as_of() - dt).days)


FRESH_HOURS = 24  # activity this recent outranks older threads: the customer is in-market right now


def _hours(dt):
    return (config.as_of() - dt).total_seconds() / 3600


def _tier(city):
    c = (city or "").strip().lower()
    return "Tier 1" if c in TIER1 else "Tier 2" if c in TIER2 else ("Tier 3" if c else "")


def _languages(evs):
    langs = Counter()
    for e in evs:
        if e["source"] == "call_extract":
            for l in (e["payload"].get("languages") or "").strip("[]").replace("'", "").split(","):
                if l.strip():
                    langs[l.strip()] += 1
        if e["source"] == "yaad" and e["payload"].get("language"):
            langs[e["payload"]["language"]] += 3  # what we heard ourselves counts more
    if not langs:
        return "Hinglish"
    top = [l for l, _ in langs.most_common(2)]
    if "Gujarati" in top:
        return "Gujarati (Hindi OK)"
    if top[0] == "English" and len(top) == 1:
        return "English"
    return "Hinglish"


def line(e):
    """One compact timeline line per event (deterministic; LLM one-liner preferred when cached)."""
    p, x = e["payload"], e["extracted"] or {}
    d = _d(e["dt"])
    if x.get("one_line"):
        return f"{d} · {e['channel']} · {x['one_line']}"
    s = e["source"]
    if s == "bot_call":
        return f"{d} · VANI call · outcome: {p.get('disposition') or 'talked'}"
    if s == "exec_call":
        return f"{d} · Executive call · {('answered' if p.get('status') == 'Answered' else 'not answered')}"
    if s == "whatsapp":
        return f"{d} · WhatsApp · {INTENTS.get(p.get('intent'), p.get('intent', '')[:50])}"
    if s == "enquiry" and e["role"] == "seller":
        return f"{d} · Enquiry received · {p.get('product', '')[:40]} from {p.get('buyer_city') or 'a buyer'}"
    if s == "enquiry":
        return f"{d} · Enquiry sent · {p.get('product', '')[:40]}"
    if s == "pns":
        who = "buyer call" if e["role"] == "seller" else "call to seller"
        return f"{d} · {who} · {'connected' if p.get('connected') else 'not connected'} {p.get('product', '')[:30]}".rstrip()
    if s == "bl":
        return f"{d} · BuyLead · {p.get('product', '')[:40]}"
    if s == "call_extract":
        return f"{d} · Call notes · {p.get('intent', '')}: {p.get('products', '')[:50]}"
    if s in ("yaad", "synthetic"):
        return f"{d} · {e['channel']} · {p.get('summary', '')[:90]}"
    return f"{d} · {e['channel']}"


INTENTS = {
    "Photo uploaded but not added as a product as ..": "sent product photo, not yet added to catalog",
    "size of image is less than 500*500": "photo rejected (below 500×500)",
    "WA_Callback_Connect_Q": "asked for a callback",
    "WA_Call_Feedback": "gave call feedback",
    "Msite_BL": "viewed a buy-lead",
    "Msite_BL_Quantity": "answered buy-lead quantity question",
    "Msite_BL_Spec": "answered buy-lead spec question",
    "MSITE_CSAT": "gave CSAT rating",
}


def _timeline(evs, prefer):
    """Most recent meaningful events, deduplicated by (source, line text)."""
    hot = config.as_of() - timedelta(days=config.DETAIL_DAYS)
    seen, out = set(), []
    ranked = sorted(evs, key=lambda e: (e["source"] in prefer, e["dt"]), reverse=True)
    for e in ranked:
        if e["dt"] < hot and e["source"] not in ("yaad", "bot_call"):
            continue
        txt = line(e)
        k = (e["source"], txt.split(" · ", 1)[-1])
        if k in seen:
            continue
        seen.add(k)
        out.append((e["dt"], txt))
        if len(out) >= config.MAX_TIMELINE:
            break
    return [t for _, t in sorted(out, reverse=True)]


def _promise_threads(evs):
    """Open loops from our own conversations (voice/chat write-back). Highest priority."""
    # one open loop PER PRODUCT: the latest conversation about a product decides whether its loop is still open
    # (a newer chat about school bags must not erase the GI-pipe promise from an earlier call)
    latest = {}
    for e in (e for e in evs if e["source"] == "yaad"):
        x = e["extracted"] or e["payload"]
        latest[_norm_product(x.get("product")) or "__general__"] = e
    out = []
    for key, e in latest.items():
        x = e["extracted"] or e["payload"]
        if not x.get("next_step") or x.get("closed"):
            continue
        out.append(Thread(key="yaad:" + key, title=x.get("product") or "Last conversation",
                          status="PROMISED", detail=x.get("summary") or x.get("one_line", ""),
                          next_step=x["next_step"], last_touch=e["dt"], score=200 - _age(e["dt"]), channel=e["channel"],
                          spoken=x.get("next_step_hinglish") or "",
                          facts=", ".join(str(x[k]) for k in ("qty", "spec", "price") if x.get(k))))
    return sorted(out, key=lambda t: t.last_touch, reverse=True)


def _norm_product(p):
    return " ".join(str(p or "").lower().replace("-", " ").split())


def _same_product(a, b):
    a, b = _norm_product(a), _norm_product(b)
    return bool(a and b) and (a == b or a in b or b in a)


def _promise_guard(m, th):
    promises = [t for t in th if t.status == "PROMISED"]
    for x in reversed(promises):
        if x.facts:
            m.known.insert(0, f"{x.title}: {x.facts} (confirmed {x.last_touch.strftime('%-d %b')} via {x.channel})")
    if promises:
        m.guardrails.insert(0, "PROMISED steps are still pending: say they are in progress; never claim they are done "
                               "or invent results (sellers found, quotes, prices)")


# ---------------------------------------------------------------- seller
def seller_memory(glid):
    m = Memory(glid=glid, role="seller")
    prof = store.profile(glid) or {}
    evs = _windowed(store.events(glid, "seller"))
    if not prof and not evs:
        m.cold = True
        return m
    m.language = _languages(evs)
    if prof:
        loc = ", ".join(x for x in [prof.get("seller_city"), prof.get("seller_state")] if x)
        cats = [prof.get(f"top_category_{i}") for i in (1, 2, 3) if prof.get(f"top_category_{i}")]
        m.who = [x for x in [
            f"Company: {prof.get('company_name')}" if prof.get("company_name") else "",
            f"Location: {loc}" if loc else "",
            f"Business: {prof.get('business_type') or '?'}, turnover {prof.get('annual_turnover') or 'n/a'}",
            f"Plan: {prof.get('customer_type') or 'n/a'} · catalog {prof.get('eng_product_count') or '?'} products",
        ] if x]
        if cats:
            m.known.append("Sells: " + ", ".join(cats))
        if loc:
            m.known.append("Location: " + loc)
    m.known.append("Language: " + m.language)

    # forbidden: every buyer identity seen in this seller's stream
    for e in store.events(glid, "seller"):
        if e["counterparty_glid"]:
            m.forbidden.add(str(e["counterparty_glid"]))

    th = _promise_threads(evs)
    _promise_guard(m, th)
    bots = [e for e in evs if e["source"] == "bot_call"]
    if bots:
        last = bots[-1]
        disp = last["payload"].get("disposition", "")
        x = last["extracted"] or {}
        if "Call Later" in disp:
            when = x.get("callback_date") or "soon"
            th.append(Thread("vani:callback", "Callback promised", "CALLBACK_DUE",
                             f"last VANI call {_d(last['dt'])} ended 'call later'" + (f"; said: {x['one_line']}" if x.get("one_line") else ""),
                             f"honour the callback (asked: {when})", last["dt"], 150 - _age(last["dt"]), "VANI call"))
        elif last["payload"].get("meeting_fixed") and _age(last["dt"]) <= 30:
            th.append(Thread("vani:meeting", "Meeting fixed", "MEETING_FIXED", f"meeting fixed on VANI call {_d(last['dt'])}",
                             "confirm the meeting happened / reschedule", last["dt"], 120 - _age(last["dt"]), "VANI call"))
        ni = [b for b in bots[-3:] if "Not Interested" in b["payload"].get("disposition", "")]
        if len(ni) >= 2:
            m.guardrails.append(f"Last {len(ni)} VANI calls ended 'Not Interested': lead with their leads/value, no plan pitch in the first minute")
        if x.get("objection"):
            m.guardrails.append(f"Last objection: {x['objection'][:80]}")

    hot = config.as_of() - timedelta(days=config.DETAIL_DAYS)
    enq = [e for e in evs if e["source"] == "enquiry" and e["dt"] >= hot]
    fresh = [e for e in enq if _hours(e["dt"]) <= FRESH_HOURS and not e["payload"].get("read")]
    if fresh:
        e = fresh[-1]
        where = f" from {e['payload']['buyer_city']}" if e["payload"].get("buyer_city") else ""
        q = (e["extracted"] or {}).get("qty") or e["payload"].get("qty")
        where += f", qty {q}" if q else ""
        th.append(Thread("enq:new", f"New enquiry · {e['payload'].get('product', '')[:40]}", "NEW_ENQUIRY",
                         f"{len(fresh)} new unread enquir{'y' if len(fresh) == 1 else 'ies'} in the last {FRESH_HOURS}h, latest "
                         f"{e['dt'].strftime('%-d %b %H:%M')}{where}",
                         "tell them about it first and offer to send the details on WhatsApp now", e["dt"], 180, "Enquiry"))
    if enq:
        prods = Counter(e["payload"].get("product", "")[:40] for e in enq if e["payload"].get("product"))
        cities = Counter(e["payload"].get("buyer_city") for e in enq if e["payload"].get("buyer_city"))
        unread = sum(1 for e in enq if not e["payload"].get("read"))
        unreplied = sum(1 for e in enq if not e["payload"].get("replied"))
        top = prods.most_common(1)[0][0] if prods else "your products"
        detail = f"{len(enq)} enquiries in 30d (top: {top}); {unread} unread, {unreplied} not replied"
        if cities:
            detail += f"; buyers mostly from {', '.join(c for c, _ in cities.most_common(2))}"
        if unread or unreplied:
            th.append(Thread("enq:pending", f"Pending enquiries · {top}", "LEADS_PENDING", detail,
                             "offer to share the unread enquiries on WhatsApp / help reply", enq[-1]["dt"],
                             80 + min(unreplied, 20) - _age(enq[-1]["dt"]), "Enquiry"))
        m.known.append("Buyers ask for: " + ", ".join(p for p, _ in prods.most_common(3)))
    missed = [e for e in evs if e["source"] == "pns" and e["dt"] >= hot and not e["payload"].get("connected")]
    if len(missed) >= 2:
        th.append(Thread("pns:missed", "Missed buyer calls", "MISSED_CALLS", f"{len(missed)} buyer calls not connected in 30d",
                      "suggest call-forwarding / best hours to stay reachable", missed[-1]["dt"], 70 - _age(missed[-1]["dt"]), "Buyer call"))
    wa = [e for e in evs if e["source"] == "whatsapp"]
    blk = [e for e in wa if "Photo uploaded" in e["payload"].get("intent", "") or "size of image" in e["payload"].get("intent", "")]
    if blk:
        th.append(Thread("wa:catalog", "Catalog photo stuck", "CATALOG_BLOCKER",
                         f"{len(blk)} photo(s) sent on WhatsApp not added to catalog (last {_d(blk[-1]['dt'])})",
                         "help add the product / re-upload ≥500×500", blk[-1]["dt"], 75 - _age(blk[-1]["dt"]), "WhatsApp"))
    if any("Callback" in e["payload"].get("intent", "") for e in wa[-5:]):
        m.guardrails.append("Recently asked for a callback on WhatsApp")

    if prof:
        if prof.get("do_not_call_requested") == "1.0":
            m.guardrails.insert(0, "DND REQUESTED: do not pitch; help only with what they ask, keep it short")
        if prof.get("showed_frustration") == "1.0":
            m.guardrails.append("Showed frustration before: be brief, get to the point")
        if prof.get("asked_if_talking_to_bot") == "1.0":
            m.guardrails.append("Asked if talking to a bot: say upfront you are IndiaMART's virtual assistant")
        if prof.get("past_objections"):
            m.guardrails.append("Past objections: " + prof["past_objections"][:90])
    ex = [e for e in evs if e["source"] == "exec_call" and e["payload"].get("status") == "Answered"]
    if ex or (prof and prof.get("already_in_touch_with_executive") == "1.0"):
        when = f" (last spoke {_d(ex[-1]['dt'])})" if ex else ""
        m.guardrails.append(f"In touch with an IndiaMART executive{when}: don't duplicate their pitch")

    m.threads = sorted(th, key=lambda t: t.score, reverse=True)[: config.MAX_THREADS]
    m.timeline = _timeline(evs, prefer={"yaad", "bot_call", "exec_call", "whatsapp"})
    _finish(m, evs)
    if prof.get("_synthetic"):
        m.synthetic_sources = sorted(set(m.synthetic_sources) | {"profile"})
    return m


# ---------------------------------------------------------------- buyer
def buyer_memory(glid):
    m = Memory(glid=glid, role="buyer")
    evs = _windowed(store.events(glid, "buyer"))
    if not evs:
        m.cold = True
        return m
    m.language = _languages(evs)
    enq = [e for e in evs if e["source"] == "enquiry"]
    if enq:
        p = enq[-1]["payload"]
        city = p.get("buyer_city") or ""
        loc = ", ".join(x for x in [city, p.get("buyer_state")] if x)
        m.who = [x for x in [
            f"Location: {loc}" + (f" ({_tier(city)})" if _tier(city) else "") if loc else "",
            f"Company: {p['buyer_company']}" if p.get("buyer_company") else "",
            f"Role: {p['buyer_designation']}" if p.get("buyer_designation") else "",
        ] if x]
        if loc:
            m.known.append("Location: " + loc)
    m.known.append("Language: " + m.language)

    groups = defaultdict(list)
    for e in evs:
        if e["source"] in ("enquiry", "pns", "bl", "call_extract", "synthetic") and e["thread_key"]:
            groups[e["thread_key"]].append(e)
    th = _promise_threads(evs)
    _promise_guard(m, th)
    wants = []
    for key, es in groups.items():
        last = max(e["dt"] for e in es)
        names = Counter(e["payload"].get("product") or e["payload"].get("products") or "" for e in es)
        product = (names.most_common(1)[0][0] or "requirement")[:45]
        sellers = {e["payload"].get("seller_glid") for e in es if e["payload"].get("seller_glid")}
        sent = [e for e in es if e["source"] == "enquiry"]
        replied = sum(1 for e in sent if e["payload"].get("seller_replied"))
        calls = [e for e in es if e["source"] == "pns"]
        conn = sum(1 for e in calls if e["payload"].get("connected"))
        bl = [e for e in es if e["source"] == "bl"]
        notes = [e for e in es if e["source"] == "call_extract"]
        x = next((e["extracted"] for e in reversed(sent) if e["extracted"]), {}) or {}
        bits = []
        if sent:
            bits.append(f"enquired {len(sent)} seller(s), {replied} replied")
        if calls:
            bits.append(f"{len(calls)} call(s), {conn} connected")
        if bl:
            bits.append(f"{len(bl)} seller(s) bought this lead")
        if notes and notes[-1]["payload"].get("prices"):
            bits.append("prices discussed: " + notes[-1]["payload"]["prices"][:50])
        searches = [e for e in es if e["source"] == "synthetic"]
        if searches:
            sp = searches[-1]["payload"]
            bits.append(sp.get("summary", "searched")[:110])
            if sp.get("qty") or sp.get("city"):
                m.known.insert(0, f"{product}: " + ", ".join(x for x in [sp.get("qty") and f"qty {sp['qty']}",
                                                                          sp.get("city") and f"deliver to {sp['city']}"] if x))
        x = x or next((e["extracted"] for e in reversed(es) if e["extracted"]), {}) or {}
        if x.get("qty") and str(x["qty"]).split()[0] not in "; ".join(bits):
            bits.append("qty " + str(x["qty"])[:30])
        if x.get("spec"):
            bits.append("spec " + str(x["spec"])[:40])
        age = _age(last)
        if any(_hours(e["dt"]) <= FRESH_HOURS for e in es if e["source"] in ("enquiry", "synthetic", "pns")):
            status, nxt, base = "ACTIVE_NOW", "they are looking for this right now: offer to connect sellers immediately", 215
        elif age > 30:
            status, nxt, base = "STALE", "ask if still needed", 20
        elif replied or conn:
            status, nxt, base = "IN_DISCUSSION", "ask if the deal is done or they want more quotes", 70
        elif sent and age >= 2:
            status, nxt, base = "UNANSWERED", "offer to connect 2–3 more responsive sellers now", 100
        elif calls and not conn:
            status, nxt, base = "FAILED_CONNECT", "offer to connect them to a seller who picks up", 95
        elif bl:
            status, nxt, base = "SELLERS_REACHING_OUT", "tell them sellers will call; ask preferred time", 60
        else:
            status, nxt, base = "OPEN", "confirm the requirement is still open", 50
        contacted = [n["company"] for n in (store.seller_name(s) for s in list(sellers)[:3]) if n and n.get("company")]
        if contacted:
            bits.append("sellers: " + ", ".join(contacted))
        promise = next((t for t in th if t.status == "PROMISED" and _same_product(t.title, product)), None)
        if promise:
            # our conversation about this product supersedes older facts (qty/spec): keep only the history
            hist = [b for b in bits if not b.startswith(("qty ", "spec ")) and not b.startswith(("sellers:", "searched", "looking for"))]
            if hist:
                promise.detail += " | earlier: " + "; ".join(hist)[:120]
            if status == "ACTIVE_NOW":  # still searching right now: keep the promise on top
                promise.score = max(promise.score, base)
            wants.append((last, product))
            continue
        th.append(Thread(key, product, status, "; ".join(bits) or "recent activity on this product", nxt, last, base + min(len(es), 10) - age,
                         es[-1]["channel"]))
        wants.append((last, product))
    m.threads = sorted(th, key=lambda t: t.score, reverse=True)[: config.MAX_THREADS]
    if wants:
        m.known.append("Looking for: " + ", ".join(p for _, p in sorted(wants, reverse=True)[:3]))
    m.guardrails.append("Never share another buyer's details or a seller's phone/personal info; seller company names only")
    m.timeline = _timeline(evs, prefer={"yaad", "enquiry", "pns"})
    _finish(m, evs)
    return m


def _finish(m, evs):
    if evs:
        m.last_event_ts = max(e["ts"] for e in evs)
    m.synthetic_sources = sorted({e["source"] if e["source"] != "synthetic" else e["channel"] for e in evs if e["synthetic"]})


def build(glid, role):
    return seller_memory(str(glid)) if role == "seller" else buyer_memory(str(glid))
