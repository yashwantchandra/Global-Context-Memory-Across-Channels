"""Chat channel. Preferred: the SAME hosted Sarvam agent in CHAT mode (SDK). Fallback: sarvam-105b-conversations
directly with the same rules and memory. Either way the opening comes from memory with zero LLM latency."""
import asyncio

from .. import config, llm, pipeline, prompts, threads
from .session import Session


def _opening(ctx):
    return ctx.split("## Suggested opening", 1)[-1].strip().lstrip(">").strip()


class LocalChat:
    kind = "local-llm"

    def __init__(self, glid, role, channel="WhatsApp chat"):
        self.s = Session(glid, role, channel)
        self.messages = [{"role": "system", "content": prompts.system_prompt(role, channel, self.s.context)}]

    async def open(self):
        text = _opening(self.s.context)
        self.messages.append({"role": "assistant", "content": text})
        self.s.add("bot", text)
        return text

    async def say(self, text):
        self.s.add("user", text)
        latest = pipeline.read(self.s.glid, self.s.role)
        note = ""
        if latest != self.s.context:  # memory changed mid-conversation (new activity on another channel)
            self.s.context = latest
            self.messages[0] = {"role": "system", "content": prompts.system_prompt(self.s.role, self.s.channel, latest)}
            note = "[Note for assistant: the MEMORY was just updated with new activity; mention it briefly.]\n"
        self.messages.append({"role": "user", "content": note + text})
        try:
            reply = ""
            # the API intermittently returns empty content under load: retry with a short backoff, then shorter history
            plan = ((config.CHAT_MODEL, 0.3, None), (config.CHAT_MODEL, 0.5, None), (config.CHAT_MODEL, 0.6, 6),
                    ("sarvam-105b", 0.4, 6))  # last resort: the other Sarvam model
            for i, (model, temp, hist) in enumerate(plan):
                if i:
                    await asyncio.sleep(0.4 * i)
                msgs = self.messages if hist is None else [self.messages[0]] + self.messages[1:][-hist:]
                reply = await asyncio.to_thread(llm.chat, msgs, model, temp, 400, None, "off")
                if reply.strip():
                    break
            reply = reply.strip() or "Ji, ek second, main check karke batati hoon."
        except llm.LLMError as e:
            reply = f"(LLM unavailable: {e})"
        self.messages.append({"role": "assistant", "content": reply})
        self.s.add("bot", reply)
        return reply

    async def close(self):
        return await asyncio.to_thread(self.s.end)


class SdkChat:
    """Hosted agent over the conv-ai SDK, interaction_type=CHAT."""
    kind = "sarvam-agent"

    def __init__(self, glid, role, channel="WhatsApp chat"):
        self.s = Session(glid, role, channel)
        self.agent = None
        self._buf, self._done = [], asyncio.Event()

    async def _on_text(self, msg):
        from sarvam_conv_ai_sdk import ServerTextMsg
        if isinstance(msg, ServerTextMsg):
            self._buf = [msg.text]
            self._done.set()
        else:
            self._buf.append(getattr(msg, "text", "") or "")

    async def open(self):
        from pydantic import SecretStr
        from sarvam_conv_ai_sdk import AsyncSamvaadAgent, InteractionConfig, InteractionType
        from sarvam_conv_ai_sdk.messages.types import UserIdentifierType
        cfg = InteractionConfig(org_id=config.SARVAM_ORG_ID, workspace_id=config.SARVAM_WORKSPACE_ID,
                                app_id=config.SARVAM_APP_ID, user_identifier=self.s.glid,
                                user_identifier_type=UserIdentifierType.CUSTOM, interaction_type=InteractionType.CHAT,
                                sample_rate=16000, version=config.SARVAM_APP_VERSION,
                                agent_variables=agent_variables(self.s))
        self.agent = AsyncSamvaadAgent(api_key=SecretStr(config.SARVAM_API_KEY), config=cfg, text_callback=self._on_text)
        await self.agent.start()
        await self.agent.wait_for_connect()
        return await self._wait_reply(first=True)

    async def _wait_reply(self, first=False):
        try:
            await asyncio.wait_for(self._done.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
        text = "".join(self._buf).strip() or ("(no greeting)" if first else "(no reply)")
        self._buf, self._done = [], asyncio.Event()
        self.s.add("bot", text)
        return text

    async def say(self, text):
        self.s.add("user", text)
        await self.agent.send_text(text)
        return await self._wait_reply()

    async def close(self):
        if self.agent:
            try:
                await self.agent.stop()
            except Exception:
                pass
        return await asyncio.to_thread(self.s.end)


def agent_variables(s):
    m = threads.build(s.glid, s.role)
    return {"context": s.context, "role": s.role, "glid": s.glid, "channel": s.channel,
            "language": m.language, "opening": _opening(s.context)}


def new_chat(glid, role):
    # The hosted agent is voice-only (v2v) in this workspace, so the runtime refuses CHAT sessions.
    # Chat uses the same rules + memory on sarvam-105b-conversations; set YAAD_CHAT=sdk to try the hosted agent.
    import os
    return SdkChat(glid, role) if os.environ.get("YAAD_CHAT") == "sdk" else LocalChat(glid, role)
