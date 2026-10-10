"""Caps, eviction, budget, privacy and cold start. Runs against a throwaway DB with synthetic events only."""
import importlib
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GC_NO_LLM", "1")
    from globalctx import config
    importlib.reload(config)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(config, "FRESHNESS_LOG", tmp_path / "fresh.csv")
    from globalctx import store
    store.init()
    return config, store


def ago(days, hours=0):
    return (datetime.now() - timedelta(days=days, hours=hours)).isoformat(timespec="seconds")


def seed_heavy_seller(store, glid="111", n=500):
    rows = [{"glid": glid, "role": "seller", "source": "profile", "ts": ago(1), "ext_id": f"p{glid}", "payload": {
        "company_name": "Test Traders", "seller_city": "Pune", "seller_state": "Maharashtra",
        "top_category_1": "Steel Pipes", "gst_verified_flag": "1", "showed_frustration": "1.0"}}]
    for i in range(n):
        d = (i % 120) + 0.1
        rows += [
            {"glid": glid, "role": "seller", "source": "enquiry", "channel": "marketplace", "ts": ago(d),
             "ext_id": f"e{i}", "payload": {"query_id": str(i), "product": f"Steel Pipe {i % 7}", "buyer_city": "Delhi",
                                           "read": i % 3 == 0, "message": "call me at 9876543210"}},
            {"glid": glid, "role": "seller", "source": "vani_call", "channel": "voice", "ts": ago(d),
             "ext_id": f"v{i}", "payload": {"summary": f"Seller talked about pipes {i}. Email x@y.com", "duration": "40",
                                           "disposition": "General (talked)"}},
            {"glid": glid, "role": "seller", "source": "whatsapp_bot", "channel": "whatsapp", "ts": ago(d),
             "ext_id": f"w{i}", "payload": {"session_id": str(i), "user_message": f"price list {i}", "intent": "x"}},
        ]
    with store.connect() as c:
        store.insert_events(c, rows)


def section_rows(md, name):
    body = md.split(f"## {name}\n", 1)[1].split("\n## ", 1)[0]
    return [l for l in body.splitlines() if l.strip()]


def test_caps_budget_and_privacy(env):
    config, store = env
    from globalctx.build.builder import build
    seed_heavy_seller(store)
    out = build("111", "seller", use_llm=False)
    md = out["md"]
    assert len(md) <= config.MAX_CHARS
    for name, cap in config.SECTION_CAPS["seller"].items():
        assert len(section_rows(md, name)) <= cap, name
    assert "9876543210" not in md and "x@y.com" not in md
    assert out["evicted"].get("Past Conversations", 0) > 0          # older history evicted, and counted
    assert "evicted:" in md and "Caution:" in md                   # risk flag ranks above metrics


def test_lookback_expiry(env):
    config, store = env
    from globalctx.build.builder import build
    with store.connect() as c:
        store.insert_events(c, [{"glid": "222", "role": "seller", "source": "whatsapp_bot", "channel": "whatsapp",
                                 "ts": ago(45), "ext_id": "old", "payload": {"session_id": "s", "user_message": "OLD MSG"}}])
    md = build("222", "seller", use_llm=False)["md"]
    assert "OLD MSG" not in md          # WhatsApp lookback is 30 days


def test_pinned_session_and_closed_threads(env):
    config, store = env
    from globalctx.build.builder import build
    seed_heavy_seller(store, "333", n=50)
    store.add_event("333", "seller", "session", "chat", {"kind": "summary", "summary": "wants 200 pipes",
                                                         "requirement": "steel pipe", "callback": "10 Oct, 5 PM",
                                                         "open_threads": ["send quote"]}, ts=ago(3))
    md = build("333", "seller", use_llm=False)["md"]
    assert "(our bot): wants 200 pipes" in section_rows(md, "Past Conversations")[0]
    assert "Callback promised" in md and "requirement: steel pipe" in md
    # a newer session without a callback closes the old thread
    store.add_event("333", "seller", "session", "voice", {"kind": "summary", "summary": "quote sent, done"}, ts=ago(0, 1))
    md2 = build("333", "seller", use_llm=False)["md"]
    assert "Callback promised" not in md2 and "quote sent" in section_rows(md2, "Past Conversations")[0]


