"""WhatsApp-style chat on Sarvam's LLM. Uses only the two APIs' internals: context.build at the start (and before
every reply, so new activity mid-chat is seen) and updater.apply(conversation) at the end."""
import asyncio
import json
import uuid

from .. import config, context, llm, prompts, updater

CHATS = {}


class Chat:
    def __init__(self, glid, role, channel="WhatsApp chat"):
        self.id = uuid.uuid4().hex[:8]
        self.glid, self.role, self.channel = glid, role, channel
        self.ctx = context.build(glid, role)
        self.messages = [{"role": "system", "content": prompts.system_prompt(role, channel, self.ctx["md"])}]
        self.turns = []
        CHATS[self.id] = self

    def open(self):
        text = self.ctx["opening"]  # straight from memory: no LLM wait
        self.messages.append({"role": "assistant", "content": text})
        self.turns.append(("bot", text))
        return text

    async def say(self, text):
        self.turns.append(("user", text))
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
        self.turns.append(("bot", reply))
        return reply

    async def end(self):
        CHATS.pop(self.id, None)
        if not any(s == "user" for s, _ in self.turns):
            return {"skipped": "no customer messages"}
        return await asyncio.to_thread(updater.apply, {
            "glid": self.glid, "role": self.role, "type": "conversation", "synthetic": 1,
            "payload": {"turns": self.turns, "channel": self.channel}})
