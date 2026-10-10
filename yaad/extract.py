"""LLM extraction: a conversation transcript -> small fixed fields (the slow lane). Runs once per conversation.

Rule for every prompt: only facts stated in the text; null when unknown. Never invent.
"""
import re

from . import config, llm

S = {"type": ["string", "null"]}

CONV_SCHEMA = {
    "type": "object",
    "properties": {
        "one_line": {"type": "string", "description": "<=18 words, English, what happened"},
        "summary": {"type": "string", "description": "<=30 words, English"},
        "product": S,
        "qty": {**S, "description": "the latest quantity the customer stated, in digits, e.g. '400 pcs' (convert Hindi number words like 'chaar sau' to digits)"},
        "spec": {**S, "description": "size/grade/material stated, e.g. '2 inch, medium class'"}, "price": S,
        "deadline": {**S, "description": "when the customer needs it, exactly as stated, e.g. 'before Diwali', 'by 20 Oct'; else null"},
        "next_step": {**S, "description": "concrete promise or pending action, <=12 words, English; null if none"},
        "next_step_hinglish": {**S, "description": "same next step in natural Roman Hinglish, <=12 words, as the bot would say it to the customer; null if none"},
        "callback_date": S,
        "objection": S,
        "language": {"type": "string", "enum": ["Hindi", "English", "Hinglish", "Gujarati", "Other"]},
        "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative", "frustrated"]},
        "closed": {"type": "boolean", "description": "true if the customer's need was fully resolved"},
        "complaint_issue": {**S, "description": "if the customer complained about a seller/order (fraud, non-delivery, quality), the issue in <=15 words; else null"},
        "complaint_seller": {**S, "description": "the seller the complaint is about, exactly as named or confirmed in the conversation; else null"},
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
        _clean(out)
        if not out.get("qty"):  # deterministic backstop: the model often drops quantities
            out["qty"] = _qty(" ".join(t for s, t in turns if s == "user"))
        if not out.get("complaint_issue"):  # a complaint must never be lost to model randomness
            out["complaint_issue"] = _complaint(turns)
        return out
    except Exception as e:  # LLM down must never break write-back
        last_user = next((t for s, t in reversed(turns) if s == "user"), "")
        return {"one_line": f"{channel} conversation ({len(turns)} turns)", "summary": last_user[:120],
                "next_step": None, "language": "Hinglish", "sentiment": "neutral", "closed": False,
                "qty": _qty(" ".join(t for s, t in turns if s == "user")),
                "complaint_issue": _complaint(turns),
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


COMPLAINT = re.compile(r"(fraud|froud|dhokha|cheat|scam|advance le|paise le|paisa le|maal nahi|nahi bheja|nahi aaya|"
                       r"refund|paisa wapas|paise wapas|complaint|shikayat|damaged|kharab|quality issue|धोखा|फ्रॉड|माल नहीं)", re.I)


def _complaint(turns):
    """Rule backstop: the customer's own line that sounds like a complaint about a seller/order, else None."""
    for s, t in turns:
        if s == "user" and COMPLAINT.search(t or ""):
            return t.strip()[:140]
    return None


def _clean(out):
    """Drop junk the model sometimes returns (schema echoes, placeholders): facts must look like facts."""
    for k in ("qty", "spec", "price", "product", "next_step", "complaint_seller"):
        v = out.get(k)
        if isinstance(v, str) and ("_or_" in v or "_" in v.strip() and " " not in v.strip() or len(v) > 120
                                   or v.strip().lower() in ("null", "none", "n/a", "unknown", "")):
            out[k] = None
    if out.get("qty") and not re.search(r"\d", str(out["qty"])):
        out["qty"] = None
