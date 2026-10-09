"""Hard requirements from the brief, on synthetic data only: cold start, firewall, size budget, resume."""
import os
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
os.environ["YAAD_DB"] = str(tmp / "t.db")
os.environ["YAAD_PROFILES"] = str(tmp / "profiles")
os.environ["SARVAM_API_KEY"] = ""  # tests never call the network

from yaad import config, parse, pipeline, store, synth  # noqa: E402

config.SARVAM_API_KEY = ""
config.SARVAM_LLM_API_KEY = ""
synth.load()


def md(glid, role):
    pipeline.rebuild(glid, role)
    return pipeline.read(glid, role)


def test_cold_start_is_generic_and_valid():
    for role in ("buyer", "seller"):
        p = parse.parse(md("NEVER-SEEN-123", role))
        assert p["meta"]["cold_start"] == "true"
        assert p["threads"] == []
        assert "kya madad" in p["opening"]


def test_schema_and_budget():
    for g, role in ((synth.SELLER, "seller"), (synth.BUYER, "buyer")):
        text = md(g, role)
        p = parse.parse(text)
        assert list(p["sections"]) == ["Who", "Known – don't ask", "Open threads", "Recent timeline", "Guardrails",
                                       "Suggested opening"]
        info = pipeline.rebuild(g, role)
        assert info["tokens"] <= config.TOKEN_BUDGET[role]
        assert p["meta"]["synthetic_sources"] != "[]"


def test_seller_file_never_contains_buyer_identity():
    text = md(synth.SELLER, "seller")
    for i in range(14):
        assert f"SYN-B-{3000 + i}" not in text
    assert "Patil Constructions" not in text


def test_guard_redacts_leaks():
    from yaad import guard, threads
    m = threads.build(synth.SELLER, "seller")
    out, rep = guard.check("call 9876543210 or SYN-B-3001 at a@b.com", m)
    assert "9876543210" not in out and "SYN-B-3001" not in out and "a@b.com" not in out
    assert rep["redactions"] == 3


def test_writeback_creates_promise_and_measures_freshness():
    from yaad.channels.session import Session
    s = Session(synth.BUYER, "buyer", "Voice call")
    s.add("bot", "Namaste ji!")
    s.add("user", "haan GI pipe abhi bhi chahiye, quote WhatsApp pe bhejo")
    # simulate the extractor (no network in tests)
    from yaad import extract
    extract.conversation = lambda turns, role, ch: {"one_line": "Wants GI pipe quote on WhatsApp", "summary": "wants quote",
                                                   "product": "GI Pipe 2 inch", "next_step": "send GI pipe quotes on WhatsApp",
                                                   "language": "Hinglish", "sentiment": "positive", "closed": False}
    import yaad.channels.session as sess
    sess.extract = extract
    res = s.end()
    assert res["event_to_file_ms"] is not None and res["event_to_file_ms"] < 1000
    p = parse.parse(pipeline.read(synth.BUYER, "buyer"))
    assert p["threads"][0]["status"] == "PROMISED"
    assert "Pichli baar Voice call" in p["opening"]  # next channel resumes from the last one
    assert any("500" in k or "GI Pipe" in k for k in p["sections"]["Known – don't ask"].splitlines())
