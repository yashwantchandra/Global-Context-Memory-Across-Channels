"""Yaad v2 engine: mapping, stages, newest-wins facts, problem threads, two-sided mirror, firewall, cold start, budget.
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
    if "gi pipe" in text or "piece" in text or "quote" in text:
        out["product"] = "GI Pipe"
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


def test_buyer_journey_maps_every_event_to_one_thread():
    r = play("amit", upto=6)
    assert [r[i]["map_method"] for i in (1, 2, 3, 4, 5)] == ["new_exact", "exact", "exact", "sibling_mcat", "counterparty"]
    assert len({r[i]["thread_id"] for i in (1, 2, 3, 4, 5)}) == 1
    assert r[3]["stage"] == "SELLERS_CONNECTED"
    assert len(r[3]["mirrored"]) == 2  # the match is written into both sellers' memory


def test_call_updates_qty_newest_wins_and_promises():
    play("amit", upto=7)
    c = context.build("SYN-B-2001", "buyer")
    gi = next(t for t in c["threads"] if t["title"] == "GI Pipe")
    assert gi["stage"] == "PROMISED"
    assert any("400" in k for k in gi["known"]) and not any("500" in k for k in gi["known"])
    seller = context.build("SYN-S-1001", "seller")
    assert "400" in seller["md"]  # the buyer's update reached the connected seller, as an aggregate


def test_complaint_becomes_ranked_problem_thread_linked_to_seller():
    r = play("amit", upto=8)
    problem = r[7]["mirrored"][0]
    assert problem["kind"] == "problem" and problem["confidence"] == "high"
    c = context.build("SYN-B-2001", "buyer")
    assert c["threads"][0]["kind"] == "problem"  # an open complaint outranks everything
    assert "complaint" in c["opening"]
    assert "Om Sai" in c["problems"][0]["summary"]


def test_firewall_seller_never_sees_buyer_identity_or_complaint():
    play("amit", upto=8)
    md = context.build("SYN-S-1001", "seller")["md"]
    for word in ("Amit", "Patil", "SYN-B-2001", "omplaint", "Om Sai"):
        assert word not in md


def test_seller_journey_two_sided_loop():
    play("rakesh", upto=2)
    c = context.build("SYN-S-1001", "seller")
    stages = {t["title"]: t["stage"] for t in c["threads"]}
    assert stages["IndiaMART follow-up"] == "CALLBACK_DUE" and stages["Catalog"] == "CATALOG_ISSUE"
    assert c["threads"][0]["stage"] == "CALLBACK_DUE"
    play_from = story.load("rakesh")
    updater.apply(story.event_body("rakesh", 2))  # Amit's lead reaches Rakesh
    c = context.build("SYN-S-1001", "seller")
    assert "GI Pipe" in {t["title"] for t in c["threads"]}  # its own thread, not merged into SS 304 Pipe
    assert "Pune" in c["opening"]
    fb = play_from["steps"][3]["fallback"]
    r = updater.apply({"glid": "SYN-S-1001", "role": "seller", "type": "conversation", "synthetic": 1,
                       "payload": {"turns": fb["turns"], "channel": fb["channel"], "platform_vars": fb["platform_vars"]}})
    assert r["stage"] == "PROMISED"
    assert any(m["glid"] == "SYN-B-2001" for m in r["mirrored"])  # Amit's memory learns the seller will quote
    buyer = context.build("SYN-B-2001", "buyer")
    assert "Shree Ganesh" in buyer["md"]
    assert "Kiran" not in context.build("SYN-S-1001", "seller")["md"]


def test_cold_start_is_generic_and_budget_holds():
    c = context.build("NEVER-SEEN", "buyer")
    assert c["cold_start"] and not c["threads"] and "kya madad" in c["opening"]
    play("amit", upto=8)
    for glid, role in (("SYN-B-2001", "buyer"), ("SYN-S-1001", "seller")):
        c = context.build(glid, role)
        assert c["tokens"] <= config.TOKEN_BUDGET[role]
        for h in ("## Snapshot", "## Open problems", "## Threads", "## Guardrails", "## Suggested opening"):
            assert h in c["md"]


def test_complaint_never_closes_the_requirement(monkeypatch):
    def closing_stub(turns, role, channel):
        out = fake_conversation(turns, role, channel)
        out["closed"] = True  # the model sometimes says "closed" when the customer is wrapping up a complaint
        return out
    play("amit", upto=7)
    monkeypatch.setattr(extract, "conversation", closing_stub)
    updater.apply({"glid": "SYN-B-2001", "role": "buyer", "type": "conversation", "synthetic": 1,
                   "payload": {"channel": "WhatsApp chat", "turns": [["user", "Om Sai Steel ne advance le liya, maal nahi bheja"]]}})
    c = context.build("SYN-B-2001", "buyer")
    kinds = [t["kind"] for t in c["threads"]]
    assert "problem" in kinds and "requirement" in kinds
