"""Load the organiser CSVs (stand-ins for the starter APIs) into the event store.

Each source becomes minimised events for BOTH parties where both are known: an enquiry is a
"received" event in the seller's stream and a "sent" event in the buyer's stream. Lookback
windows are applied at build time (threads.py), so the store keeps full history and can replay.
"""
import csv
import json
import time
from collections import Counter, defaultdict

from . import config, store

csv.field_size_limit(10**9)


def _rows(name):
    with open(config.DATA_DIR / name, encoding="utf-8", errors="replace", newline="") as f:
        yield from csv.DictReader(f)


def _ts(v):
    return (v or "")[:19]


def _norm(s):
    return " ".join((s or "").lower().split())[:60]


def _ev(glid, role, source, channel, ts, payload, cp=None, key=None):
    return (str(glid), role, source, channel, _ts(ts), cp, key, json.dumps(payload, ensure_ascii=False), None, 0, time.time())


def run(log=print):
    c = store.conn()
    c.executescript("DELETE FROM events WHERE synthetic=0; DELETE FROM profiles WHERE synthetic=0; DELETE FROM names;")
    batch = []

    def flush():
        c.executemany("INSERT INTO events(glid, role, source, channel, ts, counterparty_glid, thread_key, payload,"
                      " extracted, synthetic, ingested_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch)
        batch.clear()

    # Seller profiles (one snapshot per seller) and seller names for buyer.md
    names = {}
    n = 0
    for r in _rows("gc_seller_profile.csv"):
        g = r["fk_glusr_usr_id"]
        c.execute("INSERT OR REPLACE INTO profiles(glid, data) VALUES (?,?)", (g, json.dumps(r, ensure_ascii=False)))
        names[g] = (r.get("company_name") or "", r.get("seller_city") or "")
        n += 1
    log(f"profiles: {n}")

    # Enquiries: who replied, and mcat -> product name map
    seller_replied, buyer_followed = set(), set()
    for r in _rows("gc_enquiry_messages.csv"):
        (seller_replied if r["message_from"].lower() == "seller" else buyer_followed).add(r["query_id"])
    mcat_name = defaultdict(Counter)
    n = 0
    for r in _rows("gc_enquiries_received.csv"):
        q, s, b = r["query_id"], r["seller_glid"], r["buyer_glid"]
        product = r["product_name"] or r["subject"] or r["search_keyword"]
        key = r["mcat_id"] or _norm(product)
        if r["mcat_id"] and product:
            mcat_name[r["mcat_id"]][product] += 1
        if s and s not in names:
            names[s] = (r["seller_company"], r["seller_city"])
        replied = q in seller_replied
        if s:  # seller stream: aggregates about the buyer only (city/state), never identity
            batch.append(_ev(s, "seller", "enquiry", "Enquiry", r["enquiry_date"],
                             {"product": product, "buyer_city": r["buyer_city"], "buyer_state": r["buyer_state"],
                              "read": bool(r["first_read_date"]), "replied": replied, "module": r["source_module"]},
                             cp=b, key=key))
        if b:  # buyer stream: their own requirement + which seller they contacted
            batch.append(_ev(b, "buyer", "enquiry", "Enquiry", r["enquiry_date"],
                             {"product": product, "keyword": r["search_keyword"], "seller_glid": s,
                              "seller_replied": replied, "buyer_city": r["buyer_city"], "buyer_state": r["buyer_state"],
                              "buyer_company": r["buyer_company"], "buyer_designation": r["buyer_designation"],
                              "message": (r["message"] or "")[:600]},
                             cp=s, key=key))
        n += 1
        if len(batch) > 20000:
            flush()
    flush()
    log(f"enquiries: {n}")
    mcat_top = {m: cnt.most_common(1)[0][0] for m, cnt in mcat_name.items()}

    # PNS buyer -> seller calls
    n = 0
    for r in _rows("gc_buyer_calls_received.csv"):
        s, b, m = r["seller_glid"], r["buyer_glid"], r["mcat_id"]
        ok = r["call_status"].lower().startswith("connected")
        product = mcat_top.get(m, "")
        if s:
            batch.append(_ev(s, "seller", "pns", "Buyer call", r["call_datetime"],
                             {"connected": ok, "talk_sec": r["talk_sec"], "product": product, "buyer_state": r["caller_circle"]},
                             cp=b, key=m or None))
        if b:
            batch.append(_ev(b, "buyer", "pns", "Call to seller", r["call_datetime"],
                             {"connected": ok, "talk_sec": r["talk_sec"], "product": product, "seller_glid": s},
                             cp=s, key=m or None))
        n += 1
    flush()
    log(f"pns: {n}")

    # Buy-leads bought by sellers
    n = 0
    for r in _rows("gc_buyleads_bought.csv"):
        s, b, kw = r["seller_glid"], r["buyer_glid"], r["keyword"]
        if s:
            batch.append(_ev(s, "seller", "bl", "BuyLead", r["purchase_date"],
                             {"product": kw, "credits": r["credits_used"], "city_match": r["is_pref_location"]},
                             cp=b, key=_norm(kw)))
        if b:
            batch.append(_ev(b, "buyer", "bl", "BuyLead", r["purchase_date"],
                             {"product": kw, "seller_glid": s}, cp=s, key=_norm(kw)))
        n += 1
    flush()
    log(f"buyleads: {n}")

    # VANI bot calls (seller)
    n = 0
    for r in _rows("gc_bot_calls.csv"):
        batch.append(_ev(r["fk_glusr_usr_id"], "seller", "bot_call", "VANI call", r["call_start_time"],
                         {"disposition": r["disposition_label"], "meeting_fixed": r["meeting_fixed"] == "1",
                          "duration": r["lead_call_duration"], "summary": (r["lead_call_summary"] or "")[:800],
                          "attempt_id": r["attempt_id"]}, key="__vani__"))
        n += 1
    flush()
    log(f"bot calls: {n}")

    # Executive calls (seller)
    n = 0
    for r in _rows("gc_executive_calls.csv"):
        batch.append(_ev(r["fk_glusr_usr_id"], "seller", "exec_call", "Executive call", r["call_start_time"],
                         {"status": r["status"], "module": r["module"], "duration": r["call_duration"]}, key="__exec__"))
        n += 1
    flush()
    log(f"exec calls: {n}")

    # WhatsApp chatbot (seller): intent only; free text stays out of the store
    n = 0
    for r in _rows("gc_whatsapp_chatbot_conversations.csv"):
        if not r["intent"]:
            continue
        batch.append(_ev(r["glid"], "seller", "whatsapp", "WhatsApp", r["entry_date"],
                         {"intent": r["intent"], "action": r["action_type"], "campaign": r["campaign_name"]},
                         key="__wa__:" + r["intent"][:40]))
        n += 1
        if len(batch) > 20000:
            flush()
    flush()
    log(f"whatsapp intents: {n}")

    # AI-extracted PNS call details: the GLID was BUYER or SELLER on that call
    n = 0
    for r in _rows("gc_buyer_call_extractions.csv"):
        role = "buyer" if r["seller_role_on_call"].upper() == "BUYER" else "seller"
        batch.append(_ev(r["glid"], role, "call_extract", "Call (AI notes)", r["call_date"],
                         {"intent": r["file_intent"], "languages": r["languages"], "products": r["products_discussed"][:200],
                          "categories": r["categories"][:120], "prices": r["prices_quoted"][:120],
                          "specs": r["specs_discussed"][:160], "stock": r["stock_status"][:60]},
                         key=_norm(r["categories"] or r["products_discussed"])))
        n += 1
    flush()
    log(f"call extractions: {n}")

    c.executemany("INSERT OR REPLACE INTO names(glid, company, city) VALUES (?,?,?)",
                  [(g, a, b) for g, (a, b) in names.items()])
    c.commit()
    total = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    log(f"events in store: {total}")