def test_cold_start(env):
    config, store = env
    from globalctx.build.builder import build
    for role in ("seller", "buyer"):
        md = build("999", role, use_llm=False)["md"]
        assert "data_quality: cold_start" in md
        assert "## Suggested Opening" not in md                  # the file holds facts only
        from globalctx import sessions
        assert "Aap kuch khareedna chahte hain" in sessions.opening_for("999", role)  # role-neutral cold-start greeting
        assert list(config.SECTION_CAPS[role]) == [l[3:] for l in md.splitlines() if l.startswith("## ")]


def test_parse_roundtrip(env):
    config, store = env
    from globalctx.build.builder import build
    from globalctx.build.render import parse
    seed_heavy_seller(store, "444", n=20)
    p = parse(build("444", "seller", use_llm=False)["md"])
    assert p["meta"]["role"] == "seller" and set(p["sections"]) == set(config.SECTION_CAPS["seller"])


def test_needs_grouped_by_product(env):
    """Buyer: searches, enquiries and calls for one product share a row with its posted requirement.
    Seller: enquiries, buyer calls (matched by mcat) and BuyLeads for one product share a row; totals last."""
    config, store = env
    from globalctx.build.builder import build
    b = [{"glid": "555", "role": "buyer", "source": "buyer_activity", "channel": "marketplace", "ts": ago(1, i),
          "ext_id": f"a{i}", "payload": {"type": t, "category": c, "keyword": "-", "seller_glid": str(i)}}
         for i, (t, c) in enumerate([("Search", "Rubber Gumboots"), ("ENQ", "Gumboots"), ("C2C", "Gumboots"),
                                     ("Search", "Plastic Containers"), ("Browse", "Plastic Bottle Making Machine")])]
    b.append({"glid": "555", "role": "buyer", "source": "buylead", "channel": "marketplace", "ts": ago(0, 1),
              "ext_id": "bl", "payload": {"title": "Rubber half-length gumboots", "suppliers_connected": 0}})
    s = [{"glid": "666", "role": "seller", "source": "enquiry", "channel": "marketplace", "ts": ago(i + 1), "ext_id": f"e{i}",
          "payload": {"query_id": str(i), "product": "Toor Dal", "mcat_id": "9", "buyer_city": "Pune", "read": i > 0}}
         for i in range(3)]
    s += [{"glid": "666", "role": "seller", "source": "buyer_call", "channel": "phone", "ts": ago(2), "ext_id": "c1",
           "payload": {"status": "Connected", "talk_sec": 30, "mcat_id": "9"}},
          {"glid": "666", "role": "seller", "source": "buylead", "channel": "marketplace", "ts": ago(2), "ext_id": "l1",
           "payload": {"keyword": "Toor Dal Premium"}}]
    with store.connect() as c:
        store.insert_events(c, b + s)
    needs = section_rows(build("555", "buyer", use_llm=False)["md"], "Buying Needs")
    assert needs[0].startswith("- Rubber half-length gumboots: requirement posted")
    assert "1 enquiry" in needs[0] and "1 seller called" in needs[0]
    assert not any("Gumboots:" in r for r in needs[1:])                   # merged, not repeated
    assert any("Plastic Containers" in r for r in needs) and any("Plastic Bottle" in r for r in needs)
    demand = section_rows(build("666", "seller", use_llm=False)["md"], "Buyer Demand by Product")
    assert demand[0].startswith("- Toor Dal: 3 enquiries (1 unread)")
    assert "1 buyer call (1 answered)" in demand[0] and "1 BuyLead bought" in demand[0]
    assert demand[-1].startswith("- Totals (90d)")
