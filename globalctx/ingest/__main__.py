"""python -m globalctx.ingest  -> loads both organiser datasets into data/gc.db"""
import time

from globalctx import store
from globalctx.ingest import buyers, sellers

if __name__ == "__main__":
    store.init()
    t = time.time()
    print("sellers:", sellers.load())
    print("buyers:", buyers.load())
    print(f"done in {time.time() - t:.1f}s")
