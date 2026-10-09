"""Memory -> buyer.md / seller.md. Fixed section order, template opening, token budget via guard."""
from datetime import datetime

from . import config, guard

SECTIONS = ["Who", "Known – don't ask", "Open threads", "Recent timeline", "Guardrails", "Suggested opening"]

OPEN_SELLER = {
    "PROMISED": "Namaste {name}! Pichli baar {channel} pe jo tay hua tha, usi ka update dene ke liye sampark kiya hai. Do minute baat ho sakti hai?",
    "CALLBACK_DUE": "Namaste {name}! Aapne dobara call karne ko kaha tha, isliye call kiya hai. Do minute baat ho sakti hai?",
    "LEADS_PENDING": "Namaste {name}! Aapke {product} ke liye pichhle mahine kaafi enquiries aayi hain, kuch abhi tak reply nahi hui. Main WhatsApp pe bhej doon?",
    "CATALOG_BLOCKER": "Namaste {name}! Aapne WhatsApp pe product photo bheji thi jo catalog mein add nahi ho payi. Main abhi madad kar doon?",
    "MEETING_FIXED": "Namaste {name}! Hamari meeting fix hui thi, bas confirm karna tha ki sab theek raha?",
    "MISSED_CALLS": "Namaste {name}! Pichhle mahine kuch buyers ki calls aap tak connect nahi ho payin. Bataun kaise ek bhi lead miss na ho?",
    "NEW_ENQUIRY": "Namaste {name}! Abhi abhi aapke {product} ke liye ek nayi enquiry aayi hai{extra}. Kya main details WhatsApp pe bhej doon?",
}
OPEN_BUYER = {
    "PROMISED": "Namaste ji! Pichli baar {channel} pe aapki {product} requirement ki baat hui thi. Usi ka update dene ke liye sampark kiya hai. Baat karein?",
    "UNANSWERED": "Namaste ji! Aapne {product} ke liye sellers ko enquiry bheji thi, abhi tak jawab nahi aaya. Kya main 2-3 aur sellers se abhi connect karwa doon?",
    "FAILED_CONNECT": "Namaste ji! Aap {product} ke liye seller se baat karna chah rahe the par call connect nahi hui. Main abhi connect karwa doon?",
    "IN_DISCUSSION": "Namaste ji! {product} ke liye sellers se baat chal rahi thi. Deal ho gayi ya aur quotes chahiye?",
    "ACTIVE_NOW": "Namaste ji! Dekha ki aap abhi {product} dhoondh rahe hain. Kya main abhi 2-3 achhe sellers se connect karwa doon?",
    "SELLERS_REACHING_OUT": "Namaste ji! Aapki {product} requirement par kuch sellers ne interest dikhaya hai. Aapko kis time call theek rahega?",
    "STALE": "Namaste ji! Kya aapki {product} ki requirement abhi bhi hai?",
    "OPEN": "Namaste ji! Kya aapki {product} ki requirement abhi bhi hai?",
}
COLD = "Namaste! Main IndiaMART ki virtual assistant bol rahi hoon. Aaj main aapki kya madad kar sakti hoon?"


def opening(m):
    if m.cold or not m.threads:
        return COLD
    t = m.threads[0]
    name = "ji"
    if m.role == "seller":
        company = next((w.split(": ", 1)[1] for w in m.who if w.startswith("Company: ")), "")
        name = f"ji, {company} se baat ho rahi hai" if company else "ji"
    tpl = (OPEN_SELLER if m.role == "seller" else OPEN_BUYER).get(t.status, COLD)
    product = t.title.split(" · ")[-1] if t.title else "aapki requirement"
    extra = ""
    if t.status == "NEW_ENQUIRY":
        city = t.detail.split(" from ", 1)[1].split(",")[0] if " from " in t.detail else ""
        qty = t.detail.split("qty ", 1)[1] if "qty " in t.detail else ""
        extra = (f", {city} se" if city else "") + (f", {qty} ki" if qty else "")
    return tpl.format(name=name, channel=t.channel or "call", next=t.next_step, product=product, extra=extra,
                      spoken=(t.spoken or t.next_step).rstrip("."))


def _body(m, threads, timeline, guardrails, known):
    out = []
    out.append("## Who")
    out += [f"- {w}" for w in m.who] or ["- (no profile on record)"]
    out.append("\n## Known – don't ask")
    out += [f"- {k}" for k in known] or ["- nothing yet: ask open questions"]
    out.append("\n## Open threads")
    if threads:
        for i, t in enumerate(threads, 1):
            out.append(f"{i}. **{t.title}** [{t.status}] last {t.last_touch.strftime('%-d %b')} via {t.channel}")
            out.append(f"   - {t.detail}")
            out.append(f"   - next: {t.next_step}")
    else:
        out.append("- none open")
    out.append("\n## Recent timeline")
    out += [f"- {l}" for l in timeline] or ["- no recent activity"]
    out.append("\n## Guardrails")
    out += [f"- {g}" for g in guardrails] or ["- none"]
    out.append("\n## Suggested opening")
    out.append(f"> {m.opening}")
    return "\n".join(out)


def render(m, freshness_ms=None):
    m.opening = opening(m)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    fm = [
        "---",
        f"glid: {m.glid}",
        f"role: {m.role}",
        f"generated_at: {now}",
        f"last_event_at: {m.last_event_ts or 'none'}",
        f"freshness_ms: {'' if freshness_ms is None else round(freshness_ms)}",
        f"language: {m.language}",
        f"cold_start: {str(m.cold).lower()}",
        f"synthetic_sources: [{', '.join(m.synthetic_sources)}]",
        "---",
        f"# {'Seller' if m.role == 'seller' else 'Buyer'} memory · GLID {m.glid}",
        "",
    ]
    threads, timeline, guardrails, known = list(m.threads), list(m.timeline), list(m.guardrails), list(m.known)
    budget = config.TOKEN_BUDGET[m.role]
    while True:
        text = "\n".join(fm) + _body(m, threads, timeline, guardrails, known) + "\n"
        if guard.tokens(text) <= budget:
            break
        # trim lowest-value content first
        if len(timeline) > 2:
            timeline.pop()
        elif len(guardrails) > 3:
            guardrails.pop()
        elif len(threads) > 2:
            threads.pop()
        elif len(timeline) > 0:
            timeline.pop()
        elif len(known) > 2:
            known.pop()
        else:
            break
    return guard.check(text, m)
