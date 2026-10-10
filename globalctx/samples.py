"""python -m globalctx.samples -> samples/ with redacted copies of the demo files and the freshness log.

The real files stay in data/ (gitignored). Samples replace GLIDs, people's names and company
names with placeholders so the submission folder carries no customer data.
"""
import csv
import json
import re

from globalctx import config, store
from globalctx.build.render import parse

OUT = config.ROOT / "samples"


def _seller_names(texts):
    """Company names that WhatsApp templates mention: 'You just spoke with X', 'X (City) responded', 'X tried to reach you'."""
    pats = [r"spoke with (.+?)(?: 📞| Was|\n|$)",
            r"(?:^|, )([A-Z][\w&.'() -]{2,60}?) \([A-Za-z .]+\)(?: \(also deals in [^)]*\))? responded",
            r"^Hi! (.+?) tried to reach you"]
    names = []
    for t in texts:
        for pat in pats:
            m = re.search(pat, t)
            if m:
                names.append(m.group(1).strip(" *"))
    return names


def names_for(glid, role):
    """Every person/company name the file could mention for this GLID."""
    names = set()
    for e in store.events_for(glid, role):
        p = e["payload"]
        for k in ("company_name", "first_name", "seller_company", "buyer_company"):
            if p.get(k):
                names.add(str(p[k]).strip())
        if role == "buyer" and e["source"] == "whatsapp_bot":
            names.update(_seller_names([p.get("text", "")]))
    return {n for n in names if len(n) > 2}


def redact(text, glid_map, names):
    for real, alias in glid_map.items():
        text = text.replace(real, alias)
    for i, n in enumerate(sorted(names, key=len, reverse=True)):
        text = re.sub(re.escape(n), f"[Name {i + 1}]", text, flags=re.I)
    return text


def main():
    demo = json.loads((config.DATA_DIR / "demo_glids.json").read_text())
    glid_map, n = {}, {"seller": 0, "buyer": 0}
    for role in ("seller", "buyer"):
        for g in demo[role]:
            n[role] += 1
            glid_map[g] = f"{role.upper()}_{n[role]}" + ("_COLD" if g == demo["cold_start"][role] else "")
    for role in ("seller", "buyer"):
        (OUT / role).mkdir(parents=True, exist_ok=True)
        for g in demo[role]:
            prof = store.get_profile(g, role)
            if not prof:
                continue
            names = names_for(g, role)
            md = redact(prof["md"], glid_map, names)
            (OUT / role / f"{glid_map[g]}.md").write_text(md, encoding="utf-8")
            (OUT / role / f"{glid_map[g]}.parsed.json").write_text(
                json.dumps(parse(md), indent=2, ensure_ascii=False), encoding="utf-8")
    # request folders: one md per GLID per folder (demo GLIDs and any role-play GLIDs created today)
    for folder_dir in sorted(config.REQUESTS_DIR.glob("*")) if config.REQUESTS_DIR.exists() else []:
        for f in sorted(folder_dir.glob("*.md")):
            g = f.stem
            if g not in glid_map:
                glid_map[g] = f"NEW_{len([k for k in glid_map if glid_map[k].startswith('NEW_')]) + 1}"
            names = names_for(g, "seller") | names_for(g, "buyer")
            out = OUT / "requests" / folder_dir.name / f"{glid_map[g]}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(redact(f.read_text(encoding="utf-8"), glid_map, names), encoding="utf-8")
    if config.FRESHNESS_LOG.exists():
        rows = list(csv.DictReader(open(config.FRESHNESS_LOG)))
        with open(OUT / "freshness_log.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=rows[0].keys())
            w.writeheader()
            for r in rows:
                r["glid"] = glid_map.get(r["glid"], "OTHER")
                w.writerow(r)
        ms = sorted(int(r["freshness_ms"]) for r in rows if r["stage"] == "fast" and r["freshness_ms"])
        llm = sorted(int(r["freshness_ms"]) for r in rows if r["stage"] == "llm" and r["freshness_ms"])
        summary = {"events": len(ms), "fast_ms_median": ms[len(ms) // 2] if ms else None,
                   "fast_ms_max": max(ms) if ms else None,
                   "llm_ms_median": llm[len(llm) // 2] if llm else None, "llm_ms_max": max(llm) if llm else None}
        (OUT / "freshness_summary.json").write_text(json.dumps(summary, indent=2))
        print("freshness:", summary)
    print("samples written to", OUT)


if __name__ == "__main__":
    main()
