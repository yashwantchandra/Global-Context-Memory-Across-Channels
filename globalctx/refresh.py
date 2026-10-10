"""Event-driven refresh: a new event rebuilds only its GLID's file.

One pass per event: facts + rule-based opening, milliseconds (this is the freshness number).
No LLM runs on events. (With GC_OPENING=llm, a second background pass rewrites the opening.)
A periodic sweep rebuilds any GLID whose events are newer than its file (backstop).
"""
import asyncio
import threading

from globalctx import config, store
from globalctx.build.builder import build

_lock = threading.Lock()  # one rebuild at a time per process keeps SQLite writes simple


def rebuild_now(glid, role, trigger=None, use_llm=False):
    with _lock:
        return build(glid, role, trigger=trigger, use_llm=use_llm)


def on_event(event, llm_pass=True, background=None):
    """Call after inserting an event. Returns the fast-pass result; schedules the LLM pass."""
    fast = rebuild_now(event["glid"], event["role"], trigger=event, use_llm=False)
    if llm_pass and config.OPENING_MODE == "llm" and not config.NO_LLM and fast["opening_by"] == "template":
        job = lambda: rebuild_now(event["glid"], event["role"], trigger=event, use_llm=True)  # noqa: E731
        if background is not None:
            background(job)
        else:
            threading.Thread(target=job, daemon=True).start()
    return fast


def add_and_refresh(glid, role, source, channel, payload, synthetic=False, ts=None, llm_pass=True):
    ev = store.add_event(glid, role, source, channel, payload, ts=ts, synthetic=synthetic)
    return ev, on_event(ev, llm_pass=llm_pass)


def stale_glids():
    """GLIDs with events ingested after their file was generated."""
    with store.connect() as c:
        return [(r[0], r[1]) for r in c.execute("""
            SELECT e.glid, e.role FROM events e JOIN profiles p ON p.glid=e.glid AND p.role=e.role
            GROUP BY e.glid, e.role HAVING MAX(e.ingested_at) > MAX(p.generated_at)""")]


async def sweep_forever(every_s=300):
    while True:
        await asyncio.sleep(every_s)
        for glid, role in await asyncio.to_thread(stale_glids):
            await asyncio.to_thread(rebuild_now, glid, role, None, True)
