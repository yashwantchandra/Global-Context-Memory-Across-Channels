"""Load the buyer dataset: a log export of the internal globalcontext getContext API.

The CSV's quoting is broken, so each response is located by its
`API_RESPONSE_JSON` key and decoded as a JSON string, then parsed as JSON.
Only the fields the buyer file needs are kept (no email, address or staff names).
"""
import json
from json.decoder import scanstring

from globalctx import config, store

KEY = 'API_RESPONSE_JSON":"'


def snapshots(path=None):
    raw = open(path or config.BUYER_FILE, encoding="utf-8", errors="replace").read().replace('""', '"')
    pos = 0
    while (i := raw.find(KEY, pos)) >= 0:
        pos = i + 1
        try:
            text, _ = scanstring(raw, i + len(KEY))
            data = json.loads(text).get("data") or {}
        except (ValueError, AttributeError):
            continue  # malformed record: skip, never crash
        if data.get("glid"):
            yield data


def act_ts(v):
    """'20260911171440' -> '2026-09-11T17:14:40'"""
    v = str(v or "")
    if len(v) != 14 or not v.isdigit():
        return ""
    return f"{v[0:4]}-{v[4:6]}-{v[6:8]}T{v[8:10]}:{v[10:12]}:{v[12:14]}"


def wa_text(chat_content):
    """Pull readable text out of a WhatsApp CHAT_CONTENT JSON blob."""
    try:
        mc = json.loads(chat_content).get("message_content") or {}
    except (ValueError, AttributeError):
        return ""
    for path in (("text",), ("caption",), ("interactive", "body", "text")):
        node = mc
        for k in path:
            node = node.get(k) if isinstance(node, dict) else None
        if isinstance(node, str) and node.strip():
            return " ".join(node.replace("*", "").split())
    return ""


def events_from(d):
    g = str(d["glid"])
    act = d.get("activitydetails") or {}
    kyc = d.get("kycdetails") or {}
    conn = d.get("connectdetails") or {}
    tickets = (d.get("ticketsdetails") or {}).get("last_8_to_90_days") or {}
    up = ((kyc.get("upload_metadata") or {}).get("upload_time") or "")[:19]
    last_enq = act.get("LAST_ENQUIRY") or {}
    last_lead = act.get("LAST_LEAD") or {}

    out = [{
        "glid": g, "role": "buyer", "source": "profile", "channel": None, "ts": up or "2026-10-01T00:00:00",
        "ext_id": f"bprofile:{g}:{up}",
        "payload": {
            "first_name": kyc.get("first_name") or "",
            "city": kyc.get("city") or "", "country": kyc.get("country") or "",
            "customer_type": kyc.get("customer_type") or "",
            "registered_since": kyc.get("registered_since_with_indiamart") or "",
            "rating": kyc.get("avg_rating_count"), "rating_count": kyc.get("rating_count"),
            "mobile_verified": kyc.get("input_mobile_verification_status") or "",
            "email_verified": kyc.get("input_email_verification_status") or "",
            "gst_status": kyc.get("gst_status") or kyc.get("gst_availability") or "",
            "enq_30d": act.get("enq_last_30_days"), "enq_90d": act.get("enq_last_90_days"),
            "pns_30d": act.get("pns_received_30_days"), "pns_90d": act.get("pns_received_90_days"),
            "last_enquiry_title": last_enq.get("TITLE") or "",
            "last_call_summary": (conn.get("last_call_summary") or "")[:300],
            "negative_feedback_90d": tickets.get("negative_feedback_count", 0),
            "high_churn": kyc.get("high_churn"),
        },
    }]

    for a in act.get("BUYER_ACTIVITY") or []:
        t = act_ts(a.get("ACTIVITY_TIME"))
        if not t:
            continue
        out.append({
            "glid": g, "role": "buyer", "source": "buyer_activity", "channel": "marketplace", "ts": t,
            "ext_id": f"bact:{g}:{a.get('ACTIVITY_TIME')}:{a.get('ACTIVITY_ID')}:{a.get('PRODUCT_ID')}",
            "payload": {
                "type": a.get("ACTIVITY_TYPE"), "category": a.get("CATEGORY_NAME"),
                "keyword": a.get("KEYWORD"), "seller_glid": a.get("SELLER_GLUSR_ID"),
            },
        })

    if last_lead.get("TITLE") and last_lead.get("DATE_R"):
        out.append({
            "glid": g, "role": "buyer", "source": "buylead", "channel": "marketplace",
            "ts": last_lead["DATE_R"][:19].replace(" ", "T"), "ext_id": f"blead:{last_lead.get('LEAD_ID')}",
            "payload": {"title": last_lead["TITLE"], "posted": True},
        })

    for num in ("9696", "8181"):
        for i, m in enumerate(act.get(f"LAST_WA{num}_CHAT") or []):
            text = wa_text(m.get("CHAT_CONTENT"))
            when = (m.get("ENTRY_DATE") or "")[:19].replace(" ", "T")
            if text and when:
                out.append({
                    "glid": g, "role": "buyer", "source": "whatsapp_bot", "channel": "whatsapp", "ts": when,
                    "ext_id": f"bwa:{g}:{num}:{when}:{i}",
                    "payload": {"sender": m.get("MESSAGE_SENDER"), "text": text[:240]},
                })
    return out


def load(only_glids=None):
    n_snap = n_ev = 0
    with store.connect() as c:
        for d in snapshots():
            if only_glids is not None and str(d["glid"]) not in only_glids:
                continue
            evs = events_from(d)
            store.insert_events(c, evs)
            n_snap += 1
            n_ev += len(evs)
    return {"snapshots": n_snap, "events": n_ev}
