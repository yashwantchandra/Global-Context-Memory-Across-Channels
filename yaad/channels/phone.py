"""Phone calls through the hosted Sarvam agent, and automatic write-back of finished calls.

Placing a call:
  * direct : Sarvam instant outbound (public API, memory passed per call). Needs a phone connection on the workspace
             (SARVAM_CONNECTION_ID + SARVAM_AGENT_NUMBER from the organisers).
  * queued : no connection yet. The request is queued and placed with the Voice Agents MCP test call (which ignores
             per-call variables, so the GLID's memory is set as the agent default first). Synthetic customers only.
Write-back: a poller reads Sarvam's analytics API, takes every finished outbound call of our agent, finds its GLID/role
in the call's agent variables, and posts the transcript as a `conversation` event, exactly like any other channel.
"""
import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from .. import config, context, db, updater

OUTBOUND = "https://apps.sarvam.ai/api/outbounds/v1/orgs/{org}/workspaces/{ws}/outbounds"
ANALYTICS = "https://apps.sarvam.ai/api/analytics/v1/{org}/{ws}/{app}"
CONNECTION_ID = os.environ.get("SARVAM_CONNECTION_ID", "")
AGENT_NUMBER = os.environ.get("SARVAM_AGENT_NUMBER", "")
TEST_NUMBERS = [n for n in os.environ.get("SARVAM_TEST_NUMBERS", "").split(",") if n]  # verified numbers, from .env
QUEUE = config.ROOT / "data" / "call_queue.json"
STARTED_UTC = datetime.now(timezone.utc) - timedelta(minutes=int(os.environ.get("YAAD_PHONE_LOOKBACK_MIN", "30")))


def _table():
    db.conn().execute("""CREATE TABLE IF NOT EXISTS phone_calls (interaction_id TEXT PRIMARY KEY, attempt_id TEXT,
        glid TEXT, role TEXT, event_id INTEGER, duration REAL, ended_by TEXT, saved_at REAL)""")


def agent_variables(glid, role):
    c = context.build(glid, role)
    return {"context": c["md"], "role": role, "channel": "Phone call", "glid": glid, "opening": c["opening"],
            "language": c["snapshot"].get("language", "Hinglish")}


def _queue():
    try:
        return json.loads(QUEUE.read_text())
    except Exception:
        return []


def mode():
    return "direct" if (CONNECTION_ID and AGENT_NUMBER) else "queued"


def request_call(glid, role, number):
    number = number.strip().replace(" ", "")
    if not number.startswith("+"):
        number = "+91" + number.lstrip("0")
    if mode() == "direct":
        av = agent_variables(glid, role)
        body = {"app_config": {"app_id": config.SARVAM_APP_ID, "app_version": config.SARVAM_APP_VERSION or 1,
                               "connection_config": {"connection_id": CONNECTION_ID, "agent_phone_number": AGENT_NUMBER},
                               "agent_variables": av, "app_overrides": {"initial_bot_message": av["opening"]}},
                "user_config": {"user_phone_number": number}}
        r = httpx.post(OUTBOUND.format(org=config.SARVAM_ORG_ID, ws=config.SARVAM_WORKSPACE_ID), json=body,
                       headers={"X-API-Key": config.SARVAM_API_KEY}, timeout=30)
        r.raise_for_status()
        return {"mode": "direct", "attempt_id": r.json().get("attempt_id"), "number": number}
    if not str(glid).startswith("SYN-"):
        return {"mode": "blocked", "reason": "Calls to real customers need the organisers' phone connection."}
    q = _queue()
    item = {"id": uuid.uuid4().hex[:8], "glid": glid, "role": role, "number": number,
            "requested_at": datetime.now().isoformat(timespec="seconds"), "status": "queued"}
    q.append(item)
    QUEUE.parent.mkdir(exist_ok=True)
    QUEUE.write_text(json.dumps(q, indent=1))
    return {"mode": "queued", **item}


def queue():
    return [i for i in _queue() if i["status"] == "queued"]


def mark_placed(qid, attempt_id=""):
    q = _queue()
    for i in q:
        if i["id"] == qid:
            i.update(status="placed", attempt_id=attempt_id)
    QUEUE.write_text(json.dumps(q, indent=1))


def recent_calls(limit=10):
    _table()
    return [dict(r) for r in db.conn().execute("SELECT * FROM phone_calls ORDER BY saved_at DESC LIMIT ?", (limit,))]


async def poll_once(publish=lambda m: None):
    """Write back every finished outbound call not saved yet. Returns how many calls were processed."""
    _table()
    base = ANALYTICS.format(org=config.SARVAM_ORG_ID, ws=config.SARVAM_WORKSPACE_ID, app=config.SARVAM_APP_ID)
    h = {"X-API-Key": config.SARVAM_API_KEY}
    params = {"start_datetime": STARTED_UTC.strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end_datetime": (datetime.now(timezone.utc) + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "limit": 50}
    done = 0
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(base + "/attempts", params=params, headers=h)
        if r.status_code != 200:
            return 0
        for a in r.json().get("items", []):
            iid, av = a.get("interaction_id"), a.get("agent_variables") or {}
            if (not iid or a.get("channel_direction") != "outbound" or not a.get("ended_by")
                    or not av.get("glid") or av.get("role") not in ("buyer", "seller")):
                continue
            if db.conn().execute("SELECT 1 FROM phone_calls WHERE interaction_id=?", (iid,)).fetchone():
                continue
            t = await c.get(f"{base}/transcripts/{iid}", headers=h)
            msgs = t.json().get("messages", []) if t.status_code == 200 else []
            turns = [("bot" if m.get("role") == "assistant" else "user", m.get("content") or "") for m in msgs]
            res = {}
            if any(s == "user" for s, _ in turns):
                res = await asyncio.to_thread(updater.apply, {
                    "glid": av["glid"], "role": av["role"], "type": "conversation",
                    "synthetic": int(str(av["glid"]).startswith("SYN-")),
                    "payload": {"turns": turns, "channel": "Phone call", "platform_vars": av}})
            db.conn().execute("INSERT OR REPLACE INTO phone_calls VALUES (?,?,?,?,?,?,?,?)",
                              (iid, a.get("attempt_id"), av["glid"], av["role"], res.get("event_id"),
                               a.get("duration_in_seconds"), a.get("ended_by"), time.time()))
            db.conn().commit()
            done += 1
            publish({"type": "phone_saved", "glid": av["glid"], "role": av["role"], "result": res,
                     "duration": a.get("duration_in_seconds"), "ended_by": a.get("ended_by"), "turns": turns})
    return done


async def poller(publish, every=12):
    while True:
        try:
            await poll_once(publish)
        except Exception:
            pass
        await asyncio.sleep(every)
