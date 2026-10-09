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


@app.on_event("startup")
async def _startup():
    store.init()
    asyncio.create_task(refresh.sweep_forever())


def page(name, **subs):
    html = (WEB / name).read_text(encoding="utf-8")
    for k, v in subs.items():
        html = html.replace("{{" + k + "}}", str(v))
    return HTMLResponse(html)


def check_role(role):
    if role not in ("seller", "buyer"):
        raise HTTPException(404, "role must be seller or buyer")


# ------------------------------------------------------------------ pages

@app.get("/", response_class=HTMLResponse)
def index():
    return page("index.html")


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
