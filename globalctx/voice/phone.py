"""Real phone calls through the hosted agent, with results pulled back (no inbound webhooks needed).

  1. Place the call with the GLID's file as variables:
       python -m globalctx.voice.phone payload --role seller --glid 123
     prints the `app_variables` for MCP `place_test_call` (or instant-outbound `agent_variables`).
     With SARVAM_CONNECTION_ID + SARVAM_AGENT_NUMBER set, `call --phone +91...` dials via instant outbound.
  2. Pull results:   python -m globalctx.voice.phone poll   (loops; every finished interaction becomes events)

The agent returns our injected `glid`/`role` variables plus its extracted outputs (call_summary,
requirement, quantity, callback_time, next_step, disposition), so no transcript parsing is needed.
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

from globalctx import config, phonemap, sessions, store
from globalctx.build import narrative
from globalctx.resolve import resolve

BASE = "https://apps.sarvam.ai/api"
SEEN = config.DATA_DIR / "processed_interactions.json"


def headers():
    if not config.SARVAM_AGENTS_API_KEY:
        raise SystemExit("Set SARVAM_AGENTS_API_KEY in .env (Voice Agents API key from indus.sarvam.ai)")
    return {"X-API-Key": config.SARVAM_AGENTS_API_KEY, "Content-Type": "application/json"}


def variables_for(glid, role=None):
    """Agent variables for a call; the role is resolved from the GLID's files unless given."""
    role, md, _ = sessions.load(str(glid), role)
    check, check_en = sessions.identity_check(md)
    return {"context": md, "role": role, "glid": str(glid), "opening": sessions.opening_for(str(glid), role),
            "identity_check": check, "identity_check_en": check_en}


def call(glid, phone, role=None, version=None):
    """Instant outbound (needs a telephony connection on the workspace)."""
    v = variables_for(glid, role)
    body = {
        "app_config": {"app_id": config.SARVAM_APP_ID, "app_version": version or 1, "agent_variables": v,
                       "connection_config": {"connection_id": os.environ["SARVAM_CONNECTION_ID"],
                                             "agent_phone_number": os.environ["SARVAM_AGENT_NUMBER"]},
                       # first words on the call: identity check only, no account details before a "yes"
                       "app_overrides": {"initial_bot_message": v["identity_check"] or v["opening"]}},
        "user_config": {"user_phone_number": phone},
    }
    url = f"{BASE}/outbounds/v1/orgs/{config.SARVAM_ORG_ID}/workspaces/{config.SARVAM_WORKSPACE_ID}/outbounds"
    r = httpx.post(url, headers=headers(), json=body, timeout=30)
    r.raise_for_status()
    return r.json()


