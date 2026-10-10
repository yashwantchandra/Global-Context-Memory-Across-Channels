"""Meera service. The product is two calls:

    POST /v1/event              any IndiaMART system or conversation reports activity
    GET  /v1/context/{glid}     any bot reads the customer's memory before it speaks

Everything else here serves the demo (story steps, chat, phone, live updates). Local only: 127.0.0.1.
"""
import asyncio
import json
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel

from . import config, context, db, story, updater
from .channels import phone
from .channels.chat import CHATS, Chat

app = FastAPI(title="Meera · memory across channels")
WEB = Path(__file__).parent / "web"

_subs: list = []
_loop = None


def publish(msg):
    msg = {**msg, "at": time.time()}
    for q in list(_subs):
        if _loop:
            _loop.call_soon_threadsafe(q.put_nowait, msg)


@app.on_event("startup")
async def _startup():
    global _loop
    _loop = asyncio.get_running_loop()
    asyncio.create_task(phone.poller(publish))  # finished phone calls -> conversation events, automatically


# ================================================================ the two APIs
class Event(BaseModel):
    glid: str
    role: str                                # buyer | seller
    type: str                                # search | bl_posted | bl_matched | enquiry | pns | conversation | complaint …
    mcat_id: Optional[str] = None
    product: Optional[str] = None            # free text, used when there is no mcat
    counterparty: Optional[str] = None       # the other party's GLID (never shown to the other side)
    ts: Optional[str] = None
    payload: dict = {}
    synthetic: int = 0


@app.post("/v1/event")
async def post_event(e: Event):
    res = await asyncio.to_thread(updater.apply, e.model_dump())
    publish({"type": "event", "glid": e.glid, "role": e.role, "result": res})
    for m in res.get("mirrored", []):
        publish({"type": "event", "glid": m.get("glid"), "result": m})
    return res


@app.get("/v1/context/{glid}")
def get_context(glid: str, role: Optional[str] = None, format: str = "json"):
    if not role:
        u = db.any_user(glid)
        role = (u or {}).get("role") or "buyer"
    t0 = time.time()
    c = context.build(glid, role)
    c["build_ms"] = round((time.time() - t0) * 1000, 1)
    return PlainTextResponse(c["md"], media_type="text/markdown") if format == "md" else c


# ================================================================ inspection (the tables, shown in the UI)
@app.get("/v1/tables/{glid}")
def tables(glid: str, role: Optional[str] = None):
    evs = db.events(glid, role)
    ths = [t for r in ([role] if role else ["buyer", "seller"]) for t in db.threads(glid, r)]
    return {"events": evs[-30:], "threads": ths}


@app.get("/v1/stats")
def stats():
    return {"freshness": db.freshness_stats(),
            "events": db.conn().execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "threads": db.conn().execute("SELECT COUNT(*) FROM threads").fetchone()[0]}


# ================================================================ demo: story, chat, phone, live stream
@app.get("/")
def root():
    return RedirectResponse("/demo")


@app.get("/demo")
def demo():
    return FileResponse(WEB / "demo.html")


@app.get("/demo/story/{persona}")
def get_story(persona: str):
    if persona not in story.PERSONAS:
        raise HTTPException(404)
    s = story.load(persona)
    for i, st in enumerate(s["steps"]):
        if st.get("event"):
            st["body"] = story.event_body(persona, i)  # the exact POST /v1/event body the UI will send
    return s


@app.post("/demo/reset/{persona}")
async def reset(persona: str):
    if persona not in story.PERSONAS:
        raise HTTPException(404)
    await asyncio.to_thread(story.reset, persona)
    publish({"type": "reset", "persona": persona})
    return {"ok": True}


class Who(BaseModel):
    glid: str
    role: str


class Say(BaseModel):
    text: str


@app.post("/v1/chat/start")
def chat_start(b: Who):
    c = Chat(b.glid, b.role)
    return {"chat_id": c.id, "opening": c.open(), "context_tokens": c.ctx["tokens"]}


@app.post("/v1/chat/{cid}/say")
async def chat_say(cid: str, b: Say):
    c = CHATS.get(cid) or _404()
    t0 = time.time()
    reply = await c.say(b.text)
    return {"reply": reply, "ms": round((time.time() - t0) * 1000)}


@app.post("/v1/chat/{cid}/end")
async def chat_end(cid: str):
    c = CHATS.get(cid) or _404()
    res = await c.end()
    publish({"type": "event", "glid": c.glid, "role": c.role, "result": res})
    return res


class Replay(BaseModel):
    glid: str
    role: str
    channel: str
    turns: list
    platform_vars: dict = {}


