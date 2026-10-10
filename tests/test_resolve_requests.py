"""Role resolution (buyer/seller/new), request folders, cold-start move and preferred language."""
import importlib
from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GC_NO_LLM", "1")
    from globalctx import config
    importlib.reload(config)
    for k, v in {"DATA_DIR": tmp_path, "DB_PATH": tmp_path / "t.db", "PROFILES_DIR": tmp_path / "profiles",
                 "FRESHNESS_LOG": tmp_path / "f.csv", "REQUESTS_DIR": tmp_path / "requests"}.items():
        monkeypatch.setattr(config, k, v)
    from globalctx import store
    store.init()
    return config, store


def ago(days):
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def ev(store, glid, role, source, days, payload, ext):
    with store.connect() as c:
        store.insert_events(c, [{"glid": glid, "role": role, "source": source, "channel": "x", "ts": ago(days),
                                 "ext_id": ext, "payload": payload}])


def test_resolve_one_both_none(env):
    config, store = env
    from globalctx.resolve import resolve
    ev(store, "1", "seller", "enquiry", 5, {"product": "pipe"}, "a")
    assert resolve("1")[0] == "seller"
    ev(store, "2", "buyer", "buyer_activity", 5, {"type": "Browse"}, "b")
    assert resolve("2")[0] == "buyer"
    ev(store, "3", "seller", "enquiry", 20, {"product": "pipe"}, "c")
    ev(store, "3", "buyer", "buyer_activity", 2, {"type": "Browse"}, "d")
    assert resolve("3")[0] == "buyer"            # most recent activity wins
    ev(store, "3", "seller", "enquiry", 1, {"product": "pipe"}, "e")
    assert resolve("3")[0] == "seller"
    assert resolve("999") == (config.DEFAULT_NEW_ROLE, "no file yet: a cold-start file is created on first use")


def test_new_glid_gets_cold_file_then_moves_to_seller(env):
    config, store = env
    from globalctx import sessions
    role, md, _ = sessions.load("777")
    assert role == config.DEFAULT_NEW_ROLE and "data_quality: cold_start" in md
    assert (config.PROFILES_DIR / role / "777.md").exists()
    summary = {"summary": "wants to list steel pipes", "user_role": "seller",
               "requests": [{"type": "catalogue_update", "product": "Steel Pipe", "details": "add product"}]}
    new_role, fast, paths = sessions.record("777", role, "chat", "s1", summary, cold=True)
    assert new_role == "seller" and not (config.PROFILES_DIR / "buyer" / "777.md").exists()
    assert (config.PROFILES_DIR / "seller" / "777.md").exists()
    assert paths and paths[0].endswith("catalogue_updation_requests/777.md")


def test_request_folders_and_open_thread(env):
    config, store = env
    from globalctx import sessions
    ev(store, "5", "buyer", "buyer_activity", 2, {"type": "ENQ", "category": "Pumps"}, "p")
    summary = {"summary": "needs pumps", "requests": [
        {"type": "requirement", "product": "Water Pump", "quantity": "10 pieces", "location": "Pune"},
        {"type": "enquiry", "product": "Solar Pump", "details": "quote from the listed seller"}]}
    role, fast, paths = sessions.record("5", "buyer", "voice", "s2", summary)
    names = sorted(p.split("/")[-2] for p in paths)
    assert names == ["enquiry", "requirements"]
    req = (config.REQUESTS_DIR / "requirements" / "5.md").read_text()
    assert "Water Pump" in req and "pending: 1" in req and "## Pending" in req
    md = store.get_profile("5", "buyer")["md"]
    assert "Pending requirement to post: Water Pump" in md


def test_identity_language(env):
    config, store = env
    from globalctx.build.builder import build
    ev(store, "8", "seller", "profile", 1, {"company_name": "Ram Traders", "seller_state": "Tamil Nadu"}, "pr")
    md = build("8", "seller", use_llm=False)["md"]
    assert "## Identity" in md and "Business: Ram Traders" in md and "region speaks Tamil" in md
    ev(store, "8", "seller", "whatsapp_bot", 1, {"session_id": "1", "user_message": "விலை என்ன?"}, "w1")
    ev(store, "8", "seller", "whatsapp_bot", 1, {"session_id": "2", "user_message": "ஸ்டாக் இருக்கு"}, "w2")
    assert "Preferred language: Tamil (own messages)" in build("8", "seller", use_llm=False)["md"]


def test_contact_name_captured_and_used(env):
    config, store = env
    from globalctx import sessions
    from globalctx.build.builder import build
    ev(store, "9", "seller", "profile", 1, {"company_name": "Shiv Pulses"}, "p9")
    md = build("9", "seller", use_llm=False)["md"]
    assert "Contact: not known yet (ask once)" in md
    sessions.record("9", "seller", "phone", "s9", {"summary": "talked about dal", "contact_name": "Yashwant"})
    md = store.get_profile("9", "seller")["md"]
    assert "Business: Shiv Pulses · Contact: Yashwant" in md
    assert "## Suggested Opening" not in md                       # the file holds facts only
    assert "Yashwant ji" in sessions.opening_for("9", "seller")    # the opening is kept beside it
