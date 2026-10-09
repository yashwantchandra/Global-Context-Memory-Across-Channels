"""Labelled synthetic activity, as if a source API pushed it (demo freshness + automatic checks)."""
from datetime import datetime
from types import SimpleNamespace

from . import store, threads


def new_activity(glid, role, kind="enquiry", product="", city="", qty="", message=""):
    return inject(SimpleNamespace(glid=glid, role=role, kind=kind, product=product, city=city, qty=qty, message=message))


def inject(ev):
    """Inject a labelled synthetic activity (as if a source API pushed it) to demo event-driven freshness."""
    now = datetime.now()
    # use this customer's own top product so the injected activity fits their story
    m = threads.build(ev.glid, ev.role)
    top = next((t for t in m.threads if t.status not in ("PROMISED", "CALLBACK_DUE", "MEETING_FIXED")), None)
    if ev.role == "seller":
        prof = store.profile(ev.glid) or {}
        recent = [e for e in store.events(ev.glid, "seller") if e["source"] == "enquiry"]
        product = (recent[-1]["payload"].get("product") if recent else None) or prof.get("top_category_1") or "your product"
        key = recent[-1]["thread_key"] if recent else None
        kinds = {
            "enquiry": ("enquiry", "Enquiry", {"product": ev.product or product, "buyer_city": ev.city or "Indore",
                                               "read": False, "replied": False, "qty": ev.qty, "message": ev.message[:400]}),
            "photo": ("whatsapp", "WhatsApp", {"intent": "Photo uploaded but not added as a product as .."}),
            "callback": ("whatsapp", "WhatsApp", {"intent": "WA_Callback_Connect_Q"}),
        }
    else:
        product = top.title if top else "GI Pipe 2 inch"
        key = top.key if top and not top.key.startswith("yaad:") else None
        kinds = {
            "enquiry": ("enquiry", "Enquiry", {"product": ev.product or product, "seller_glid": "SYN-S-1004",
                                               "seller_replied": False, "buyer_city": ev.city, "message": ev.message[:400]}),
            "requirement": ("synthetic", "Search", {"product": product, "summary": f"searched again: '{product}'"}),
        }
    src, ch, payload = kinds.get(ev.kind, list(kinds.values())[0])
    if ev.product and ev.product.lower() != str(payload.get("product", "")).lower():
        key = "typed:" + ev.product.lower()[:40]  # a different product starts its own thread
    from .extract import _qty
    qty = ev.qty.strip() or _qty(ev.message)
    if qty and qty.replace(",", "").isdigit():
        qty += " pcs"  # a bare number typed in the Qty box means pieces
    if ev.role == "buyer" and ev.kind == "requirement" and ev.product:
        bits = [f"looking for {ev.product}"] + ([f"qty {qty}"] if qty else []) + ([f"deliver to {ev.city}"] if ev.city else []) \
            + ([f"note: {ev.message[:80]}"] if ev.message else [])
        payload = {"product": ev.product, "summary": ", ".join(bits), "city": ev.city, "qty": qty}
    extracted = {"qty": qty, "city": ev.city or None, "product": payload.get("product"),
                 "one_line": f"{payload.get('product')}" + (f", {qty}" if qty else "") + (f", {ev.city}" if ev.city else "")} \
        if (qty or ev.message or ev.city) else None
    eid = store.add_event(ev.glid, ev.role, src, ch, now, payload, synthetic=True, thread_key=key or "injected",
                          extracted=extracted)
    row = store.conn().execute("SELECT freshness_ms, tokens FROM renders WHERE trigger_event_id=?", (eid,)).fetchone()
    return {"event_id": eid, "event_to_file_ms": row["freshness_ms"] if row else None, "kind": ev.kind,
            "product": payload.get("product") or payload.get("intent")}
