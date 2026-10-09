"""CLI: python -m yaad <command>

  ingest              load organiser CSVs into data/yaad.db
  synth               load labelled synthetic personas (+ cold-start GLID)
  pick                suggest demo GLIDs (prints ids + statuses only, never content)
  build GLID [role]   rebuild one file (role: buyer|seller, default both that exist)
  extract GLID...     LLM backfill for free-text events of these GLIDs, then rebuild
  serve               start the local web app on http://127.0.0.1:8000
  stats               freshness + size stats from the render log
  check [--fast]      automatic demo check (real files: structure only; synthetic scenarios in a scratch DB)
"""
import sys

from . import pipeline, store


def main(argv):
    if not argv:
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]
    if cmd == "ingest":
        from . import ingest
        ingest.run()
    elif cmd == "synth":
        from . import synth
        for g in synth.load():
            for role in ("seller", "buyer"):
                if role in store.roles_for(g) or g == synth.COLD:
                    print(pipeline.rebuild(g, role))
    elif cmd == "pick":
        from . import pick
        pick.run()
    elif cmd == "build":
        roles = [args[1]] if len(args) > 1 else (store.roles_for(args[0]) or ["seller"])
        for r in roles:
            print(pipeline.rebuild(args[0], r))
    elif cmd == "extract":
        from . import extract
        for g in args:
            n = extract.backfill(g)
            print(f"{g}: {n} events extracted")
            for r in store.roles_for(g):
                print(pipeline.rebuild(g, r))
    elif cmd == "serve":
        import uvicorn
        # open live-update (SSE) streams would otherwise block shutdown forever and leave old servers running
        uvicorn.run("yaad.web.app:app", host="127.0.0.1", port=8000, timeout_graceful_shutdown=2)
    elif cmd == "check":
        from . import check
        sys.exit(0 if check.run(fast="--fast" in args) else 1)
    elif cmd == "stats":
        from . import stats
        stats.run()
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
