"""Render the fixed-schema Markdown file. Same section order every time, for both roles."""
from globalctx import config

SOURCE_WINDOW = {
    "enquiries": "enquiry", "buyleads": "buylead", "buyer_calls": "buyer_call", "vani_calls": "vani_call",
    "exec_calls": "exec_call", "whatsapp": "whatsapp_bot", "pns": "pns", "sessions": "session",
    "buyer_activity": "buyer_activity", "sellers": "buyer_activity",
}


def front_matter(facts, generated_at, freshness_ms, evicted):
    srcs = sorted(facts.sources)
    look = {s: f"{config.LOOKBACK_DAYS[SOURCE_WINDOW[s]]}d" for s in srcs if s in SOURCE_WINDOW}
    lines = [
        "---",
        f"glid: {facts.glid}",
        f"role: {facts.role}",
        f"generated_at: {generated_at}",
        f"last_event_at: {facts.last_event_at or 'null'}",
        f"freshness_ms: {freshness_ms if freshness_ms is not None else 'null'}",
        f"data_quality: {facts.data_quality}",
        f"synthetic: {'true' if facts.synthetic else 'false'}",
        f"sources: [{', '.join(srcs)}]",
        "lookback: {" + ", ".join(f"{k}: {v}" for k, v in look.items()) + "}",
        "evicted: {" + ", ".join(f"{k.lower().replace(' & ', '_').replace(' ', '_')}: {v}"
                                  for k, v in evicted.items()) + "}",
        "---",
    ]
    return "\n".join(lines)


def render(facts, sections, evicted, generated_at, freshness_ms):
    title = "Seller" if facts.role == "seller" else "Buyer"
    out = [front_matter(facts, generated_at, freshness_ms, evicted), f"# {title} context · GLID {facts.glid}", ""]
    for name, rows in sections.items():
        out.append(f"## {name}")
        if name == "Suggested Opening":
            out.extend(f'"{r}"' for r in rows)
        else:
            out.extend(f"- {r}" for r in rows)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def parse(md: str) -> dict:
    """For non-bot consumers: {'meta': {...}, 'sections': {heading: [rows]}} with no LLM."""
    meta, sections, cur = {}, {}, None
    body = md
    if md.startswith("---"):
        _, fm, body = md.split("---", 2)
        for line in fm.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    for line in body.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            sections[cur] = []
        elif cur and line.strip():
            sections[cur].append(line.lstrip("- ").strip().strip('"'))
    return {"meta": meta, "sections": sections}