def interactions(since, until):
    url = f"{BASE}/analytics/v1/{config.SARVAM_ORG_ID}/{config.SARVAM_WORKSPACE_ID}/{config.SARVAM_APP_ID}/interactions"
    # note: sort_by/sort_order make this endpoint return 500 (checked 9 Oct), so we sort locally
    params = {"start_datetime": since.isoformat(), "end_datetime": until.isoformat(), "limit": 50}
    r = httpx.get(url, headers=headers(), params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    items = data.get("items") or data.get("data") or (data if isinstance(data, list) else [])
    return sorted(items, key=lambda i: i.get("start_datetime") or "")


def transcript_turns(interaction_id):
    """The call's turns from the analytics API: [{'speaker': 'user'|'bot', 'text': ...}]."""
    url = (f"{BASE}/analytics/v1/{config.SARVAM_ORG_ID}/{config.SARVAM_WORKSPACE_ID}/{config.SARVAM_APP_ID}"
           f"/transcripts/{interaction_id}")
    r = httpx.get(url, headers=headers(), timeout=30)
    r.raise_for_status()
    return [{"speaker": "user" if m.get("role") == "user" else "bot", "text": m.get("content", "")}
            for m in r.json().get("messages") or [] if m.get("content")]


def ingest_interaction(it):
    """One finished call -> summary (+ requests) event -> rebuild. Returns freshness in ms, or None."""
    v = it.get("agent_variables") or {}
    glid, role = v.get("glid"), v.get("role")
    cold = False
    if not glid:  # no injected context: the demo phone map, matched on Sarvam's hashed caller ID
        m = phonemap.lookup(it.get("user_identifier") or "") or {}
        glid, role = m.get("glid"), m.get("role")
        cold = True
    if not glid:
        return None  # not one of ours
    if role not in ("buyer", "seller"):
        role = resolve(glid)[0]
    channel = "phone" if it.get("channel_direction") == "outbound" else "voice"
    if (v.get("disposition") == "no_conversation" or str(it.get("end_reason", "")).upper() == "VOICEMAIL"
            or (it.get("duration_in_seconds") or 0) < 20):
        # nobody really spoke: record the attempt, never let the LLM summarise it into invented content
        summary = {"summary": "Call not answered (voicemail or no conversation)", "disposition": "no_conversation",
                   "open_threads": [], "requests": [], "source_system": "sarvam_agent"}
        role, fast, _ = sessions.record(glid, role, channel, it["interaction_id"], summary, cold=cold)
        return fast["freshness_ms"]
    try:
        turns = transcript_turns(it["interaction_id"])
    except httpx.HTTPError:
        turns = []
    summary = narrative.summarise_session(role, turns) if turns else {}
    # the agent's own post-call variables fill any gaps
    summary = {**{"summary": v.get("call_summary") or "call with Mira", "requirement": v.get("requirement") or None,
                  "quantity": v.get("quantity") or None, "callback": v.get("callback_time") or None,
                  "next_step": v.get("next_step") or None, "open_threads": [], "requests": []},
               **{k: val for k, val in summary.items() if val not in (None, "", [])},
               "disposition": v.get("disposition") or None, "source_system": "sarvam_agent"}
    role, fast, paths = sessions.record(glid, role, channel, it["interaction_id"], summary, cold=cold)
    for path in paths:
        print(f"    request file updated: {path}")
    return fast["freshness_ms"]


def poll(every_s=15, lookback_min=60):
    store.init()
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    since = datetime.now(timezone.utc) - timedelta(minutes=lookback_min)
    print(f"polling Sarvam analytics every {every_s}s for agent {config.SARVAM_APP_ID}")
    while True:
        now = datetime.now(timezone.utc)
        try:
            for it in interactions(since, now):
                iid = it.get("interaction_id")
                if not iid or iid in seen:
                    continue
                ms = ingest_interaction(it)
                seen.add(iid)
                SEEN.write_text(json.dumps(sorted(seen)))
                if ms is not None:
                    v = it.get("agent_variables") or {}
                    print(f"{now:%H:%M:%S} interaction {iid} -> GLID {v.get('glid') or '(phone map)'} "
                          f"file updated ({ms} ms after ingest)", flush=True)
        except httpx.HTTPError as e:
            print("poll error:", e)
        time.sleep(every_s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("payload"); p.add_argument("--role"); p.add_argument("--glid", required=True)
    c = sub.add_parser("call"); c.add_argument("--role"); c.add_argument("--glid", required=True)
    c.add_argument("--phone", help="default: the number mapped to this GLID in data/phone_map.json")
    sub.add_parser("poll")
    a = ap.parse_args()
    store.init()
    if a.cmd == "payload":
        print(json.dumps(variables_for(a.glid, a.role), ensure_ascii=False, indent=2))
    elif a.cmd == "call":
        phone = a.phone or phonemap.phone_for(a.glid)
        if not phone:
            raise SystemExit("no phone given and none mapped: python -m globalctx.phonemap add <phone> --glid ...")
        print(call(a.glid, phone, a.role))
    else:
        poll()
