"""Yaad v2 engine on the Raju (buyer) / Kaju (seller) journeys: mapping, returning buyer, newest-wins facts,
problem threads, two-sided mirror, firewall, customer card, cold start, budget.
Offline: a scratch DB, and the LLM summary is replaced by a deterministic stub."""
import os
import tempfile

os.environ["YAAD_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["SARVAM_LLM_API_KEY"] = ""

import pytest  # noqa: E402

from yaad import config, context, extract, story, updater  # noqa: E402

config.SARVAM_LLM_API_KEY = ""


def fake_conversation(turns, role, channel):
    text = " ".join(t for s, t in turns if s == "user").lower()
    out = {"one_line": f"{channel} conversation", "summary": "stub", "language": "Hinglish", "sentiment": "neutral",
           "closed": False, "next_step": None, "qty": extract._qty(text), "product": None,
           "complaint_issue": extract._complaint(turns), "complaint_seller": None}
    if "polo" in text or "piece" in text or "quote" in text:
        out["product"] = "Polo T-Shirt"
    if "whatsapp" in text or "quote" in text:
        out["next_step"] = "send quotes"
    return out


@pytest.fixture(autouse=True)
def stub_llm(monkeypatch):
    monkeypatch.setattr(extract, "conversation", fake_conversation)


def play(persona, upto=None):
    s = story.reset(persona)
    out = {}
    for i, st in enumerate(s["steps"][:upto]):
        if st["kind"] == "event":
            out[i] = updater.apply(story.event_body(persona, i))
        elif st["kind"] in ("call", "chat"):
            fb = st["fallback"]
            body = {"glid": s["glid"], "role": s["role"], "type": "conversation", "synthetic": 1,
                    "payload": {"turns": fb["turns"], "channel": fb["channel"], "platform_vars": fb.get("platform_vars")}}
            out[i] = updater.apply(body)
    return out


def polo(c):
    return next(t for t in c["threads"] if t["title"] == "Polo T-Shirt")


def test_history_is_one_thread_with_a_quote():
    story.reset("raju")
    t = polo(context.build("SYN-B-2001", "buyer"))
    assert t["stage"] == "IN_TALKS" and "quoted ₹240/pc" in t["status"]
    assert any("200" in k for k in t["known"])


def test_returning_buyer_lands_on_the_same_thread_with_a_returning_opening():
    r = play("raju", upto=3)
    assert r[2]["map_method"] == "exact" and not r[2]["new_thread"]
    c = context.build("SYN-B-2001", "buyer")
    assert "phir se Polo T-Shirt" in c["opening"] and "₹240/pc" in c["opening"]


def test_call_without_mcat_maps_by_connected_seller():
    r = play("raju", upto=4)
    assert r[3]["map_method"] == "counterparty"  # PNS to Kaju, who is already a connected seller on the thread
    assert r[2]["thread_id"] == r[3]["thread_id"]


def test_call_updates_qty_newest_wins_and_reaches_the_seller():
    play("raju", upto=5)
    t = polo(context.build("SYN-B-2001", "buyer"))
    assert t["stage"] == "PROMISED"
    assert any("500" in k for k in t["known"]) and not any("200" in k for k in t["known"])
    assert "500" in context.build("SYN-S-1001", "seller")["md"]  # Kaju gets the update, as an aggregate


def test_complaint_becomes_ranked_problem_thread_linked_to_seller():
    r = play("raju", upto=6)
    problem = r[5]["mirrored"][0]
    assert problem["kind"] == "problem" and problem["confidence"] == "high"
    c = context.build("SYN-B-2001", "buyer")
    assert c["threads"][0]["kind"] == "problem" and "complaint" in c["opening"]
    assert "Delhi Knit" in c["problems"][0]["summary"]


def test_customer_card_in_md():
    story.reset("raju")
    md = context.build("SYN-B-2001", "buyer")["md"]
    assert "## Customer card" in md and "GST verified" in md and "Delhi (Tier 1)" in md and "Delhi, Delhi" not in md
    assert "10 enquiries · 20 PNS calls · 10 buy-leads" in md and "(+2)" in md
    md = context.build("SYN-S-1001", "seller")["md"]
    assert "Kaju Garments" in md and "reply rate 40%" in md and "- Sells:" in md


def test_firewall_seller_never_sees_buyer_identity_or_complaint():
    play("raju", upto=6)
    md = context.build("SYN-S-1001", "seller")["md"]
    for word in ("Raju", "SYN-B-2001", "omplaint", "Delhi Knit", "sample"):
        assert word not in md


def test_seller_journey_two_sided_loop():
    play("kaju", upto=3)
    c = context.build("SYN-S-1001", "seller")
    stages = {t["title"]: t["stage"] for t in c["threads"]}
    assert stages["IndiaMART follow-up"] == "CALLBACK_DUE" and stages["Catalog"] == "CATALOG_ISSUE"
    assert c["threads"][0]["stage"] == "CALLBACK_DUE"
    s = story.load("kaju")
    updater.apply(story.event_body("kaju", 3))  # Raju's lead reaches Kaju
    c = context.build("SYN-S-1001", "seller")
    assert "Polo T-Shirt" in {t["title"] for t in c["threads"]}  # its own thread, not merged into Round Neck
    assert "Delhi" in c["opening"]
    fb = s["steps"][4]["fallback"]
    r = updater.apply({"glid": "SYN-S-1001", "role": "seller", "type": "conversation", "synthetic": 1,
                       "payload": {"turns": fb["turns"], "channel": fb["channel"], "platform_vars": fb["platform_vars"]}})
    assert r["stage"] == "PROMISED"
    assert any(m["glid"] == "SYN-B-2001" for m in r["mirrored"])  # Raju's memory learns Kaju will quote
    assert "Kaju Garments" in context.build("SYN-B-2001", "buyer")["md"]
    seller_md = context.build("SYN-S-1001", "seller")["md"]
    assert "Kiran" not in seller_md and "Raju" not in seller_md


def test_cold_start_is_generic_and_budget_holds():
    c = context.build("NEVER-SEEN", "buyer")
    assert c["cold_start"] and not c["threads"] and "kya madad" in c["opening"]
    play("raju", upto=6)
    for glid, role in (("SYN-B-2001", "buyer"), ("SYN-S-1001", "seller")):
        c = context.build(glid, role)
        assert c["tokens"] <= config.TOKEN_BUDGET[role]
        for h in ("## Customer card", "## Open problems", "## Threads", "## Guardrails", "## Suggested opening"):
            assert h in c["md"]


def test_complaint_never_closes_the_requirement(monkeypatch):
    def closing_stub(turns, role, channel):
        out = fake_conversation(turns, role, channel)
        out["closed"] = True
        return out
    play("raju", upto=5)
    monkeypatch.setattr(extract, "conversation", closing_stub)
    updater.apply({"glid": "SYN-B-2001", "role": "buyer", "type": "conversation", "synthetic": 1,
                   "payload": {"channel": "WhatsApp chat",
                               "turns": [["user", "Delhi Knit House ka sample kharab tha"]]}})
    kinds = [t["kind"] for t in context.build("SYN-B-2001", "buyer")["threads"]]
    assert "problem" in kinds and "requirement" in kinds


def test_a_goodbye_does_not_close_the_requirement(monkeypatch):
    def closing_stub(turns, role, channel):
        out = fake_conversation(turns, role, channel)
        out.update(closed=True, next_step=None)
        return out
    play("raju", upto=5)
    monkeypatch.setattr(extract, "conversation", closing_stub)
    updater.apply({"glid": "SYN-B-2001", "role": "buyer", "type": "conversation", "synthetic": 1,
                   "payload": {"channel": "Web call", "turns": [["user", "theek hai, dhanyavaad"]]}})
    assert polo(context.build("SYN-B-2001", "buyer"))["stage"] != "CLOSED"
    updater.apply({"glid": "SYN-B-2001", "role": "buyer", "type": "conversation", "synthetic": 1,
                   "payload": {"channel": "Web call", "turns": [["user", "polo t shirt mil gaya, ab nahi chahiye"]]}})
    t = polo(context.build("SYN-B-2001", "buyer"))
    assert t["stage"] == "CLOSED"  # explicit closure closes it, and it stays visible


def test_resetting_one_persona_keeps_the_other():
    play("raju", upto=5)
    story.reset("kaju")
    assert polo(context.build("SYN-B-2001", "buyer"))["stage"] == "PROMISED"  # Raju's call survives
