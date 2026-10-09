"""Load the organiser seller dataset (11 CSVs) into the event store.

Only the columns the files need are kept. Free text that can carry other
people's details (enquiry subjects, reply texts, recording URLs) is not stored.
"""
import csv

from globalctx import config, store

csv.field_size_limit(10**9)

DATASET_END = "2026-10-02T00:00:00"


def ts(value):
    """'2026-08-13 06:17:42.465' -> '2026-08-13T06:17:42'. Empty -> ''."""
    v = (value or "").strip()
    return v[:19].replace(" ", "T") if v else ""


def rows(name):
    path = config.SELLER_DIR / name
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        yield from csv.DictReader(f)


def short(text, n=300):
    text = " ".join((text or "").split())
    return text[:n]


def ev(glid, source, channel, when, ext_id, payload):
    return {"glid": glid, "role": "seller", "source": source, "channel": channel,
            "ts": when, "ext_id": f"{source}:{ext_id}", "payload": payload}


PROFILE_DROP = {"seller_pincode", "seller_locality"}


def profiles():
    for r in rows("gc_seller_profile.csv"):
        g = r["fk_glusr_usr_id"]
        yield ev(g, "profile", None, DATASET_END, g, {k: v for k, v in r.items() if k not in PROFILE_DROP})


def enquiries():
    for r in rows("gc_enquiries_received.csv"):
        yield ev(r["seller_glid"], "enquiry", "marketplace", ts(r["enquiry_date"]), r["query_id"], {
            "query_id": r["query_id"], "product": short(r["product_name"], 80),
            "keyword": short(r["search_keyword"], 60), "mcat_id": r["mcat_id"],
            "message": short(r["message"], 160), "buyer_city": r["buyer_city"],
            "buyer_state": r["buyer_state"], "read": r["read_status"] == "1" or bool(r["first_read_date"]),
            "module": r["source_module"],
        })


def enquiry_messages():
    for r in rows("gc_enquiry_messages.csv"):
        yield ev(r["seller_glid"], "enquiry_message", "marketplace", ts(r["reply_date"]), r["reply_id"], {
            "query_id": r["query_id"], "from": r["message_from"], "sequence": r["sequence"],
            "template": r["template_flag"],
        })


def buyer_calls():
    for r in rows("gc_buyer_calls_received.csv"):
        yield ev(r["seller_glid"], "buyer_call", "phone", ts(r["call_datetime"]), r["call_id"], {
            "status": r["call_status"], "talk_sec": float(r["talk_sec"] or 0),
            "buyer_state": r["caller_circle"], "mcat_id": r["mcat_id"],
        })


def buyleads():
    for r in rows("gc_buyleads_bought.csv"):
        kw = (r["keyword"] or "").split("_p")[0]
        yield ev(r["seller_glid"], "buylead", "marketplace", ts(r["purchase_date"]), r["purchase_id"], {
            "keyword": short(kw, 60), "credits": r["credits_used"], "pref_location": r["is_pref_location"],
        })


def whatsapp_messages():
    for r in rows("gc_whatsapp_messages.csv"):
        yield ev(r["glid"], "whatsapp_msg", "whatsapp", ts(r["entry_date"] or r["sent_at"]), r["message_id"], {
            "sender": r["message_sender"], "campaign": r["campaign_name"],
            "status": r["message_status"], "read": bool(r["read_at"]),
        })


def whatsapp_bot():
    for r in rows("gc_whatsapp_chatbot_conversations.csv"):
        yield ev(r["glid"], "whatsapp_bot", "whatsapp", ts(r["entry_date"]), r["chat_id"], {
            "session_id": r["session_id"], "user_message": short(r["user_message"], 200),
            "bot_reply": short(r["bot_reply"], 200), "intent": r["intent"],
            "action": r["action_type"], "feedback": r["feedback"],
        })


def pns_extractions():
    for r in rows("gc_buyer_call_extractions.csv"):
        yield ev(r["glid"], "pns", "phone", ts(r["call_date"]), r["file_id"], {
            "role_on_call": r["seller_role_on_call"], "intent": r["file_intent"],
            "products": short(r["products_discussed"], 100), "categories": short(r["categories"], 100),
            "prices": short(r["prices_quoted"], 80), "specs": short(r["specs_discussed"], 100),
            "stock": r["stock_status"], "languages": r["languages"],
        })


def vani_calls():
    for r in rows("gc_bot_calls.csv"):
        yield ev(r["fk_glusr_usr_id"], "vani_call", "voice", ts(r["call_start_time"]), r["attempt_id"], {
            "summary": short(r["lead_call_summary"], 400), "disposition": r["disposition_label"],
            "meeting_fixed": r["meeting_fixed"], "duration": r["lead_call_duration"],
        })


def exec_calls():
    for r in rows("gc_executive_calls.csv"):
        yield ev(r["fk_glusr_usr_id"], "exec_call", "phone", ts(r["call_start_time"]), r["click_to_call_id"], {
            "status": r["status"], "duration": r["call_duration_customer"], "module": r["module"],
        })


LOADERS = [profiles, enquiries, enquiry_messages, buyer_calls, buyleads, whatsapp_messages,
           whatsapp_bot, pns_extractions, vani_calls, exec_calls]


def load(only_glids=None):
    counts = {}
    with store.connect() as c:
        for fn in LOADERS:
            batch = [e for e in fn() if e["ts"] and (only_glids is None or e["glid"] in only_glids)]
            store.insert_events(c, batch)
            counts[fn.__name__] = len(batch)
    return counts
