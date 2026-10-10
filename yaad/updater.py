"""POST /v1/event lands here: map the event to a thread, update the thread, mirror to the other side if needed.

Fast lane (every event, no LLM, ~ms): stage, facts (newest wins, with source + time), sellers, last activity.
Slow lane (conversations only, ~1 s): the transcript is summarised once by the LLM (extract.conversation), which
gives the next step, new facts (qty...), a short thread summary, and any complaint.
"""
import time

from . import db, extract, mapper

BUYER_STAGES = ["SEARCHING", "BL_POSTED", "SELLERS_CONNECTED", "IN_TALKS", "PROMISED", "CLOSED"]
CHANNEL = {"search": "Search", "bl_posted": "BuyLead", "bl_matched": "BuyLead", "enquiry": "Enquiry", "pns": "PNS call",
           "complaint": "Complaint", "lead_bought": "BuyLead", "vani_call": "VANI call", "catalog_issue": "WhatsApp",
           "seller_update": "Seller update", "requirement_update": "Buyer update"}
# seller-side events that are not about a product get their own fixed "system" thread
SYSTEM_THREADS = {"vani_call": ("_vani", "IndiaMART follow-up"), "catalog_issue": ("_catalog", "Catalog")}


def _advance(t, stage):
    if t["kind"] == "requirement" and stage in BUYER_STAGES and t["stage"] in BUYER_STAGES:
        if BUYER_STAGES.index(stage) <= BUYER_STAGES.index(t["stage"]):
            return
    t["stage"] = stage


def _fact(t, key, value, src, ts):
    if value in (None, "", [], {}):
        return
    old = t["facts"].get(key)
    if old is None or (old.get("ts") or "") <= ts:  # newest wins, provenance kept
        t["facts"][key] = {"v": value, "src": src, "ts": ts}


def _seller(t, sid, name, status, ts):
    for s in t["sellers"]:
        if s["id"] == sid:
            s.update(status=status, ts=ts, name=name or s.get("name"))
            return
    t["sellers"].append({"id": sid, "name": name or sid, "status": status, "ts": ts})


def _name(glid):
    u = db.any_user(glid) or {}
    return u.get("company") or u.get("name") or glid


def _new_thread(ev, mcat_id, title, kind, parent=None):
    ts = ev["ts"]
    return {"thread_id": db.new_thread_id(ev["glid"]), "glid": ev["glid"], "role": ev["role"], "mcat_id": mcat_id,
            "title": title, "kind": kind, "parent_thread_id": parent, "stage": None, "facts": {}, "sellers": [],
            "links": {}, "summary": "", "next_step": "", "last_channel": "", "last_activity": ts, "created_at": ts,
            "n_events": 0}


