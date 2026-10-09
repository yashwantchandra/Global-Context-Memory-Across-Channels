"""Phone calls to a teammate's phone, and automatic write-back of finished phone calls.

Placing a call:
  * direct  : Sarvam instant outbound (public API). Needs a phone connection on the workspace
              (SARVAM_CONNECTION_ID + SARVAM_AGENT_NUMBER, from the organisers). Memory goes per call.
  * queued  : no connection yet. The request is queued; Claude places it with the Voice Agents MCP test call
              (which ignores per-call variables, so the GLID's memory is set as the agent default first).
              Synthetic GLIDs only on this path, so real customer data never passes through a third party.
Write-back (both paths): a background poller reads the analytics API, takes every finished OUTBOUND call of our
agent since the app started, finds its GLID/role in the call's agent variables, fetches the transcript and
writes it back as an event, so the file rebuilds and the next channel resumes.
"""
import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from . import config, pipeline, store, threads

OUTBOUND = "https://apps.sarvam.ai/api/outbounds/v1/orgs/{org}/workspaces/{ws}/outbounds"
ANALYTICS = "https://apps.sarvam.ai/api/analytics/v1/{org}/{ws}/{app}"
CONNECTION_ID = os.environ.get("SARVAM_CONNECTION_ID", "")
AGENT_NUMBER = os.environ.get("SARVAM_AGENT_NUMBER", "")
TEST_NUMBERS = [n for n in os.environ.get("SARVAM_TEST_NUMBERS", "").split(",") if n]  # verified numbers, from .env
QUEUE = config.ROOT / "data" / "call_queue.json"
STARTED_UTC = datetime.now(timezone.utc) - timedelta(minutes=int(os.environ.get("YAAD_PHONE_LOOKBACK_MIN", "30")))

store.conn().executescript("""CREATE TABLE IF NOT EXISTS phone_calls (
  interaction_id TEXT PRIMARY KEY, attempt_id TEXT, glid TEXT, role TEXT, event_id INTEGER,
  duration REAL, ended_by TEXT, saved_at REAL)""")


def _vars(glid, role):
    md = pipeline.read(glid, role)
    m = threads.build(glid, role)
    opening = md.split("## Suggested opening")[-1].strip().lstrip("> ").strip()
    return {"context": md, "role": role, "channel": "Phone call", "glid": str(glid), "opening": opening,
            "language": m.language}


def _queue():
    try:
        return json.loads(QUEUE.read_text())
    except Exception:
        return []


def _save_queue(q):
    QUEUE.parent.mkdir(exist_ok=True)
    QUEUE.write_text(json.dumps(q, indent=1, ensure_ascii=False))


def mode():
    return "direct" if (CONNECTION_ID and AGENT_NUMBER) else "queued"


def request_call(glid, role, number):
    number = number.strip().replace(" ", "")
    if not number.startswith("+"):
        number = "+91" + number.lstrip("0")
    if mode() == "direct":
        av = _vars(glid, role)
        body = {"app_config": {"app_id": config.SARVAM_APP_ID, "app_version": config.SARVAM_APP_VERSION or 1,
                               "connection_config": {"connection_id": CONNECTION_ID, "agent_phone_number": AGENT_NUMBER},
                               "agent_variables": av, "app_overrides": {"initial_bot_message": av["opening"]}},
                "user_config": {"user_phone_number": number}}
        r = httpx.post(OUTBOUND.format(org=config.SARVAM_ORG_ID, ws=config.SARVAM_WORKSPACE_ID), json=body,
                       headers={"X-API-Key": config.SARVAM_API_KEY}, timeout=30)
        r.raise_for_status()
        return {"mode": "direct", "attempt_id": r.json().get("attempt_id"), "number": number}
    if not str(glid).startswith("SYN-"):
        return {"mode": "blocked", "reason": "Phone calls for real customers need the organisers' phone connection "
                "(instant outbound). Until then use a synthetic customer, or the laptop call."}
    q = _queue()
    item = {"id": uuid.uuid4().hex[:8], "glid": str(glid), "role": role, "number": number,
            "requested_at": datetime.now().isoformat(timespec="seconds"), "status": "queued"}
    q.append(item)
    _save_queue(q)
    return {"mode": "queued", **item}


def queue():
    return [i for i in _queue() if i["status"] == "queued"]


def mark_placed(qid, attempt_id=""):
    q = _queue()
    for i in q:
        if i["id"] == qid:
            i.update(status="placed", attempt_id=attempt_id, placed_at=datetime.now().isoformat(timespec="seconds"))
    _save_queue(q)


def recent_calls(limit=10):
    rows = store.conn().execute("SELECT * FROM phone_calls ORDER BY saved_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- automatic write-back
async def poll_once(publish=lambda m: None):
    """Write back every finished outbound call of our agent that is not saved yet. Returns how many were saved."""
    from .channels.session import ingest_transcript
    base = ANALYTICS.format(org=config.SARVAM_ORG_ID, ws=config.SARVAM_WORKSPACE_ID, app=config.SARVAM_APP_ID)
    h = {"X-API-Key": config.SARVAM_API_KEY}
    now = datetime.now(timezone.utc)
    params = {"start_datetime": STARTED_UTC.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end_datetime": (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 50}
    saved = 0
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(base + "/attempts", params=params, headers=h)
        if r.status_code != 200:
            return 0
        for a in r.json().get("items", []):
            iid = a.get("interaction_id")
            av = a.get("agent_variables") or {}
            if (not iid or a.get("channel_direction") != "outbound" or not a.get("ended_by")
                    or not av.get("glid") or av.get("role") not in ("buyer", "seller")):
                continue
            if store.conn().execute("SELECT 1 FROM phone_calls WHERE interaction_id=?", (iid,)).fetchone():
                continue
            t = await c.get(f"{base}/transcripts/{iid}", headers=h)
            msgs = t.json().get("messages", []) if t.status_code == 200 else []
            turns = [("bot" if m.get("role") == "assistant" else "user", m.get("content") or "") for m in msgs]
            eid = None
            if any(s == "user" for s, _ in turns):
                res = await asyncio.to_thread(ingest_transcript, av["glid"], av["role"], "Phone call", turns, av)
                eid = res.get("event_id")
            store.conn().execute("INSERT OR REPLACE INTO phone_calls VALUES (?,?,?,?,?,?,?,?)",
                                 (iid, a.get("attempt_id"), av["glid"], av["role"], eid,
                                  a.get("duration_in_seconds"), a.get("ended_by"), time.time()))
            store.conn().commit()
            saved += 1
            publish({"type": "phone_saved", "glid": av["glid"], "role": av["role"], "event_id": eid,
                     "duration": a.get("duration_in_seconds"), "ended_by": a.get("ended_by"),
                     "turns": len(turns)})
    return saved


async def poller(publish, every=12):
    while True:
        try:
            await poll_once(publish)
        except Exception:
            pass
        await asyncio.sleep(every)
