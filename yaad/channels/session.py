"""A conversation on any channel. On end(): extract -> event -> rebuild, so the next channel resumes."""
import time
import uuid
from datetime import datetime

from .. import extract, pipeline, store

SESSIONS = {}


class Session:
    def __init__(self, glid, role, channel):
        self.id = uuid.uuid4().hex[:10]
        self.glid, self.role, self.channel = str(glid), role, channel
        self.context = pipeline.read(self.glid, role)  # memory as it was when the session started
        self.turns = []  # [(user|bot, text)]
        self.started = time.time()
        self.ended = None
        self.result = None
        SESSIONS[self.id] = self

    def add(self, speaker, text):
        if text and text.strip():
            self.turns.append((speaker, text.strip()))

    def end(self):
        """Write the conversation back as an event. The add_event hook rebuilds the file inline."""
        if self.ended:
            return self.result
        self.ended = time.time()
        if not any(s == "user" for s, _ in self.turns):
            self.result = {"skipped": "no customer turns"}
            return self.result
        t_extract = time.time()
        x = extract.conversation(self.turns, self.role, self.channel)
        extract_ms = (time.time() - t_extract) * 1000
        payload = {"summary": x.get("summary") or x.get("one_line"), "language": x.get("language"),
                   "turns": len(self.turns), "session_id": self.id, "transcript": self.turns[-40:]}
        eid = store.add_event(self.glid, self.role, "yaad", self.channel, datetime.now(), payload,
                              thread_key=(x.get("product") or "").lower()[:60] or None, extracted=x)
        last = store.conn().execute("SELECT freshness_ms, tokens FROM renders WHERE trigger_event_id=? ORDER BY id DESC LIMIT 1",
                                    (eid,)).fetchone()
        self.result = {"event_id": eid, "extracted": x, "extract_ms": round(extract_ms),
                       "event_to_file_ms": round(last["freshness_ms"]) if last else None,
                       "conversation_end_to_file_ms": round((time.time() - self.ended) * 1000),
                       "tokens": last["tokens"] if last else None}
        return self.result


def ingest_transcript(glid, role, channel, turns, platform_vars=None, ended_at=None):
    """Write back a conversation that happened outside this process (e.g. a Sarvam phone call read from
    analytics). turns: [(user|bot, text)]. platform_vars: Sarvam's post-call output variables, kept for
    cross-checking our own extraction."""
    s = Session.__new__(Session)
    s.id = uuid.uuid4().hex[:10]
    s.glid, s.role, s.channel = str(glid), role, channel
    s.context, s.turns, s.started, s.result = None, list(turns), time.time(), None
    s.ended = None
    res = s.end()
    if platform_vars and res.get("event_id"):
        import json
        ev = store.event(res["event_id"])
        x = json.loads(ev["extracted"] or "{}")
        pv = {k: v for k, v in platform_vars.items() if k in ("call_summary", "next_step", "callback_time", "product", "outcome") and v}
        x["sarvam_post_call"] = pv
        if str(x.get("extracted_by", "")).startswith("fallback"):  # our extractor failed: use Sarvam's post-call fields
            x.update({"one_line": pv.get("call_summary", x.get("one_line")), "summary": pv.get("call_summary"),
                      "product": pv.get("product"), "next_step": pv.get("next_step"),
                      "callback_date": pv.get("callback_time"), "closed": pv.get("outcome") == "resolved",
                      "extracted_by": "sarvam post-call variables"})
            x["qty"] = x.get("qty") or extract._qty(" ".join(t for sp, t in turns if sp == "user"))
        else:  # two extractors: fill whatever ours missed from Sarvam's post-call variables
            if not x.get("next_step") and pv.get("next_step") and pv.get("outcome") != "resolved":
                x["next_step"] = pv["next_step"]
            x["product"] = x.get("product") or pv.get("product")
            x["callback_date"] = x.get("callback_date") or pv.get("callback_time")
            if pv.get("outcome") in ("next_step_agreed", "callback_requested"):
                x["closed"] = False  # Sarvam says something is still pending
        store.set_extracted(res["event_id"], x)
        pipeline.rebuild(s.glid, role)
        res["extracted"] = x
    return res
