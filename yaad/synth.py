"""Labelled synthetic personas (synthetic=1 on every row). Used for development, tests, public samples,
and as the clean cold-start GLID. All names and businesses are fictional."""
import json
from datetime import timedelta

from . import config, store

SELLER, BUYER, COLD = "SYN-S-1001", "SYN-B-2001", "SYN-COLD-0001"
OTHER_SELLERS = {"SYN-S-1002": ("Kaveri Pipes & Fittings", "Pune"), "SYN-S-1003": ("Om Sai Steel Mart", "Mumbai"),
                 "SYN-S-1004": ("Deccan Tubes Co.", "Nashik")}


def load():
    c = store.conn()
    c.execute("DELETE FROM events WHERE synthetic=1 OR glid LIKE 'SYN-%'")  # incl. our conversations with personas
    c.execute("DELETE FROM profiles WHERE synthetic=1")
    now = config.as_of()
    ago = lambda d, h=11: (now - timedelta(days=d)).replace(hour=h, minute=5, second=0)

    prof = {"company_name": "Shree Ganesh Steel Traders", "seller_city": "Rajkot", "seller_state": "Gujarat",
            "business_type": "Proprietorship", "annual_turnover": "40 L - 1.5 Cr", "customer_type": "vgFCPplus with PNS",
            "eng_product_count": "38", "top_category_1": "Stainless Steel Pipes", "top_category_2": "GI Pipes",
            "top_category_3": "Pipe Fittings", "do_not_call_requested": "0.0", "showed_frustration": "0.0",
            "asked_if_talking_to_bot": "1.0", "already_in_touch_with_executive": "0.0",
            "past_objections": "Says plan is too costly for a small business"}
    c.execute("INSERT OR REPLACE INTO profiles(glid, data, synthetic) VALUES (?,?,1)", (SELLER, json.dumps(prof)))
    for g, (co, city) in {**OTHER_SELLERS, SELLER: ("Shree Ganesh Steel Traders", "Rajkot")}.items():
        c.execute("INSERT OR REPLACE INTO names(glid, company, city) VALUES (?,?,?)", (g, co, city))
    c.commit()

    add = lambda *a, **k: store.add_event(*a, synthetic=True, rebuild=False, **k)
    # seller: VANI history, pending enquiries, stuck catalog photo, Gujarati calls
    add(SELLER, "seller", "bot_call", "VANI call", ago(40), {"disposition": "Not Interested", "summary": ""}, thread_key="__vani__",
        extracted={"one_line": "Said plan too costly; not interested right now", "objection": "price of plan"})
    add(SELLER, "seller", "bot_call", "VANI call", ago(6), {"disposition": "Call Later / Busy", "summary": ""}, thread_key="__vani__",
        extracted={"one_line": "Busy at shop, asked to call back after Navratri", "callback_date": "after Navratri"})
    for i in range(14):
        add(SELLER, "seller", "enquiry", "Enquiry", ago(2 + i * 2, 10 + i % 6),
            {"product": "SS 304 Pipe" if i % 3 else "GI Pipe", "buyer_city": ["Pune", "Ahmedabad", "Surat"][i % 3],
             "read": i % 2 == 0, "replied": i % 4 == 0}, counterparty=f"SYN-B-{3000 + i}", thread_key="ss304")
    add(SELLER, "seller", "whatsapp", "WhatsApp", ago(4), {"intent": "Photo uploaded but not added as a product as .."},
        thread_key="__wa__")
    for i in range(3):
        add(SELLER, "seller", "call_extract", "Call (AI notes)", ago(10 + i), {"languages": "['Gujarati','Hindi']",
            "intent": "requirement", "products": "SS 304 pipe"}, thread_key="ss304")

    # buyer: unanswered GI pipe requirement + failed call; older stale requirement
    for i, s in enumerate(OTHER_SELLERS):
        add(BUYER, "buyer", "enquiry", "Enquiry", ago(6, 9 + i),
            {"product": "GI Pipe 2 inch", "seller_glid": s, "seller_replied": False, "buyer_city": "Pune",
             "buyer_state": "Maharashtra", "buyer_company": "Patil Constructions", "buyer_designation": "Purchase Manager",
             "message": ""}, counterparty=s, thread_key="gi-pipe",
            extracted={"one_line": "Needs 2-inch GI pipes for a site", "qty": "500 pcs", "spec": "2 inch, medium class"})
    add(BUYER, "buyer", "pns", "Call to seller", ago(5), {"connected": False, "product": "GI Pipe 2 inch",
        "seller_glid": "SYN-S-1002"}, counterparty="SYN-S-1002", thread_key="gi-pipe")
    add(BUYER, "buyer", "enquiry", "Enquiry", ago(50), {"product": "Water Tank 1000L", "seller_glid": "SYN-S-1003",
        "seller_replied": True, "buyer_city": "Pune", "buyer_state": "Maharashtra", "buyer_company": "Patil Constructions",
        "buyer_designation": "Purchase Manager", "message": ""}, counterparty="SYN-S-1003", thread_key="tank")
    add(BUYER, "buyer", "synthetic", "Search", ago(3), {"product": "GI Pipe 2 inch", "summary": "searched 'GI pipe 2 inch Pune'"},
        thread_key="gi-pipe")
    return [SELLER, BUYER, COLD]
