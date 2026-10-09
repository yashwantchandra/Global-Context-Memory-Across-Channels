"""Local web app: memory viewer, chat, voice call, exec card, due-today queue, freshness. Localhost only."""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .. import brief, config, parse, pipeline, stats, store, synth, threads
from ..channels.chat import new_chat
from ..channels.voice import VoiceCall

app = FastAPI(title="Yaad")
STATIC = Path(__file__).parent / "static"
DEMO_FILE = config.ROOT / "data" / "demo.json"

# ---------------------------------------------------------------- live updates (SSE)
_subs: list[asyncio.Queue] = []
_loop = None


def publish(msg: dict):
    msg = {**msg, "at": time.time()}
    for q in list(_subs):
        if _loop:
            _loop.call_soon_threadsafe(q.put_nowait, msg)


pipeline.on_rebuild(lambda info: publish({"type": "rebuild", **info}))


@app.on_event("startup")
async def _startup():
    global _loop
    _loop = asyncio.get_running_loop()
    from .. import phone
    asyncio.create_task(phone.poller(publish))  # finished phone calls -> transcript -> memory, automatically


@app.get("/api/stream")
async def stream():
    q: asyncio.Queue = asyncio.Queue()
    _subs.append(q)

    async def gen():
        try:
            yield "data: {\"type\":\"hello\"}\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(msg, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _subs.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------- memory
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/glids")
def glids():
    demo = json.loads(DEMO_FILE.read_text()) if DEMO_FILE.exists() else {"seller": [], "buyer": []}
    out = [{"glid": synth.SELLER, "role": "seller", "label": "Shree Ganesh Steel Traders", "sub": "Rajkot · Gujarati · callback due"},
           {"glid": synth.BUYER, "role": "buyer", "label": "Patil Constructions", "sub": "Pune · GI pipe · unanswered"},
           {"glid": synth.COLD, "role": "buyer", "label": "New customer", "sub": "cold start · no history"}]
    for role in ("seller", "buyer"):
        for g in demo.get(role, []):
            m = threads.build(g, role)
            top = m.threads[0].status.replace("_", " ").lower() if m.threads else "no open thread"
            out.append({"glid": g, "role": role, "label": f"{role.title()} {g}", "sub": top})
    return out


@app.get("/api/brief/{glid}/{role}")
def role_brief(glid: str, role: str):
    """What a teammate needs to play this customer on a test call. Local only, never sent anywhere."""
    return brief.build(glid, role)


@app.get("/api/memory/{glid}/{role}")
def memory(glid: str, role: str):
    md = pipeline.read(glid, role)
    last = store.conn().execute("SELECT * FROM renders WHERE glid=? AND role=? ORDER BY id DESC LIMIT 1",
                                (glid, role)).fetchone()
    return {"md": md, "parsed": parse.parse(md), "render": dict(last) if last else None}


@app.post("/api/rebuild/{glid}/{role}")
def rebuild(glid: str, role: str):
    return pipeline.rebuild(glid, role)


class NewEvent(BaseModel):
    glid: str
    role: str
    kind: str = "enquiry"  # enquiry | callback | photo | requirement
    # typed by the presenter in the UI (all optional; blanks fall back to the customer's own top product)
    product: str = ""
    city: str = ""
    qty: str = ""
    message: str = ""


@app.post("/api/event")
def inject(ev: NewEvent):
    """Inject a labelled synthetic activity (as if a source API pushed it) to demo event-driven freshness."""
    from ..inject import inject as _inject
    return _inject(ev)


# ---------------------------------------------------------------- chat
_chats = {}


class Start(BaseModel):
    glid: str
    role: str


class Say(BaseModel):
    text: str


@app.post("/api/chat/start")
async def chat_start(b: Start):
    c = new_chat(b.glid, b.role)
    opening = await c.open()
    _chats[c.s.id] = c
    return {"chat_id": c.s.id, "kind": c.kind, "opening": opening}


@app.post("/api/chat/{cid}/say")
async def chat_say(cid: str, b: Say):
    c = _chats.get(cid) or _missing()
    t0 = time.time()
    reply = await c.say(b.text)
    return {"reply": reply, "latency_ms": round((time.time() - t0) * 1000)}


