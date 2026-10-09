"""python -m globalctx.pick_demo -> data/demo_glids.json (5 rich sellers, 5 rich buyers, 1 cold-start each)."""
import json

from globalctx import config, store

COLD = {"seller": "900000001", "buyer": "900000002"}  # ids that exist in no dataset


def pick():
    with store.connect() as c:
        sellers = c.execute("""
            SELECT glid, COUNT(DISTINCT source) AS n_src, MAX(ts) AS last_ts FROM events
            WHERE role='seller' AND source!='profile' AND ts >= '2026-08-15'
            GROUP BY glid
            HAVING SUM(source='vani_call')>0 AND SUM(source='enquiry')>0 AND SUM(source='whatsapp_bot')>0
            ORDER BY n_src DESC, last_ts DESC LIMIT 5""").fetchall()
        buyers = c.execute("""
            SELECT glid, COUNT(*) AS n, COUNT(DISTINCT source) AS n_src FROM events
            WHERE role='buyer' AND source!='profile' GROUP BY glid
            HAVING SUM(source='buyer_activity')>5
            ORDER BY n_src DESC, n DESC LIMIT 5""").fetchall()
    return {"seller": [r["glid"] for r in sellers] + [COLD["seller"]],
            "buyer": [r["glid"] for r in buyers] + [COLD["buyer"]],
            "cold_start": COLD}


if __name__ == "__main__":
    demo = pick()
    (config.DATA_DIR / "demo_glids.json").write_text(json.dumps(demo, indent=2))
    print(json.dumps(demo, indent=2))
