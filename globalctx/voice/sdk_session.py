"""Talk to the hosted Sarvam agent with the GLID's file pushed in at session start.

  Voice (laptop mic + speaker, needs PyAudio):  python -m globalctx.voice.sdk_session --role seller --glid 123
  Text chat through the same agent:            python -m globalctx.voice.sdk_session --role seller --glid 123 --chat

Every transcript turn is written to the event store; when the interaction ends the
session is summarised and the file is rebuilt (freshness is printed).
"""
import argparse
import asyncio
import signal
import sys

from pydantic import SecretStr
from sarvam_conv_ai_sdk import AsyncSamvaadAgent, InteractionConfig, InteractionType, Role
from sarvam_conv_ai_sdk.messages.types import UserIdentifierType

from globalctx import config, refresh, sessions, store


async def run(glid, role, chat=False, version=None, max_seconds=300):
    store.init()
    channel = "chat" if chat else "voice"
    sid, opening, md = sessions.start_external(glid, role, channel)
    role = sessions.live(sid)["role"]
    print(f"[context] {len(md)} chars loaded for {role} {glid} · session {sid}")
    cfg = InteractionConfig(
        user_identifier_type=UserIdentifierType.CUSTOM, user_identifier=str(glid),
        org_id=config.SARVAM_ORG_ID, workspace_id=config.SARVAM_WORKSPACE_ID, app_id=config.SARVAM_APP_ID,
        version=version or 1, interaction_type=InteractionType.CHAT if chat else InteractionType.CALL,
        sample_rate=16000, initial_bot_message=sessions.identity_check(md)[0] or opening,
        agent_variables={"context": md, "role": role, "glid": str(glid), "opening": opening},
    )
    done = asyncio.Event()

    heard = []

    async def on_transcript(msg):
        who = "user" if msg.role == Role.USER else "bot"
        print(f"{'You' if who == 'user' else 'Mira'}: {msg.content}", flush=True)
        heard.append((who, msg.content))

    async def on_text(msg):  # chat mode: bot replies arrive as text messages
        text = getattr(msg, "text", None) or getattr(msg, "content", None)
        if text and chat:
            print(f"Mira: {text}")
            sessions.add_turn(sid, "bot", text)

    async def on_event(ev):
        if "end" in str(getattr(ev, "type", "")).lower():
            done.set()

    audio = None
    if not chat:
        from sarvam_conv_ai_sdk import AsyncDefaultAudioInterface
        audio = AsyncDefaultAudioInterface(input_sample_rate=16000)
    if not config.SARVAM_AGENTS_API_KEY:
        sys.exit("Set SARVAM_AGENTS_API_KEY in .env (Voice Agents API key from indus.sarvam.ai, not the dashboard key)")
    agent = AsyncSamvaadAgent(api_key=SecretStr(config.SARVAM_AGENTS_API_KEY), config=cfg, audio_interface=audio,
                              transcript_callback=None if chat else on_transcript,
                              text_callback=on_text if chat else None, event_callback=on_event)
    try:
        await agent.start()
        await agent.wait_for_connect(timeout=15.0)
        print(f"[connected] interaction {agent.get_interaction_id()}  (speak now; Ctrl+C or /end to finish)", flush=True)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):  # stopping from outside still saves the call
            loop.add_signal_handler(sig, done.set)
        loop.call_later(max_seconds, done.set)
        if chat:
            loop = asyncio.get_running_loop()
            while not done.is_set():
                line = (await loop.run_in_executor(None, sys.stdin.readline)).strip()
                if not line or line == "/end":
                    break
                sessions.add_turn(sid, "user", line)
                await agent.send_text(line)
        else:
            await done.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await agent.stop()
        for who, text in sessions.merge_partials(heard):  # streaming partials -> one turn each
            sessions.add_turn(sid, who, text)
        summary, fast = sessions.end(sid, glid, role, channel)
        if summary:
            refresh.rebuild_now(glid, fast["role"], use_llm=True)  # finish the LLM opening before the CLI exits
            for path in fast.get("request_files") or []:
                print(f"[request file updated] {path}")
        if summary:
            print(f"[file updated in {fast['freshness_ms']} ms] {summary.get('summary')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["seller", "buyer"], help="optional: resolved from the GLID's files")
    ap.add_argument("--glid", required=True)
    ap.add_argument("--chat", action="store_true", help="text chat through the hosted agent instead of voice")
    ap.add_argument("--version", type=int, default=None, help="agent version (default 1: the draft, until a version is committed)")
    ap.add_argument("--max-seconds", type=int, default=300, help="end the call after this long")
    a = ap.parse_args()
    asyncio.run(run(a.glid, a.role, a.chat, a.version, a.max_seconds))
