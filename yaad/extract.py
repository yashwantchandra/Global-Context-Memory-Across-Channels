"""LLM extraction (sarvam-105b): free text -> small fixed fields. Runs once per event and is cached.

Rule for every prompt: only facts stated in the text; null when unknown. Never invent.
"""
import re

from . import config, llm, store

S = {"type": ["string", "null"]}

CONV_SCHEMA = {
    "type": "object",
    "properties": {
        "one_line": {"type": "string", "description": "<=18 words, English, what happened"},
        "summary": {"type": "string", "description": "<=30 words, English"},
        "product": S,
        "qty": {**S, "description": "the latest quantity the customer stated, in digits, e.g. '400 pcs' (convert Hindi number words like 'chaar sau' to digits)"},
        "spec": {**S, "description": "size/grade/material stated, e.g. '2 inch, medium class'"}, "price": S,
        "next_step": {**S, "description": "concrete promise or pending action, <=12 words, English; null if none"},
        "next_step_hinglish": {**S, "description": "same next step in natural Roman Hinglish, <=12 words, as the bot would say it to the customer; null if none"},
        "callback_date": S,
        "objection": S,
        "language": {"type": "string", "enum": ["Hindi", "English", "Hinglish", "Gujarati", "Other"]},
        "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative", "frustrated"]},
        "closed": {"type": "boolean", "description": "true if the customer's need was fully resolved"},
    },
    "required": ["one_line", "summary", "next_step", "next_step_hinglish", "language", "sentiment", "closed"],
}

SYS = ("You extract structured memory from IndiaMART conversations. Use ONLY facts explicitly stated. "
       "If a field is not stated, return null. Never guess names, prices, dates or quantities. Output JSON only.")


def conversation(turns, role, channel):
    """turns: [(speaker, text)] from our own voice/chat session."""
    convo = "\n".join(f"{'Customer' if s == 'user' else 'Bot'}: {t}" for s, t in turns)[-6000:]
    try:
        msgs = [{"role": "system", "content": SYS},
                {"role": "user", "content": f"Channel: {channel}. The customer is a {role} on IndiaMART.\n\n{convo}"}]
        try:
            out = llm.json_out(msgs, CONV_SCHEMA, max_tokens=900, reasoning_effort="off")
        except Exception:  # transient empty/invalid reply seen in testing: one retry
            out = llm.json_out(msgs, CONV_SCHEMA, max_tokens=1200, reasoning_effort="off", temperature=0.4)
        out["extracted_by"] = config.EXTRACT_MODEL
        if not out.get("qty"):  # deterministic backstop: the model often drops quantities
            out["qty"] = _qty(" ".join(t for s, t in turns if s == "user"))
        return out
    except Exception as e:  # LLM down must never break write-back
        last_user = next((t for s, t in reversed(turns) if s == "user"), "")
        return {"one_line": f"{channel} conversation ({len(turns)} turns)", "summary": last_user[:120],
                "next_step": None, "language": "Hinglish", "sentiment": "neutral", "closed": False,
                "qty": _qty(" ".join(t for s, t in turns if s == "user")),
                "extracted_by": f"fallback ({type(e).__name__})"}


QTY = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(pcs|pc|piece|pieces|nos|units?|kg|kgs|ton|tons|tonne|tonnes|mt|meter|meters|mtr|ltr|litre|litres|boxes|box|bags?)\b", re.I)


UNITS = {"ek": 1, "one": 1, "do": 2, "two": 2, "teen": 3, "three": 3, "chaar": 4, "char": 4, "four": 4, "paanch": 5,
         "panch": 5, "five": 5, "chhe": 6, "chah": 6, "six": 6, "saat": 7, "seven": 7, "aath": 8, "eight": 8, "nau": 9,
         "nine": 9, "das": 10, "ten": 10, "bees": 20, "twenty": 20, "pachaas": 50, "fifty": 50,
         "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
         "बीस": 20, "पचास": 50}
SCALE = {"sau": 100, "hundred": 100, "सौ": 100, "hazaar": 1000, "hazar": 1000, "thousand": 1000, "हज़ार": 1000,
         "हजार": 1000}
WORD_UNITS = {"piece": "pcs", "pieces": "pcs", "pcs": "pcs", "पीस": "pcs", "kg": "kg", "किलो": "kg", "kilo": "kg",
              "ton": "ton", "टन": "ton", "meter": "m", "मीटर": "m", "box": "box", "bag": "bags"}
WORD_QTY = re.compile(r"(\S+)\s+(sau|hundred|सौ|hazaa?r|thousand|हज़ार|हजार)\s+(\S+)", re.I)


def _qty(text):
    m = QTY.search(text or "")
    if m:
        return f"{m.group(1)} {m.group(2).lower()}"
    for m in WORD_QTY.finditer(text or ""):  # "chaar sau piece", "चार सौ पीस", "four hundred pieces"
        n, scale = UNITS.get(m.group(1).lower().strip(",.")), SCALE.get(m.group(2).lower())
        unit = WORD_UNITS.get(m.group(3).lower().strip(",.।"))
        if n and scale and unit:
            return f"{n * scale} {unit}"
    return None


BOT_SCHEMA = {
    "type": "object",
    "properties": {"one_line": {"type": "string"}, "objection": S, "callback_date": S, "next_step": S},
    "required": ["one_line"],
}
ENQ_SCHEMA = {
    "type": "object",
    "properties": {"one_line": {"type": "string"}, "qty": S, "spec": S},
    "required": ["one_line"],
}


def bot_call(e):
    text = e["payload"].get("summary") or ""
    if not text.strip():
        return None
    return llm.json_out([{"role": "system", "content": SYS},
                         {"role": "user", "content": "Summarise this VANI sales-bot call with a seller. one_line <=15 words, "
                                                     "objection = seller's main objection, callback_date = when they asked "
                                                     "to be called back, next_step = pending action.\n\n" + text}],
                        BOT_SCHEMA, max_tokens=500, reasoning_effort="off")


def enquiry(e):
    text = e["payload"].get("message") or ""
    if len(text.strip()) < 15:
        return None
    return llm.json_out([{"role": "system", "content": SYS},
                         {"role": "user", "content": "A buyer's enquiry. one_line <=12 words describing the requirement; "
                                                     "qty and spec only if stated.\n\n" + text}],
                        ENQ_SCHEMA, max_tokens=400, reasoning_effort="off")


def backfill(glid, log=print):
    """Extract the few free-text events that can reach a file: last 3 VANI calls, last 6 buyer enquiries."""
    done = 0
    for role, src, fn, k in (("seller", "bot_call", bot_call, 3), ("buyer", "enquiry", enquiry, 6)):
        evs = [e for e in store.events(glid, role) if e["source"] == src][-k:]
        for e in evs:
            if e["extracted"]:
                continue
            try:
                x = fn(e)
            except Exception as ex:
                log(f"  extract failed for event {e['id']}: {type(ex).__name__}")
                continue
            if x:
                store.set_extracted(e["id"], x)
                done += 1
    return done
