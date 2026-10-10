"""WhatsApp-style chat on Sarvam's LLM. Uses only the two APIs' internals: context.build at the start (and before
every reply, so new activity mid-chat is seen) and updater.apply(conversation) at the end."""
import asyncio
import json
import uuid

from .. import config, context, db, llm, prompts, updater

CHATS = {}


def recover(glid, role):
    """Write back chats whose end never came (tab closed, server restarted), from the turns saved as they happened."""
    done = []
    for cid, c in db.unfinished_chats(glid, role).items():
        if cid in CHATS:
            continue  # still live
        if any(s == "user" for s, _ in c["turns"]):
            done.append(updater.apply({"glid": glid, "role": role, "type": "conversation", "synthetic": 1,
                                       "payload": {"turns": c["turns"], "channel": c["channel"], "recovered": True}}))
        db.end_chat(cid)
    return done


class Chat:
    def __init__(self, glid, role, channel="WhatsApp chat"):
        self.id = uuid.uuid4().hex[:8]
        self.glid, self.role, self.channel = glid, role, channel
        self.recovered = recover(glid, role)  # an earlier chat that never reached end() is written back first
        self.ctx = context.build(glid, role)
        self.messages = [{"role": "system", "content": prompts.system_prompt(role, channel, self.ctx["md"])}]
        self.turns = []
        demo_claude = (config.DEMO_CHAT_PROVIDER == "claude" and config.ANTHROPIC_API_KEY
                       and str(glid).startswith("SYN-"))  # real customers always stay on Sarvam
        self.model = config.CLAUDE_CHAT_MODEL if demo_claude else config.CHAT_MODEL
        CHATS[self.id] = self

    def open(self):
        text = self.ctx["opening"]  # straight from memory: no LLM wait
        self.messages.append({"role": "assistant", "content": text})
        self._turn("bot", text)
        return text

    async def say(self, text):
        self._turn("user", text)
        latest = context.build(self.glid, self.role)
        note = ""
        sig = lambda c: json.dumps([c["threads"], c["opening"]], sort_keys=True, default=str)
        if sig(latest) != sig(self.ctx):  # real new activity mid-chat (not just a new timestamp)
            self.ctx = latest
            self.messages[0] = {"role": "system", "content": prompts.system_prompt(self.role, self.channel, latest["md"])}
            note = ("[For the assistant only: the customer's record just got new activity (see the system prompt). "
                    "Mention the new activity itself naturally; never say 'memory', 'record' or 'update'.]\n")
        self.messages.append({"role": "user", "content": note + text})
        reply = ""
        if self.model.startswith("claude"):  # demo-only, synthetic customers only (see config.DEMO_CHAT_PROVIDER)
            try:
                reply = await asyncio.to_thread(llm.claude_chat, self.messages)
            except llm.LLMError:
                self.model = config.CHAT_MODEL  # fall back to Sarvam for the rest of this chat
        if reply.strip():
            self.messages.append({"role": "assistant", "content": reply.strip()})
            self._turn("bot", reply.strip())
            return reply.strip()
        # the API sometimes returns empty content under load: retry with backoff, then the other Sarvam model
        plan = ((config.CHAT_MODEL, 0.3, None), (config.CHAT_MODEL, 0.5, None), (config.CHAT_MODEL, 0.6, 6),
                ("sarvam-105b", 0.4, 6))
        try:
            for i, (model, temp, hist) in enumerate(plan):
                if i:
                    await asyncio.sleep(0.4 * i)
                msgs = self.messages if hist is None else [self.messages[0]] + self.messages[1:][-hist:]
                reply = await asyncio.to_thread(llm.chat, msgs, model, temp, 400, None, "off")
                if reply.strip():
                    break
        except llm.LLMError:
            pass
        reply = reply.strip() or "Ji, ek second, main check karke batati hoon."
        self.messages.append({"role": "assistant", "content": reply})
        self._turn("bot", reply)
        return reply

    def _turn(self, speaker, text):
        self.turns.append((speaker, text))
        db.add_turn(self.id, self.glid, self.role, self.channel, speaker, text)  # saved now, not only at end()

    async def end(self):
        CHATS.pop(self.id, None)
        if not any(s == "user" for s, _ in self.turns):
            db.end_chat(self.id)
            return {"skipped": "no customer messages"}
        res = await asyncio.to_thread(updater.apply, {
            "glid": self.glid, "role": self.role, "type": "conversation", "synthetic": 1,
            "payload": {"turns": self.turns, "channel": self.channel}})
        db.end_chat(self.id)
        return res