@app.post("/demo/replay")
async def replay(b: Replay):
    """Safety net for live demos: replay a recorded conversation as a real conversation event."""
    res = await asyncio.to_thread(updater.apply, {
        "glid": b.glid, "role": b.role, "type": "conversation", "synthetic": 1,
        "payload": {"turns": b.turns, "channel": b.channel, "platform_vars": b.platform_vars}})
    publish({"type": "event", "glid": b.glid, "role": b.role, "result": res})
    return res


class PhoneReq(BaseModel):
    glid: str
    role: str
    number: str


@app.get("/v1/phone/info")
def phone_info():
    return {"mode": phone.mode(), "numbers": phone.TEST_NUMBERS, "queue": phone.queue(), "recent": phone.recent_calls()}


@app.post("/v1/phone/call")
def phone_call(b: PhoneReq):
    res = phone.request_call(b.glid, b.role, b.number)
    publish({"type": "phone_request", **res})
    return res


@app.post("/v1/phone/placed/{qid}")
def phone_placed(qid: str, attempt_id: str = ""):
    phone.mark_placed(qid, attempt_id)
    publish({"type": "phone_placed", "id": qid})
    return {"ok": True}


@app.get("/v1/stream")
async def stream():
    q: asyncio.Queue = asyncio.Queue()
    _subs.append(q)

    async def gen():
        try:
            yield 'data: {"type":"hello"}\n\n'
            while True:
                try:
                    yield f"data: {json.dumps(await asyncio.wait_for(q.get(), 15), default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _subs.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _404():
    raise HTTPException(404, "unknown session")


# ================================================================ internet voice call (browser mic ⇄ Sarvam over WSS)
VENDOR = WEB / "vendor"


@app.get("/vendor/{path:path}")
def vendor(path: str):
    """Serve the vendored Sarvam Web SDK. Its build uses extensionless and folder imports ('./types'), so redirect
    to the real file: browsers then resolve each module's own relative imports from the redirected URL."""
    base = (VENDOR / path).resolve()
    if not str(base).startswith(str(VENDOR.resolve())):
        raise HTTPException(404)
    if base.is_file():
        return FileResponse(base, media_type="text/javascript" if base.suffix == ".js" else None)
    if base.with_name(base.name + ".js").is_file():
        return RedirectResponse(f"/vendor/{path}.js", status_code=307)
    if (base / "index.js").is_file():
        return RedirectResponse(f"/vendor/{path.rstrip('/')}/index.js", status_code=307)
    raise HTTPException(404)


@app.get("/v1/voice/session")
def voice_session(glid: str, role: str):
    """Everything the browser needs to start a call, except the API key: IDs, pinned version, and the memory."""
    av = phone.agent_variables(glid, role)
    av["channel"] = "Web call"
    return {"org_id": config.SARVAM_ORG_ID, "workspace_id": config.SARVAM_WORKSPACE_ID, "app_id": config.SARVAM_APP_ID,
            "version": config.SARVAM_APP_VERSION, "user_identifier": glid, "agent_variables": av,
            "opening": av["opening"]}


@app.get("/v1/sarvam/orgs/{org}/workspaces/{ws}/apps/{app_id}/url")
async def sarvam_signed_url(org: str, ws: str, app_id: str, interaction_type: str = "call",
                            version: Optional[int] = None):
    """Proxy for the Web SDK's signed-URL request: adds our key server-side, only for our own agent. The browser then
    connects to Sarvam's single-use signed WSS URL directly; the key never reaches it."""
    if (org, ws, app_id) != (config.SARVAM_ORG_ID, config.SARVAM_WORKSPACE_ID, config.SARVAM_APP_ID):
        raise HTTPException(403, "not our agent")
    params = {"interaction_type": interaction_type}
    if version:
        params["version"] = version
    url = f"https://apps.sarvam.ai/api/app-runtime/orgs/{org}/workspaces/{ws}/apps/{app_id}/url"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, params=params, headers={"X-API-Key": config.SARVAM_API_KEY})
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text[:300])
    return r.json()


class VoiceEnd(BaseModel):
    glid: str
    role: str
    turns: list
    interaction_id: Optional[str] = None


@app.post("/v1/voice/end")
async def voice_end(b: VoiceEnd):
    """The call's live transcript (collected in the browser) becomes a conversation event, instantly."""
    turns = [[("user" if str(s).lower() == "user" else "bot"), t] for s, t in b.turns if str(t or "").strip()]
    if not any(s == "user" for s, _ in turns):
        return {"skipped": "the customer did not speak"}
    res = await asyncio.to_thread(updater.apply, {
        "glid": b.glid, "role": b.role, "type": "conversation", "synthetic": int(b.glid.startswith("SYN-")),
        "payload": {"turns": turns, "channel": "Web call", "interaction_id": b.interaction_id}})
    publish({"type": "event", "glid": b.glid, "role": b.role, "result": res})
    return res
