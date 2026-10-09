"""Row selection: caps per section, ranking, Do-Not-Ask derivation and the size budget.

Rows are never edited in place. Every rebuild re-selects from the event store,
so 'deleting' a row means it is not picked here (expired, closed, outranked
or trimmed). What was dropped is counted in `evicted` for the front-matter.
"""
from globalctx import config

FIXED = {"Identity", "Snapshot", "Leads & Enquiries", "Responses & Calls", "Enquiries & Status", "KYC",
         "Categories Searched", "Sellers Contacted"}
RANKED = {"Past Conversations", "Open Threads", "Engagement Signals"}
NEVER_TRIM = {"Identity", "Snapshot", "Open Threads", "Do-Not-Ask", "Suggested Opening", "KYC"}
MAX_PER_CHANNEL = 2

EMPTY = {
    "Past Conversations": "No conversations in the last 30-90 days",
    "Open Threads": "None",
    "Engagement Signals": "No engagement data",
    "Snapshot": "No profile data (cold start)",
}


def clip(text, n=config.MAX_ROW_CHARS):
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def fmt(row, n=config.MAX_ROW_CHARS):
    t = clip(row.text, n - (12 if row.synthetic else 0))
    return t + (" (synthetic)" if row.synthetic else "")


def pick(name, rows, cap):
    """Return (kept_rows, n_evicted) for one section."""
    seen, uniq = set(), []
    for r in rows:
        key = r.text.lower()
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    if name in RANKED:
        uniq.sort(key=lambda r: (r.pinned, r.score), reverse=True)
        if name == "Past Conversations":  # keep the history cross-channel
            per, mixed = {}, []
            for r in uniq:
                per[r.channel] = per.get(r.channel, 0) + 1
                if r.pinned or per[r.channel] <= MAX_PER_CHANNEL:
                    mixed.append(r)
            uniq = mixed + [r for r in uniq if r not in mixed]
            # newest first for reading, pinned stays first
            kept = uniq[:cap]
            kept = [r for r in kept if r.pinned] + sorted([r for r in kept if not r.pinned],
                                                          key=lambda r: r.ts, reverse=True)
            return kept, max(0, len(rows) - len(kept))
    kept = uniq[:cap]
    return kept, max(0, len(rows) - len(kept))


def do_not_ask(known, kept_sections):
    """Only facts whose source section still has rows survive (an evicted fact is never claimed)."""
    items = []
    for fact, section in known:
        if kept_sections.get(section) and fact not in items:
            items.append(fact)
    return clip("; ".join(items), 260) if items else "Nothing yet: ask open questions"


def select(facts, opening, render_fn):
    """facts: Facts. render_fn(sections, evicted) -> md text. Returns (sections, evicted, md)."""
    caps = config.SECTION_CAPS[facts.role]
    kept, evicted = {}, {}
    for name, cap in caps.items():
        if name in ("Do-Not-Ask", "Suggested Opening"):
            continue
        rows, dropped = pick(name, facts.sections.get(name, []), cap)
        kept[name] = rows
        if dropped:
            evicted[name] = dropped

    def assemble():
        out = {}
        for name in caps:
            if name == "Do-Not-Ask":
                out[name] = [do_not_ask(facts.known, kept)]
            elif name == "Suggested Opening":
                out[name] = [clip(opening, 260)]
            else:
                out[name] = [fmt(r) for r in kept[name]] or [EMPTY.get(name, "No data in lookback window")]
        return out

    sections = assemble()
    md = render_fn(sections, evicted)
    # Global budget: trim from the bottom of low-priority sections, never pinned rows
    for name in config.TRIM_ORDER:
        while len(md) > config.MAX_CHARS and name in kept and name not in NEVER_TRIM:
            removable = [r for r in kept[name] if not r.pinned]
            if not removable:
                break
            kept[name].remove(removable[-1])
            evicted[name] = evicted.get(name, 0) + 1
            sections = assemble()
            md = render_fn(sections, evicted)
    return sections, evicted, md
