"""Local web app (127.0.0.1 only): file viewer, web chat channel, call-prep page, simulate activity, freshness.

Run:  .venv/bin/uvicorn globalctx.app:app --host 127.0.0.1 --port 8000
"""
import asyncio
import csv
import json
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from globalctx import config, refresh, sessions, store
from globalctx.resolve import resolve
from globalctx.build.render import parse

WEB = Path(__file__).parent / "web"
app = FastAPI(title="Global Context")
app.mount("/static", StaticFiles(directory=WEB), name="static")


CALL_QUEUE = config.DATA_DIR / "call_queue.json"
POLL_STATUS = {"running": False, "last_ok": None, "last_error": None, "ingested": []}


async def _poll_calls(every_s=15):
    """Pull finished calls (placed from here, by Claude via MCP, or from Sarvam's UI) and write them back."""
    from datetime import datetime, timedelta, timezone
    from globalctx.voice import phone
    seen = set(json.loads(phone.SEEN.read_text())) if phone.SEEN.exists() else set()
    since = datetime.now(timezone.utc) - timedelta(minutes=30)
    POLL_STATUS["running"] = True
    while True:
        try:
            items = await asyncio.to_thread(phone.interactions, since, datetime.now(timezone.utc))
            for it in items:
                iid = it.get("interaction_id")
                if not iid or iid in seen:
                    continue
                ms = await asyncio.to_thread(phone.ingest_interaction, it)
                seen.add(iid)
                phone.SEEN.write_text(json.dumps(sorted(seen)))
                if ms is not None:
                    POLL_STATUS["ingested"] = ([{"interaction": iid, "glid": (it.get("agent_variables") or {}).get("glid"),
                                                  "ms": ms, "at": store.now_iso()}] + POLL_STATUS["ingested"])[:20]
            POLL_STATUS["last_ok"], POLL_STATUS["last_error"] = store.now_iso(), None
        except Exception as e:  # network / API hiccup: keep going
            POLL_STATUS["last_error"] = str(e)[:200]
        await asyncio.sleep(every_s)


@app.on_event("startup")
async def _startup():
    store.init()
    asyncio.create_task(refresh.sweep_forever())
    if config.SARVAM_AGENTS_API_KEY:
        asyncio.create_task(_poll_calls())


def page(name, **subs):
    html = (WEB / name).read_text(encoding="utf-8")
    for k, v in subs.items():
        html = html.replace("{{" + k + "}}", str(v))
    # never serve a stale page from the browser cache (the UI changes often during the hackathon)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, max-age=0"})


def check_role(role):
    if role not in ("seller", "buyer"):
        raise HTTPException(404, "role must be seller or buyer")


# ------------------------------------------------------------------ pages

@app.get("/", response_class=HTMLResponse)
def index():
    """One site: sidebar of every buyer/seller file + tabs (360 view, chat, file, activity)."""
    return page("site.html")


@app.get("/classic", response_class=HTMLResponse)
def classic():
    return page("index.html")


@app.get("/api/glids")
def list_glids():
    """Every GLID that has a file, with what the sidebar needs (name, role, open threads, pending requests)."""
    import re
    demo = {}
    path = config.DATA_DIR / "demo_glids.json"
    if path.exists():
        d = json.loads(path.read_text())
        demo = {g: "demo" for r in ("seller", "buyer") for g in d.get(r, [])}
    cases = {p.stem.split("_")[1] for p in (config.DATA_DIR / "cases").glob("*_*_*.json")} if (config.DATA_DIR / "cases").exists() else set()
    out = []
    with store.connect() as c:
        rows = c.execute("SELECT glid, role, md, generated_at FROM profiles ORDER BY generated_at DESC").fetchall()
    for r in rows:
        md = r["md"]
        ident = (re.search(r"## Identity\n- (.+)", md) or [None, ""])[1]
        name = (re.search(r"Contact: ([^·]+)", ident) or re.search(r"Name: ([^·]+)", ident) or [None, ""])[1].strip()
        biz = (re.search(r"Business: ([^·]+)", ident) or [None, ""])[1].strip()
        if not name or "not known" in name or name == "unknown":
            name = biz if biz and biz != "unknown" else "New user"
        threads = md.split("## Open Threads")[1].split("\n## ")[0] if "## Open Threads" in md else ""
        n_threads = len([l for l in threads.splitlines() if l.startswith("- ") and l.strip() != "- None"])
        pending = 0
        for folder in set(config.REQUEST_FOLDERS.values()):
            f = config.REQUESTS_DIR / folder / f"{r['glid']}.md"
            if f.exists():
                m = re.search(r"^pending: (\d+)", f.read_text(encoding="utf-8"), re.M)
                pending += int(m.group(1)) if m else 0
        out.append({"glid": r["glid"], "role": r["role"], "name": name, "business": biz if biz != name else "",
                    "open_threads": n_threads, "pending_requests": pending, "updated": r["generated_at"],
                    "cold": "data_quality: cold_start" in md, "synthetic": "synthetic: true" in md,
                    "tag": "case" if r["glid"] in cases else demo.get(r["glid"], "")})
    return out