@app.post("/api/chat/{cid}/end")
async def chat_end(cid: str):
    c = _chats.pop(cid, None) or _missing()
    res = await c.close()
    publish({"type": "writeback", "session": cid, **(res or {})})
    return res


def _missing():
    raise HTTPException(404, "unknown session")


# ---------------------------------------------------------------- voice
_calls = {}


@app.post("/api/voice/start")
async def voice_start(b: Start):
    await _stop_all_calls()  # only one call may hold the mic
    call = VoiceCall(b.glid, b.role, publish)
    try:
        await call.start()
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    _calls[call.s.id] = call
    return {"call_id": call.s.id}


async def _stop_all_calls():
    results = []
    for cid in list(_calls):
        results.append(await _calls.pop(cid).finish())
    return results


@app.post("/api/voice/stop_all")
async def voice_stop_all():
    return {"stopped": len(await _stop_all_calls())}


@app.post("/api/voice/{cid}/end")
async def voice_end(cid: str):
    call = _calls.pop(cid, None)
    if not call:  # unknown id: stop whatever is running rather than leave a mic open
        res = await _stop_all_calls()
        return res[-1] if res else {"skipped": "no call running"}
    res = await call.finish()
    await _stop_all_calls()
    return res


# ---------------------------------------------------------------- reuse outside the bot
@app.get("/api/card/{glid}")
def card(glid: str):
    """Executive call-prep card: parsed from seller.md by headings, no LLM."""
    p = parse.parse(pipeline.read(glid, "seller"))
    return {"glid": glid, "meta": p["meta"], "who": p["sections"].get("Who", ""), "threads": p["threads"],
            "guardrails": p["guardrails"], "say_first": p["opening"]}


_queue_cache = {"at": 0, "rows": []}


@app.get("/api/queue")
def queue(limit: int = 25):
    """Due-today queue across all sellers, ranked by their top open thread (rules only)."""
    if time.time() - _queue_cache["at"] > 120:
        rows = []
        for (g,) in store.conn().execute("SELECT glid FROM profiles"):
            m = threads.seller_memory(g)
            if m.threads:
                t = m.threads[0]
                rows.append({"glid": g, "top": t.status, "title": t.title, "next": t.next_step,
                             "score": round(t.score, 1), "threads": [x.status for x in m.threads],
                             "dnd": any(x.startswith("DND") for x in m.guardrails)})
        rows.sort(key=lambda r: r["score"], reverse=True)
        _queue_cache.update(at=time.time(), rows=rows)
    rows = _queue_cache["rows"]
    counts = {}
    for r in rows:
        counts[r["top"]] = counts.get(r["top"], 0) + 1
    return {"total": len(rows), "by_top_status": counts, "rows": [r for r in rows if not r["dnd"]][:limit]}


@app.get("/api/freshness")
def freshness():
    rows = store.conn().execute("SELECT glid, role, freshness_ms, tokens, written_at, trigger_event_id FROM renders "
                                "WHERE freshness_ms IS NOT NULL ORDER BY id DESC LIMIT 30").fetchall()
    return {"summary": stats.summary(), "recent": [dict(r) for r in rows]}


# ---------------------------------------------------------------- phone calls
class PhoneCall(BaseModel):
    glid: str
    role: str
    number: str


@app.get("/api/phone/info")
def phone_info():
    from .. import phone
    return {"mode": phone.mode(), "numbers": phone.TEST_NUMBERS, "queue": phone.queue(), "recent": phone.recent_calls()}


@app.post("/api/phone/call")
def phone_call(b: PhoneCall):
    from .. import phone
    try:
        res = phone.request_call(b.glid, b.role, b.number)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")
    publish({"type": "phone_request", **res})
    return res


@app.post("/api/phone/placed/{qid}")
def phone_placed(qid: str, attempt_id: str = ""):
    from .. import phone
    phone.mark_placed(qid, attempt_id)
    publish({"type": "phone_placed", "id": qid})
    return {"ok": True}


@app.post("/api/phone/poll")
async def phone_poll():
    from .. import phone
    return {"saved": await phone.poll_once(publish)}


@app.get("/api/checks")
def checks():
    p = config.ROOT / "samples" / "check_results.json"
    if not p.exists():
        return {"passed": None, "total": None}
    d = json.loads(p.read_text())
    return {"passed": d["passed"], "total": d["total"]}
