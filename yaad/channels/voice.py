"""Browser/laptop voice call: hosted Sarvam agent over the conv-ai SDK (interaction_type=CALL), using this
machine's mic and speaker. Transcript turns stream back live; interaction end -> write-back -> rebuild."""
import asyncio
import logging

from .. import config
from .audio import make_audio_interface
from .chat import agent_variables
from .session import Session

log = logging.getLogger("uvicorn.error")


class VoiceCall:
    def __init__(self, glid, role, publish):
        self.s = Session(glid, role, "Voice call")
        self.publish = publish  # fn(dict) -> pushes to the web UI
        self.agent = None
        self.task = None
        self.closed = asyncio.Event()

    async def _on_transcript(self, msg):
        speaker = "user" if str(getattr(msg.role, "value", msg.role)) == "user" else "bot"
        log.info("voice %s transcript %s: %s", self.s.id, speaker, (msg.content or "")[:80])
        self.s.add(speaker, msg.content)
        self.publish({"type": "turn", "session": self.s.id, "speaker": speaker, "text": msg.content})

    async def _on_event(self, ev):
        from sarvam_conv_ai_sdk import ServerInteractionEndEvent
        log.info("voice %s event %s", self.s.id, type(ev).__name__)
        if isinstance(ev, ServerInteractionEndEvent):
            await self.finish()

    async def start(self):
        from pydantic import SecretStr
        from sarvam_conv_ai_sdk import (AsyncDefaultAudioInterface, AsyncSamvaadAgent, InteractionConfig,
                                        InteractionType)
        from sarvam_conv_ai_sdk.messages.types import UserIdentifierType
        if not all([config.SARVAM_ORG_ID, config.SARVAM_WORKSPACE_ID, config.SARVAM_APP_ID, config.SARVAM_API_KEY]):
            raise RuntimeError("Set SARVAM_API_KEY, SARVAM_ORG_ID, SARVAM_WORKSPACE_ID, SARVAM_APP_ID in .env")
        cfg = InteractionConfig(org_id=config.SARVAM_ORG_ID, workspace_id=config.SARVAM_WORKSPACE_ID,
                                app_id=config.SARVAM_APP_ID, user_identifier=self.s.glid,
                                user_identifier_type=UserIdentifierType.CUSTOM, interaction_type=InteractionType.CALL,
                                sample_rate=16000, version=config.SARVAM_APP_VERSION,
                                agent_variables=(av := agent_variables(self.s)),
                                initial_bot_message=av["opening"])  # memory opening is the very first line
        self.agent = AsyncSamvaadAgent(api_key=SecretStr(config.SARVAM_API_KEY), config=cfg,
                                       audio_interface=make_audio_interface(),
                                       transcript_callback=self._on_transcript, event_callback=self._on_event)
        await self.agent.start()
        self.publish({"type": "call", "state": "started", "session": self.s.id})

    async def finish(self):
        if self.closed.is_set():
            return self.s.result
        self.closed.set()
        try:
            await asyncio.wait_for(self.agent.stop(), timeout=5)  # never let a stuck socket keep the mic open
        except Exception:
            pass
        self.publish({"type": "call", "state": "ended", "session": self.s.id})
        log.info("voice %s finishing: %d turns (%d from customer), echo-guard muted %s mic frames", self.s.id,
                 len(self.s.turns), sum(1 for sp, _ in self.s.turns if sp == "user"),
                 getattr(self.agent.audio_interface if self.agent else None, "muted_frames", "n/a"))
        result = await asyncio.to_thread(self.s.end)
        self.publish({"type": "writeback", "session": self.s.id, **(result or {})})
        return result