@app.get("/chat/{glid}")
def chat_auto(glid: str):
    """Role is not hard-coded: pick the GLID's buyer or seller file (or create a cold-start one)."""
    role, _, _ = sessions.load(glid)
    return RedirectResponse(f"/chat/{role}/{glid}")


class Contact(BaseModel):
    name: str


@app.post("/api/contact/{glid}")
def set_contact(glid: str, c: Contact):
    """Set the contact person's name for a GLID; the file rebuilds and the next opening uses it."""
    role, _, _ = sessions.load(glid)
    ev, fast = refresh.add_and_refresh(glid, role, "contact", "manual", {"contact_name": c.name.strip()})
    return {"glid": glid, "role": role, "freshness_ms": fast["freshness_ms"]}


class PhoneIn(BaseModel):
    phone: str


def _call_mode():
    import os
    return "dial" if os.environ.get("SARVAM_CONNECTION_ID") and os.environ.get("SARVAM_AGENT_NUMBER") else "queue"


@app.get("/api/phone/{glid}")
def get_phone(glid: str):
    from globalctx import phonemap
    return {"glid": glid, "phone": phonemap.phone_for(glid), "mode": _call_mode(), "poller": POLL_STATUS}


@app.post("/api/phone/{glid}")
def set_phone(glid: str, p: PhoneIn):
    from globalctx import phonemap
    role, _, _ = sessions.load(glid)
    return {"glid": glid, "phone": phonemap.add(p.phone, glid, role)}


@app.post("/api/call/{glid}")
def call_glid(glid: str):
    """Dial with this GLID's file (instant outbound) or, without a telephony connection, queue the call."""
    from globalctx import phonemap
    from globalctx.voice import phone as ph
    number = phonemap.phone_for(glid)
    if not number:
        raise HTTPException(400, "map a phone number to this GLID first")
    role, _, _ = sessions.load(glid)
    if _call_mode() == "dial":
        out = ph.call(glid, number, role)
        return {"status": "dialing", "attempt": out, "phone": phonemap.mask(number)}
    q = json.loads(CALL_QUEUE.read_text()) if CALL_QUEUE.exists() else []
    q = [c for c in q if c["glid"] != glid] + [{"glid": glid, "role": role, "phone": number, "at": store.now_iso()}]
    CALL_QUEUE.write_text(json.dumps(q, indent=2))
    return {"status": "queued", "phone": phonemap.mask(number), "queue": len(q)}


@app.get("/api/call-queue")
def call_queue():
    from globalctx import phonemap
    q = json.loads(CALL_QUEUE.read_text()) if CALL_QUEUE.exists() else []
    return [{**c, "phone": phonemap.mask(c["phone"])} for c in q]


@app.delete("/api/call-queue/{glid}")
def call_done(glid: str):
    q = json.loads(CALL_QUEUE.read_text()) if CALL_QUEUE.exists() else []
    CALL_QUEUE.write_text(json.dumps([c for c in q if c["glid"] != glid], indent=2))
    return {"ok": True}


@app.get("/api/resolve/{glid}")
def resolve_glid(glid: str):
    role, reason = resolve(glid)
    return {"glid": glid, "role": role, "reason": reason}


@app.get("/api/requests")
def list_requests():
    """Request folders: one md per GLID per folder."""
    out = {}
    for folder in sorted(set(config.REQUEST_FOLDERS.values())):
        d = config.REQUESTS_DIR / folder
        out[folder] = sorted(f.stem for f in d.glob("*.md")) if d.exists() else []
    return out


@app.get("/api/requests/{folder}/{glid}", response_class=PlainTextResponse)
def request_file(folder: str, glid: str):
    if folder not in set(config.REQUEST_FOLDERS.values()):
        raise HTTPException(404, "unknown folder")
    path = config.REQUESTS_DIR / folder / f"{glid}.md"
    if not path.exists():
        raise HTTPException(404, "no requests for this GLID")
    return path.read_text(encoding="utf-8")


@app.get("/view/{glid}", response_class=HTMLResponse)
def view_page(glid: str):
    """Customer 360: the GLID's file rendered as a status board (open threads, requirement, requests, timeline)."""
    role, _, _ = sessions.load(glid)
    return page("view.html", role=role, glid=glid)


