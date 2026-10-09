"""Channel sessions (web chat, voice call): load context at start, write turns, summarise at end.

The role is never fixed per channel: `resolve` picks the GLID's buyer or seller file at session start
(or creates a cold-start one). The summary event at the end is what lets the next channel resume
without re-asking; requests raised in the conversation go to the request folders.
"""
import json
import re
import uuid

from globalctx import config, llm, prompts, refresh, store
from globalctx.build import narrative
from globalctx.build import requests as reqs
from globalctx.resolve import resolve

_live = {}  # session_id -> {glid, role, channel, messages, opening, cold}


def _opening_from(md):
    m = re.search(r'## Suggested Opening\n"(.+?)"', md, re.S)
    return m.group(1).strip() if m else narrative.GENERIC["seller"]


def identity_check(md):
    """First line of a call: confirm we are speaking to the right person before sharing any context.
    Returns (hinglish_line, english_line) or (None, None) for a cold start / unknown name."""
    m = re.search(r"## Identity\n- (.+)", md)
    if not m or "data_quality: cold_start" in md:
        return None, None
    row = m.group(1)
    name = re.search(r"(?:Contact|Name): ([^·]+)", row)
    name = name.group(1).strip() if name and "not known" not in name.group(1) else None
    biz = re.search(r"Business: ([^·]+)", row)
    biz = biz.group(1).strip() if biz and biz.group(1).strip() != "unknown" else None
    if name and biz:
        return (f"Namaste! Kya meri baat {name} ji se ho rahi hai, {biz} se?",
                f"Namaste, am I speaking with {name} ji from {biz}?")
    if name or biz:
        who = name or biz
        return f"Namaste! Kya meri baat {who} ji se ho rahi hai?", f"Namaste, am I speaking with {who} ji?"
    return None, None


def context_for(glid, role=None):
    """Latest file for a GLID (role resolved if not given), built on demand. Returns md."""
    return load(glid, role)[1]


def load(glid, role=None):
    """Returns (role, md, reason). Builds the file on demand, including a cold-start file for a new GLID."""
    glid = str(glid)
    reason = "given"
    if role is None:
        role, reason = resolve(glid)
    prof = store.get_profile(glid, role)
    if not prof:
        refresh.rebuild_now(glid, role, use_llm=True)
        prof = store.get_profile(glid, role)
    return role, prof["md"], reason


def _register(glid, role, channel, with_llm_messages):
    role, md, reason = load(glid, role)
    sid = uuid.uuid4().hex[:12]
    opening = _opening_from(md)
    _live[sid] = {"glid": str(glid), "role": role, "channel": channel, "context": md, "opening": opening,
                  "cold": "data_quality: cold_start" in md, "reason": reason,
                  "messages": [{"role": "system", "content": prompts.system_prompt(role, glid, channel, md)},
                               {"role": "assistant", "content": opening}] if with_llm_messages else []}
    return sid, role, opening, md


def start(glid, role=None, channel="chat"):
    """Local web chat. Returns (sid, opening, md); the chosen role is in live(sid)['role']."""
    sid, role, opening, md = _register(glid, role, channel, True)
    add_turn(sid, "bot", opening)
    return sid, opening, md


def start_external(glid, role=None, channel="voice"):
    """A session run by the hosted Sarvam agent (voice via SDK). Returns (sid, opening, md)."""
    sid, role, opening, md = _register(glid, role, channel, False)
    return sid, opening, md


def add_turn(sid, speaker, text):
    s = _live[sid]
    store.add_event(s["glid"], s["role"], "session", s["channel"],
                    {"kind": "turn", "session_id": sid, "speaker": speaker, "text": text}, synthetic=config.ROLEPLAY)


def merge_partials(turns):
    """Streaming STT sends growing partials ('haan ji', 'haan ji bataiye', ...): keep the last of each run."""
    def norm(x):
        return re.sub(r"[^\w\s]", "", x).strip()

    out = []
    for who, text in turns:
        text = (text or "").strip()
        if not text:
            continue
        a, b = norm(out[-1][1]) if out else "", norm(text)
        if out and out[-1][0] == who and (b.startswith(a[:10]) or a.startswith(b[:10])):
            out[-1] = (who, text if len(text) >= len(out[-1][1]) else out[-1][1])
        else:
            out.append((who, text))
    return out


def chat_reply(sid, user_text):
    s = _live[sid]
    add_turn(sid, "user", user_text)
    s["messages"].append({"role": "user", "content": user_text})
    reply = llm.chat(s["messages"], model=config.CHAT_MODEL, temperature=0.3, max_tokens=300)
    s["messages"].append({"role": "assistant", "content": reply})
    add_turn(sid, "bot", reply)
    return reply


def turns(sid):
    with store.connect() as c:
        rows = c.execute("SELECT payload FROM events WHERE source='session' "
                         "AND json_extract(payload,'$.session_id')=? AND json_extract(payload,'$.kind')='turn' "
                         "ORDER BY id", (sid,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def _move_cold_start(glid, old_role, new_role):
    """A brand-new GLID turned out to be the other role: move its session events and drop the empty file."""
    with store.connect() as c:
        c.execute("UPDATE events SET role=? WHERE glid=? AND role=?", (new_role, str(glid), old_role))
        c.execute("DELETE FROM profiles WHERE glid=? AND role=?", (str(glid), old_role))
    old = config.PROFILES_DIR / old_role / f"{glid}.md"
    if old.exists():
        old.unlink()


def record(glid, role, channel, sid, summary, synthetic=None, cold=False):
    """Write a session summary (+ requests) and rebuild. Shared by chat, SDK voice and phone polling.
    Returns (role, fast-rebuild result, request file paths)."""
    synthetic = config.ROLEPLAY if synthetic is None else synthetic
    said_role = summary.get("user_role")
    if cold and said_role in ("buyer", "seller") and said_role != role:
        _move_cold_start(glid, role, said_role)
        role = said_role
    paths = reqs.record(glid, role, channel, sid, summary.get("requests") or [], synthetic)
    payload = {"kind": "summary", "session_id": sid, **{k: v for k, v in summary.items() if k != "requests"},
               "requests": len(summary.get("requests") or [])}
    ev, fast = refresh.add_and_refresh(glid, role, "session", channel, payload, synthetic=synthetic)
    return role, fast, paths


def end(sid, glid=None, role=None, channel=None):
    """Summarise the session, write summary + requests and rebuild the file. Returns (summary, rebuild)."""
    s = _live.pop(sid, None) or {"glid": str(glid), "role": role or resolve(glid)[0], "channel": channel,
                                 "cold": False}
    t = turns(sid)
    if not any(x["speaker"] == "user" for x in t):
        return None, None  # nothing said: don't pollute history
    summary = narrative.summarise_session(s["role"], t)
    role, fast, paths = record(s["glid"], s["role"], s["channel"], sid, summary, cold=s.get("cold", False))
    fast = {**fast, "role": role, "request_files": paths}
    return summary, fast


def live(sid):
    return _live.get(sid)