def apply(ev, mirror=True):
    received = time.time()
    ev = {"payload": {}, "synthetic": 0, **ev}
    ev["ts"] = ev.get("ts") or db.now()
    p, ts, typ = ev["payload"], ev["ts"], ev["type"]
    x = None

    # ---- slow lane first for conversations: we need the product to map the thread
    if typ == "conversation":
        turns = [tuple(t) for t in p.get("turns", [])]
        x = extract.conversation(turns, ev["role"], p.get("channel", "Conversation"))
        pv = p.get("platform_vars") or {}  # Sarvam's own post-call variables fill whatever we missed
        if not x.get("next_step") and pv.get("next_step") and pv.get("outcome") != "resolved":
            x["next_step"] = pv["next_step"]
        x["product"] = x.get("product") or pv.get("product")
        if pv.get("outcome") in ("next_step_agreed", "callback_requested"):
            x["closed"] = False

    if typ in SYSTEM_THREADS:
        mid, name = SYSTEM_THREADS[typ]
        if not db.mcat(mid):
            db.upsert_mcat(mid, name, None)  # system threads never merge with anything
        ev["mcat_id"] = mid

    # ---- complaint events go straight to a problem thread
    if typ == "complaint":
        return _complaint(ev, p.get("text"), p.get("seller_name"), p.get("product"), received, mirror)

    # ---- map to a thread (rules, then LLM pick)
    res = mapper.resolve(ev, product_hint=(x or {}).get("product"))
    t = db.thread(res["thread_id"]) if res["thread_id"] else None
    created = t is None
    if created:
        kind = "requirement" if ev["role"] == "buyer" else "opportunity"
        t = _new_thread(ev, res.get("mcat_id"), res.get("title") or "General", kind)

    # ---- fast lane
    if ev["role"] == "buyer":
        if typ == "search":
            _advance(t, "SEARCHING")
            for k in ("qty", "city"):
                _fact(t, k, p.get(k), "search", ts)
        elif typ == "bl_posted":
            _advance(t, "BL_POSTED")
            for k in ("qty", "spec", "city"):
                _fact(t, k, p.get(k), "buylead", ts)
        elif typ == "bl_matched":
            _advance(t, "SELLERS_CONNECTED")
            for s in p.get("sellers", []):
                _seller(t, s["id"], s.get("name") or _name(s["id"]), "connected", ts)
        elif typ == "enquiry":
            _advance(t, "IN_TALKS" if p.get("replied") else "SELLERS_CONNECTED")
            if ev.get("counterparty"):
                _seller(t, ev["counterparty"], _name(ev["counterparty"]),
                        "replied" if p.get("replied") else "enquired, no reply", ts)
            _fact(t, "also_needs", p.get("also_needs"), "enquiry", ts)
        elif typ == "pns":
            _advance(t, "IN_TALKS" if p.get("connected") else "SELLERS_CONNECTED")
            if ev.get("counterparty"):
                _seller(t, ev["counterparty"], _name(ev["counterparty"]),
                        "spoke on call" if p.get("connected") else "call not connected", ts)
        elif typ == "seller_update":
            _advance(t, "IN_TALKS")
            _seller(t, ev["counterparty"], p.get("seller_name") or _name(ev["counterparty"]), p.get("note", "updated"), ts)
    else:  # seller
        if typ == "lead_bought":
            t["stage"] = "LEAD_BOUGHT"
            for k in ("qty", "spec", "buyer_city"):
                _fact(t, k, p.get(k), "buylead", ts)
            if ev.get("counterparty"):  # internal link for the two-sided loop; never rendered
                t["links"].setdefault("buyers", {})[ev["counterparty"]] = p.get("buyer_thread")
        elif typ == "enquiry":
            if t["stage"] not in ("PROMISED",):
                t["stage"] = "ENQUIRY_UNREAD" if not p.get("read") else "RESPONDED"
            n = (t["facts"].get("unread_enquiries") or {}).get("v", 0) + (0 if p.get("read") else 1)
            _fact(t, "unread_enquiries", n, "enquiry", ts)
            _fact(t, "buyer_city", p.get("buyer_city"), "enquiry", ts)
            _fact(t, "qty", p.get("qty"), "enquiry", ts)
            if ev.get("counterparty"):
                t["links"].setdefault("buyers", {}).setdefault(ev["counterparty"], p.get("buyer_thread"))
        elif typ == "requirement_update":
            _fact(t, "qty", p.get("qty"), "buyer update", ts)
            _fact(t, "spec", p.get("spec"), "buyer update", ts)
        elif typ == "pns":
            n = (t["facts"].get("missed_calls") or {}).get("v", 0) + (0 if p.get("connected") else 1)
            _fact(t, "missed_calls", n, "pns", ts)
        elif typ == "vani_call":
            t["stage"] = "CALLBACK_DUE" if p.get("callback") else "CONTACTED"
            _fact(t, "last_vani_outcome", p.get("disposition"), "vani", ts)
            _fact(t, "callback_asked", p.get("callback"), "vani", ts)
            t["next_step"] = f"honour the callback ({p['callback']})" if p.get("callback") else t["next_step"]
            t["summary"] = p.get("note", t["summary"])
        elif typ == "catalog_issue":
            t["stage"] = "CATALOG_ISSUE"
            t["summary"] = p.get("note", "product photo sent on WhatsApp, not added to catalog")
            t["next_step"] = "help add the product / re-upload photo ≥500×500"

    # ---- slow lane results
    if x:
        for k in ("qty", "spec", "price"):
            _fact(t, k, x.get(k), "conversation", ts)
        if x.get("summary"):
            t["summary"] = x["summary"]
        # a requirement is closed only when nothing is pending; a complaint never means the need was fulfilled
        if x.get("closed") and not x.get("next_step") and not x.get("complaint_issue"):
            t["stage"] = "CLOSED" if ev["role"] == "buyer" else "RESPONDED"
            t["next_step"] = ""
        elif x.get("next_step"):
            t["stage"] = "PROMISED"
            t["next_step"] = x["next_step"]
        if x.get("language"):
            t["links"]["language"] = x["language"]

    t["last_activity"] = max(t["last_activity"] or ts, ts)
    t["last_channel"] = (p.get("channel") if typ == "conversation" else CHANNEL.get(typ, typ))
    t["n_events"] = (t["n_events"] or 0) + 1
    if not t["stage"]:
        t["stage"] = "OPEN"
    db.save_thread(t)
    eid = db.insert_event({**_ev_row(ev), "thread_id": t["thread_id"], "mcat_id": t["mcat_id"],
                           "map_method": res["method"], "map_confidence": res["confidence"], "received_at": received})
    ms = db.log_freshness(eid, ev["glid"], ev["role"], received)
    out = {"event_id": eid, "glid": ev["glid"], "role": ev["role"], "type": typ, "thread_id": t["thread_id"],
           "thread": t["title"], "stage": t["stage"],
           "map_method": res["method"], "confidence": res["confidence"], "new_thread": created, "ms": round(ms, 1),
           "mirrored": []}
    if x:
        out["extracted"] = {k: x.get(k) for k in ("one_line", "next_step", "qty", "product", "complaint_issue",
                                                  "complaint_seller", "language", "extracted_by")}

    # ---- our call/chat with a seller honours any pending callback
    if typ == "conversation" and ev["role"] == "seller":
        cb = next((th for th in db.threads(ev["glid"], "seller") if th["stage"] == "CALLBACK_DUE"), None)
        if cb:
            cb["stage"], cb["next_step"] = "CONTACTED", ""
            cb["summary"] = f"Callback honoured on {p.get('channel', 'call')} ({ts[:10]})"
            db.save_thread(cb)

    # ---- a complaint mentioned inside a conversation becomes its own problem thread
    if x and x.get("complaint_issue"):
        c = _complaint({**ev, "type": "complaint"}, x["complaint_issue"], x.get("complaint_seller"),
                       x.get("product"), time.time(), mirror)
        out["mirrored"].append(c)

    # ---- two-sided: mirror to the counterparty's memory (re-read: a complaint above may have changed seller status)
    if mirror:
        out["mirrored"] += _mirror(ev, db.thread(t["thread_id"]), x)
    return out