@app.get("/api/view/{glid}")
def view_data(glid: str):
    role, md, reason = sessions.load(glid)
    data = parse(md)
    check, _ = sessions.identity_check(md)
    reqs = []
    for folder in sorted(set(config.REQUEST_FOLDERS.values())):
        path = config.REQUESTS_DIR / folder / f"{glid}.md"
        if path.exists():
            parsed = parse(path.read_text(encoding="utf-8"))
            for status in ("Pending", "Done"):
                for row in parsed["sections"].get(status, []):
                    if row != "None":
                        reqs.append({"folder": folder, "status": status.lower(), "text": row})
    last = None  # latest measured event -> file time for this GLID (the freshness evidence)
    if config.FRESHNESS_LOG.exists():
        rows = [r for r in csv.DictReader(open(config.FRESHNESS_LOG)) if r["glid"] == glid and r["stage"] == "fast"]
        if rows:
            last = {"ms": rows[-1]["freshness_ms"], "trigger": rows[-1]["trigger_source"], "at": rows[-1]["generated_at"]}
    return {"glid": glid, "role": role, "role_reason": reason, "identity_check": check, "requests": reqs,
            "opening": sessions.opening_for(glid, role), "last_freshness": last, **data}


@app.get("/chat/{role}/{glid}", response_class=HTMLResponse)
def chat_page(role: str, glid: str):
    check_role(role)
    return page("chat.html", role=role, glid=glid)


@app.get("/prep/{role}/{glid}", response_class=HTMLResponse)
def prep_page(role: str, glid: str):
    check_role(role)
    return page("prep.html", role=role, glid=glid)


# ------------------------------------------------------------------ API

@app.get("/api/demo")
def demo():
    path = config.DATA_DIR / "demo_glids.json"
    return json.loads(path.read_text()) if path.exists() else {"seller": [], "buyer": []}


@app.get("/api/profile/{role}/{glid}", response_class=PlainTextResponse)
def profile_md(role: str, glid: str):
    check_role(role)
    return sessions.context_for(glid, role)


@app.get("/api/profile/{role}/{glid}/parsed")
def profile_parsed(role: str, glid: str):
    """What a non-bot consumer sees: sections by heading, no LLM."""
    check_role(role)
    return parse(sessions.context_for(glid, role))


class NewEvent(BaseModel):
    glid: str
    role: str
    source: str
    channel: str | None = None
    payload: dict = {}


PRESETS = {
    "seller": ("enquiry", "marketplace", lambda: {"query_id": f"syn-{uuid.uuid4().hex[:8]}", "product": "HDPE Pipe 2 inch",
                                                    "buyer_city": "Lucknow", "read": False, "message": "Need 500 metre, best rate?"}),
    "buyer": ("buylead", "marketplace", lambda: {"title": "Industrial Water Pump 5 HP", "posted": True}),
}


@app.post("/api/events")
def new_event(ev: NewEvent, bg: BackgroundTasks):
    """Simulate new activity (labelled synthetic). Returns the fast-pass freshness; LLM pass runs after."""
    check_role(ev.role)
    if ev.source == "preset":
        src, ch, mk = PRESETS[ev.role]
        ev.source, ev.channel, ev.payload = src, ch, {**mk(), **ev.payload}
    row = store.add_event(ev.glid, ev.role, ev.source, ev.channel, ev.payload, synthetic=True)
    fast = refresh.on_event(row, background=bg.add_task)
    return {"event_id": row["id"], "freshness_ms": fast["freshness_ms"], "chars": fast["chars"]}


@app.get("/api/freshness")
def freshness(limit: int = 30):
    if not config.FRESHNESS_LOG.exists():
        return []
    rows = list(csv.DictReader(open(config.FRESHNESS_LOG)))
    return rows[-limit:][::-1]


class Start(BaseModel):
    glid: str
    role: str | None = None   # resolved from the GLID's files when omitted
    channel: str = "chat"


@app.post("/api/session/start")
def session_start(s: Start):
    if s.role:
        check_role(s.role)
    sid, opening, md = sessions.start(s.glid, s.role, s.channel)
    live = sessions.live(sid)
    return {"session_id": sid, "opening": opening, "context": md, "role": live["role"], "role_reason": live["reason"]}


class Msg(BaseModel):
    text: str


@app.post("/api/session/{sid}/message")
def session_message(sid: str, m: Msg):
    if not sessions.live(sid):
        raise HTTPException(404, "session not found or ended")
    return {"reply": sessions.chat_reply(sid, m.text)}


@app.post("/api/session/{sid}/end")
def session_end(sid: str):
    summary, fast = sessions.end(sid)
    return {"summary": summary, "freshness_ms": fast and fast["freshness_ms"], "role": fast and fast["role"],
            "request_files": (fast or {}).get("request_files", [])}
