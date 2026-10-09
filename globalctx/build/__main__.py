"""python -m globalctx.build --role seller --glid 123 [--no-llm]   or   --demo"""
import argparse
import json

from globalctx import config, store
from globalctx.build.builder import build

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=["seller", "buyer"])
    ap.add_argument("--glid", nargs="*")
    ap.add_argument("--demo", action="store_true", help="build the demo GLIDs in data/demo_glids.json")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    store.init()
    targets = []
    if a.demo:
        demo = json.loads((config.DATA_DIR / "demo_glids.json").read_text())
        targets = [(g, r) for r in ("seller", "buyer") for g in demo.get(r, [])]
    else:
        targets = [(g, a.role) for g in a.glid or []]
    for g, r in targets:
        out = build(g, r, use_llm=not a.no_llm)
        print(f"{r:6} {g:>12}  {out['chars']:5} chars  opening={out['opening_by']:8}  evicted={out['evicted']}")