def _complaint(ev, text, seller_hint, product_hint, received, mirror):
    parent, seller, method, conf = mapper.resolve_complaint_parent(ev["glid"], ev["role"], seller_hint, product_hint,
                                                                   ev["ts"], text)
    seller_name = (seller or {}).get("name") or seller_hint
    existing = next((t for t in db.threads(ev["glid"], ev["role"]) if t["kind"] == "problem"
                     and t["parent_thread_id"] == (parent or {}).get("thread_id") and t["stage"] == "OPEN"), None)
    t = existing or _new_thread(ev, (parent or {}).get("mcat_id"),
                                f"Complaint · {(parent or {}).get('title', 'order')}", "problem",
                                (parent or {}).get("thread_id"))
    t["stage"] = "OPEN"
    _fact(t, "issue", text, "complaint", ev["ts"])
    _fact(t, "about_seller", seller_name, "complaint", ev["ts"])
    t["summary"] = f"{text}" + (f" (seller: {seller_name})" if seller_name else "")
    t["next_step"] = "acknowledge first; escalated to IndiaMART support"
    t["last_activity"], t["last_channel"] = ev["ts"], ev["payload"].get("channel") or "Complaint"
    t["n_events"] = (t["n_events"] or 0) + 1
    db.save_thread(t)
    if parent and seller:
        _seller(parent, seller["id"], seller_name, "complaint raised", ev["ts"])
        db.save_thread(parent)
    eid = db.insert_event({**_ev_row({**ev, "type": "complaint"}), "thread_id": t["thread_id"],
                           "mcat_id": t["mcat_id"], "counterparty": (seller or {}).get("id"),
                           "map_method": f"complaint:{method}", "map_confidence": conf, "received_at": received})
    ms = db.log_freshness(eid, ev["glid"], ev["role"], received)
    return {"event_id": eid, "glid": ev["glid"], "role": ev["role"], "type": "complaint", "thread_id": t["thread_id"],
            "thread": t["title"], "stage": "OPEN", "kind": "problem",
            "parent": (parent or {}).get("thread_id"), "map_method": f"complaint:{method}", "confidence": conf,
            "ms": round(ms, 1)}


