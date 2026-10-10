"""Build one GLID's file: facts -> opening -> select/trim -> render -> save + freshness log."""
import csv
from datetime import datetime

from globalctx import config, store
from globalctx.build import facts as F
from globalctx.build import narrative, render, select


def _ms_between(start_iso, end_iso):
    try:
        return int((datetime.fromisoformat(end_iso) - datetime.fromisoformat(start_iso)).total_seconds() * 1000)
    except (TypeError, ValueError):
        return None


def log_freshness(row):
    new = not config.FRESHNESS_LOG.exists()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.FRESHNESS_LOG, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["glid", "role", "stage", "trigger_event_id", "trigger_source",
                                           "event_ingested_at", "generated_at", "freshness_ms", "chars", "opening_by"])
        if new:
            w.writeheader()
        w.writerow(row)


def build(glid, role, trigger=None, use_llm=True):
    """trigger: the event row that caused this rebuild (for freshness). use_llm=False gives the fast pass."""
    glid = str(glid)
    fx = F.facts_for(glid, role)
    prev = store.get_profile(glid, role)
    b = narrative.basis({"ctx": fx.opening_context, "q": fx.data_quality})
    if config.OPENING_MODE != "llm":  # default: deterministic, free, instant (approach C)
        opening, by = narrative.template_opening(role, fx.opening_context, fx.data_quality), "template"
    elif prev and prev.get("opening_basis") == b and prev.get("opening"):
        opening, by = prev["opening"], "cached"
    elif use_llm:
        opening, by = narrative.llm_opening(role, fx.opening_context, fx.data_quality)
    else:
        opening, by = narrative.template_opening(role, fx.opening_context, fx.data_quality), "template"

    # freshness is measured from when the triggering event entered the store
    generated_at = store.now_iso()
    if trigger:  # event-driven rebuild: this is the freshness measurement (event entered store -> file written)
        freshness_ms = _ms_between(trigger["ingested_at"], generated_at)
    else:        # manual / sweep rebuild: keep the last real measurement instead of reporting file age
        freshness_ms = (prev or {}).get("freshness_ms")

    def render_fn(sections, evicted):
        return render.render(fx, sections, evicted, generated_at, freshness_ms)

    sections, evicted, md = select.select(fx, opening, render_fn)
    # keep the template opening's basis unset so the LLM pass can still upgrade it
    store.save_profile(glid, role, md, generated_at, fx.last_event_at, freshness_ms, opening,
                       b if by in ("llm", "cached") else None)
    path = config.PROFILES_DIR / role / f"{glid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    if trigger:
        log_freshness({
            "glid": glid, "role": role, "stage": "llm" if use_llm else "fast",
            "trigger_event_id": trigger.get("id"), "trigger_source": trigger.get("source"),
            "event_ingested_at": trigger["ingested_at"], "generated_at": generated_at, "freshness_ms": freshness_ms,
            "chars": len(md), "opening_by": by,
        })
    return {"glid": glid, "role": role, "md": md, "chars": len(md), "freshness_ms": freshness_ms,
            "opening_by": by, "evicted": evicted, "path": str(path)}
