"""Event -> thread. Rules first (mcat is IndiaMART's own join key); the LLM only *chooses* among the user's own
threads when rules can't decide. Every decision records its method and confidence, so it is auditable."""
from datetime import datetime

from . import config, db, llm


def _norm(s):
    return " ".join(str(s or "").lower().replace("-", " ").split())


def _days(a, b):
    try:
        return abs((datetime.fromisoformat(a[:19]) - datetime.fromisoformat(b[:19])).total_seconds()) / 86400
    except Exception:
        return 1e9


def _same_name(a, b):
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a == b or a in b or b in a)


def mcat_for_text(text):
    """Product words -> the best mcat: exact name > longest name inside the text > shortest name containing it."""
    t = _norm(text)
    if not t:
        return None

    def score(m):
        n = _norm(m["name"])
        if not n or n.startswith("_"):
            return None
        if n == t:
            return (3, 0)
        if n in t:
            return (2, len(n))
        if t in n:
            return (1, -len(n))
        return None

    scored = [(score(m), m) for m in db.mcats() if not str(m["mcat_id"]).startswith("_")]
    scored = [x for x in scored if x[0]]
    return max(scored, key=lambda x: x[0])[1] if scored else None


def _open(threads):
    return [t for t in threads if t["kind"] != "problem" and t["stage"] not in ("CLOSED",)]


def _result(t, method, conf):
    return {"thread_id": t["thread_id"] if t else None, "method": method, "confidence": conf}


def resolve(ev, product_hint=None):
    """Return {thread_id|None, method, confidence, mcat_id, title}. thread_id None means: create a new thread."""
    ts = ev.get("ts") or db.now()
    mine = _open(db.threads(ev["glid"], ev["role"]))
    mcat_id = ev.get("mcat_id")
    method_mcat = "exact"
    if not mcat_id:
        m = mcat_for_text(ev.get("product") or product_hint)
        if m:
            mcat_id, method_mcat = m["mcat_id"], "product_text"
    if mcat_id:
        m = db.mcat(mcat_id)
        # 1/3. same mcat
        same = next((t for t in mine if t["mcat_id"] == mcat_id), None)
        if same:
            return {**_result(same, method_mcat, "high" if method_mcat == "exact" else "medium"), "mcat_id": mcat_id}
        # 2. sibling mcat recently active: buyer side only (one requirement can span pipe + fittings; a seller's
        #    SS-pipe enquiries and a GI-pipe lead are different opportunities)
        if ev["role"] == "buyer" and m and m.get("parent_id"):
            sibs = [t for t in mine if (db.mcat(t["mcat_id"]) or {}).get("parent_id") == m["parent_id"]
                    and _days(t["last_activity"], ts) <= config.SIBLING_DAYS]
            if sibs:
                return {**_result(sibs[0], "sibling_mcat", "high"), "mcat_id": mcat_id}
        return {"thread_id": None, "method": f"new_{method_mcat}", "confidence": "high", "mcat_id": mcat_id,
                "title": (m or {}).get("name") or ev.get("product") or mcat_id}
    # 4. counterparty seen recently on a thread
    cp = ev.get("counterparty")
    if cp:
        for t in mine:
            seen = [e for e in db.events(ev["glid"], ev["role"]) if e["thread_id"] == t["thread_id"]
                    and e["counterparty"] == cp and _days(e["ts"], ts) <= config.COUNTERPARTY_DAYS]
            if seen:
                return {**_result(t, "counterparty", "medium"), "mcat_id": t["mcat_id"]}
    # 5. free text with candidates: let the LLM choose among the user's own threads
    if (product_hint or ev.get("text")) and len(mine) > 1:
        pick = _llm_pick(mine, product_hint or ev.get("text"))
        if pick:
            return {**_result(pick, "llm_pick", "medium"), "mcat_id": pick["mcat_id"]}
    if len(mine) == 1 and ev["type"] == "conversation":
        return {**_result(mine[0], "only_open_thread", "low"), "mcat_id": mine[0]["mcat_id"]}
    # 6. nothing fits: a new thread, flagged
    return {"thread_id": None, "method": "new_low_conf", "confidence": "low", "mcat_id": None,
            "title": ev.get("product") or product_hint or "General"}


def _named_in(text, name):
    """'Om Sai Steel took an advance' names 'Om Sai Steel Mart': match on the first two words of the seller name."""
    words = _norm(name).split()
    return bool(words) and " ".join(words[:2]) in _norm(text)


def resolve_complaint_parent(glid, role, seller_hint=None, product_hint=None, ts=None, text=None):
    """Which requirement thread is a complaint about? Candidates = threads with sellers connected recently."""
    ts = ts or db.now()
    cands = [t for t in db.threads(glid, role) if t["kind"] == "requirement" and t["sellers"]
             and _days(t["last_activity"], ts) <= config.COMPLAINT_LOOKBACK_DAYS]
    if seller_hint:
        for t in cands:
            for s in t["sellers"]:
                if _same_name(s.get("name"), seller_hint) or s.get("id") == seller_hint:
                    return t, s, "seller_name", "high"
    if text:
        for t in cands:
            for s in t["sellers"]:
                if _named_in(text, s.get("name")):
                    return t, s, "seller_in_text", "high"
    if product_hint:
        m = mcat_for_text(product_hint)
        hit = next((t for t in cands if m and t["mcat_id"] == m["mcat_id"]), None)
        if hit:
            return hit, None, "product_text", "medium"
    if len(cands) == 1:
        return cands[0], None, "only_candidate", "medium"
    if cands:
        pick = _llm_pick(cands, f"complaint: {seller_hint or ''} {product_hint or ''}")
        if pick:
            return pick, None, "llm_pick", "low"
        return cands[0], None, "most_recent", "low"
    return None, None, "no_candidate", "low"


def _llm_pick(cands, text):
    if not config.SARVAM_LLM_API_KEY or not text:
        return None
    options = {f"T{i + 1}": t for i, t in enumerate(cands[:6])}
    listing = "\n".join(f"{k}: {t['title']} ({t['stage']}, sellers: "
                        f"{', '.join(s.get('name', '') for s in t['sellers'][:3]) or '-'})" for k, t in options.items())
    try:
        out = llm.json_out([{"role": "system", "content": "Pick which of the user's threads this message is about. "
                                                           "Answer UNSURE unless one thread clearly matches."},
                            {"role": "user", "content": f"Threads:\n{listing}\n\nMessage: {text}"}],
                           {"type": "object", "properties": {"choice": {"type": "string"}}, "required": ["choice"]},
                           max_tokens=60, reasoning_effort="off")
        return options.get(str(out.get("choice", "")).strip().upper())
    except Exception:
        return None