def _mirror(ev, t, x):
    """The same real-world event, written into the other party's memory (aggregates only on the seller side)."""
    out, p = [], ev["payload"]
    if ev["role"] == "buyer" and ev["type"] == "bl_matched":
        for s in p.get("sellers", []):
            out.append(apply({"glid": s["id"], "role": "seller", "type": "lead_bought", "mcat_id": t["mcat_id"],
                              "counterparty": ev["glid"], "ts": ev["ts"], "synthetic": ev.get("synthetic", 0),
                              "payload": {"qty": (t["facts"].get("qty") or {}).get("v"),
                                          "spec": (t["facts"].get("spec") or {}).get("v"),
                                          "buyer_city": (t["facts"].get("city") or {}).get("v"),
                                          "buyer_thread": t["thread_id"]}}, mirror=False))
    elif ev["role"] == "buyer" and ev["type"] == "enquiry" and ev.get("counterparty"):
        out.append(apply({"glid": ev["counterparty"], "role": "seller", "type": "enquiry", "mcat_id": t["mcat_id"],
                          "counterparty": ev["glid"], "ts": ev["ts"], "synthetic": ev.get("synthetic", 0),
                          "payload": {"read": False, "buyer_city": (t["facts"].get("city") or {}).get("v"),
                                      "qty": (t["facts"].get("qty") or {}).get("v"), "buyer_thread": t["thread_id"]}},
                         mirror=False))
    elif ev["role"] == "buyer" and ev["type"] == "conversation" and x and (x.get("qty") or x.get("spec")):
        for s in t["sellers"]:
            if s["status"] != "complaint raised" and db.thread_for(s["id"], "seller", t["mcat_id"]):
                out.append(apply({"glid": s["id"], "role": "seller", "type": "requirement_update",
                                  "mcat_id": t["mcat_id"], "counterparty": ev["glid"], "ts": ev["ts"],
                                  "synthetic": ev.get("synthetic", 0),
                                  "payload": {"qty": x.get("qty"), "spec": x.get("spec")}}, mirror=False))
    elif ev["role"] == "seller" and ev["type"] == "conversation" and x and x.get("next_step"):
        for buyer, bthread in (t["links"].get("buyers") or {}).items():
            out.append(apply({"glid": buyer, "role": "buyer", "type": "seller_update", "mcat_id": t["mcat_id"],
                              "counterparty": ev["glid"], "ts": ev["ts"], "synthetic": ev.get("synthetic", 0),
                              "payload": {"seller_name": _name(ev["glid"]), "note": x["next_step"]}}, mirror=False))
    return out


def _ev_row(ev):
    return {k: ev.get(k) for k in ("glid", "role", "type", "mcat_id", "counterparty", "ts", "payload", "synthetic")}
