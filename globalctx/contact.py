"""Set the contact person's name for a GLID (the dataset only has company names).

  python -m globalctx.contact --glid 22552651 --name Yashwant
  python -m globalctx.contact --glid 22552651 --show

Stored as an event (source 'contact'), so the file rebuilds the normal way and the next call or chat
greets the person by this name. A name the user says later in a conversation replaces it.
"""
import argparse

from globalctx import refresh, sessions, store

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--glid", required=True)
    ap.add_argument("--name")
    ap.add_argument("--role", choices=["seller", "buyer"], help="optional: resolved from the GLID's files")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    store.init()
    role, md, _ = sessions.load(a.glid, a.role)
    if a.name:
        ev, fast = refresh.add_and_refresh(a.glid, role, "contact", "manual", {"contact_name": a.name.strip()},
                                           llm_pass=False)
        md = refresh.rebuild_now(a.glid, role, use_llm=True)["md"]
        print(f"{role}/{a.glid}.md updated in {fast['freshness_ms']} ms")
    print(md[md.index("## Identity"):md.index("## Snapshot")].strip())
    print(md[md.index("## Suggested Opening"):].strip())
