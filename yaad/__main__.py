"""CLI: python -m yaad <command>

  serve               start the service + demo UI on http://127.0.0.1:8000
  reset amit|rakesh   reset a demo persona to the start of its journey
  context GLID [role] print the md a bot would receive
"""
import sys


def main(argv):
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "serve":
        import uvicorn
        # open SSE streams would otherwise block shutdown and leave old servers running
        uvicorn.run("yaad.api:app", host="127.0.0.1", port=8000, timeout_graceful_shutdown=2)
    elif cmd == "reset" and args:
        from . import story
        story.reset(args[0])
        print(f"reset {args[0]}")
    elif cmd == "context" and args:
        from . import context
        print(context.build(args[0], args[1] if len(args) > 1 else "buyer")["md"])
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
