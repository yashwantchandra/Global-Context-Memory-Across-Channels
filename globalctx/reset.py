"""Reset a GLID's file to its source data: undo what our channels added, then rebuild.

  python -m globalctx.reset --glid 910000101 [--glid SYN-B-2001 ...]

Removes the conversations (session summaries and turns) and the requests raised on our channels,
deletes the GLID's request-folder files, and rebuilds every role's file. Source events (enquiries,
calls, WhatsApp, case files) and names or languages set by the team are kept, so the file goes back
to how it looked before any role-play. Useful for replaying a demo.
"""
import argparse

from globalctx import config, refresh, store

CHANNEL_SOURCES = ("session", "request")


def reset(glid):
    glid = str(glid)
    with store.connect() as c:
        roles = {r[0] for r in c.execute("SELECT DISTINCT role FROM events WHERE glid=?", (glid,))}
        roles |= {r[0] for r in c.execute("SELECT role FROM profiles WHERE glid=?", (glid,))}
        removed = c.execute("DELETE FROM events WHERE glid=? AND source IN (?, ?)", (glid, *CHANNEL_SOURCES)).rowcount
    for folder in set(config.REQUEST_FOLDERS.values()):
        path = config.REQUESTS_DIR / folder / f"{glid}.md"
        if path.exists():
            path.unlink()
    rebuilt = {role: refresh.rebuild_now(glid, role, use_llm=False)["chars"] for role in sorted(roles)}
    return {"glid": glid, "removed_events": removed, "rebuilt": rebuilt}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--glid", action="append", required=True)
    for g in ap.parse_args().glid:
        print(reset(g))
