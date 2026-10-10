"""Automated voice test: a synthetic caller (Sarvam TTS) talks to the hosted agent over a real voice session.

  python -m globalctx.voice.sim_caller --role seller --glid 22552651 --line "Haan ji" --line "..."

Flow: push the GLID's file in at session start -> stream caller audio -> record the agent's audio per turn ->
transcribe it with Saaras -> write every turn as an event -> end session -> summary -> file rebuilt.
Used for rehearsal and as evidence; real demos use a teammate on the laptop mic (sdk_session.py).
"""
import argparse
import array
import asyncio
import base64
import io
import time
import wave

from pydantic import SecretStr
from sarvam_conv_ai_sdk import AsyncSamvaadAgent, InteractionConfig, InteractionType, Role
from sarvam_conv_ai_sdk.messages.types import UserIdentifierType

from globalctx import config, llm, refresh, sessions, store

RATE_IN = 16000
FRAME = b"\x00\x00" * (RATE_IN // 10)  # 100 ms of silence


def tts_pcm(text):
    """Caller line -> 16 kHz 16-bit mono PCM."""
    r = llm.client().text_to_speech.convert(text=text, language_code="hi-IN", speaker="rahul",
                                            speech_sample_rate=RATE_IN)
    with wave.open(io.BytesIO(base64.b64decode(r.audios[0]))) as w:
        return w.readframes(w.getnframes())


def stt(pcm, rate):
    if not pcm:
        return ""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    buf.seek(0)
    return llm.client().speech_to_text.transcribe(file=("turn.wav", buf, "audio/wav"),
                                                  model="saaras:v3", language_code="unknown").transcript


def loud(chunk):
    a = array.array("h"); a.frombytes(chunk[: len(chunk) // 2 * 2])
    return max((abs(x) for x in a), default=0) > 500


async def run(glid, role, lines, version=1):
    store.init()
    sid, opening, md = sessions.start_external(glid, role, "voice")
    role = sessions.live(sid)["role"]
    print(f"[context] {role} {glid}: {len(md)} chars pushed in · expected opening: {opening}")
    cfg = InteractionConfig(
        user_identifier_type=UserIdentifierType.CUSTOM, user_identifier=str(glid),
        org_id=config.SARVAM_ORG_ID, workspace_id=config.SARVAM_WORKSPACE_ID, app_id=config.SARVAM_APP_ID,
        version=version, interaction_type=InteractionType.CALL, sample_rate=RATE_IN, initial_bot_message=sessions.identity_check(md)[0] or opening,
        agent_variables={"context": md, "role": role, "glid": str(glid), "opening": opening})
    st = {"buf": [], "done": False, "rate": 8000, "transcripts": []}

    async def on_audio(m):
        # the server streams each spoken turn as 'pending' chunks and closes it with status 'completed'
        st["rate"] = m.sample_rate or 8000
        if m.audio_base64:
            st["buf"].append(base64.b64decode(m.audio_base64))
        if str(getattr(m, "status", "")).lower().endswith("completed"):
            st["done"] = True

    async def on_transcript(m):
        who = "user" if m.role == Role.USER else "bot"
        st["transcripts"].append((who, m.content))

    agent = AsyncSamvaadAgent(api_key=SecretStr(config.SARVAM_AGENTS_API_KEY), config=cfg,
                              audio_callback=on_audio, transcript_callback=on_transcript)

    async def send(chunk):
        for _ in range(30):  # the socket can lag a moment behind wait_for_connect
            try:
                return await agent.send_audio(chunk)
            except Exception:
                await asyncio.sleep(0.2)
        raise RuntimeError("voice session dropped")

    async def bot_turn(max_wait=25):
        """Keep the mic open (silence) until the agent has spoken and gone quiet; return what it said."""
        st["buf"], st["done"] = [], False
        t0 = time.monotonic()
        while time.monotonic() - t0 < max_wait and not st["done"]:
            await send(FRAME)
            await asyncio.sleep(0.1)
        pcm = b"".join(st["buf"])
        return await asyncio.to_thread(stt, pcm, st["rate"]) if loud(pcm) else ""

    await agent.start()
    await agent.wait_for_connect(timeout=15)
    await bot_turn()
    for line in lines:
        pcm = await asyncio.to_thread(tts_pcm, line)
        step = RATE_IN // 10 * 2
        for i in range(0, len(pcm), step):  # real-time stream of the caller's voice
            await send(pcm[i:i + step])
            await asyncio.sleep(0.1)
        await bot_turn()
    await bot_turn(max_wait=8)  # let the last reply finish
    await agent.stop()
    for who, text in sessions.merge_partials(st["transcripts"]):
        sessions.add_turn(sid, who, text)
        print(f"{'Caller' if who == 'user' else 'Mira  '}: {text}")
    summary, fast = sessions.end(sid, glid, role, "voice")
    if summary:
        refresh.rebuild_now(glid, fast["role"], use_llm=True)  # finish the LLM opening before the CLI exits
        for path in fast.get("request_files") or []:
            print(f"[request file updated] {path}")
    print(f"[file updated in {fast['freshness_ms']} ms] summary: {summary}")
    return summary, fast


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["seller", "buyer"], help="optional: resolved from the GLID's files")
    ap.add_argument("--glid", required=True)
    ap.add_argument("--line", action="append", required=True, help="what the caller says, in order")
    ap.add_argument("--version", type=int, default=1)
    a = ap.parse_args()
    asyncio.run(run(a.glid, a.role, a.line, a.version))
