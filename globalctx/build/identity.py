"""Who the bot is talking to: display name and preferred language, with the evidence for each.

Language priority (most direct evidence first):
  1. what the user spoke in our last voice/chat session (session summary)
  2. scripts and words in the user's own messages (WhatsApp chatbot, our session turns)
  3. languages detected on their phone calls (PNS extractions)
  4. default Hinglish, with the state's regional language noted as a hint
"""
import ast
import re
from collections import Counter

SCRIPTS = [  # unicode block -> language
    (r"[ऀ-ॿ]", "Hindi"), (r"[ঀ-৿]", "Bengali"), (r"[਀-੿]", "Punjabi"),
    (r"[઀-૿]", "Gujarati"), (r"[଀-୿]", "Odia"), (r"[஀-௿]", "Tamil"),
    (r"[ఀ-౿]", "Telugu"), (r"[ಀ-೿]", "Kannada"), (r"[ഀ-ൿ]", "Malayalam"),
]
HINGLISH_WORDS = re.compile(r"\b(hai|haan|nahi|kya|chahiye|bhai|ji|kitna|rate|bhejo|karo|mujhe|aap)\b", re.I)

STATE_LANGUAGE = {
    "telangana": "Telugu", "andhra pradesh": "Telugu", "tamil nadu": "Tamil", "karnataka": "Kannada",
    "kerala": "Malayalam", "maharashtra": "Marathi", "gujarat": "Gujarati", "west bengal": "Bengali",
    "odisha": "Odia", "punjab": "Punjabi",
}

SUPPORTED = {"Hindi", "Hinglish", "English", "Bengali", "Gujarati", "Kannada", "Malayalam", "Marathi",
             "Odia", "Punjabi", "Tamil", "Telugu"}


def _from_texts(texts):
    votes = Counter()
    for t in texts:
        t = t or ""
        hit = False
        for pat, lang in SCRIPTS:
            if re.search(pat, t):
                votes[lang] += 1
                hit = True
                break
        if not hit and re.search(r"[A-Za-z]", t):
            votes["Hinglish" if HINGLISH_WORDS.search(t) else "English"] += 1
    return votes


def preferred_language(events, profile, role):
    """Returns (language, source)."""
    for e in events:  # newest first: a language set by the team for this GLID wins
        if e["source"] == "contact" and e["payload"].get("preferred_language"):
            note = e["payload"].get("language_note")
            return e["payload"]["preferred_language"], "set by team" + (f"; {note}" if note else "")
    sessions = [e for e in events if e["source"] == "session" and e["payload"].get("kind") == "summary"]
    for s in sessions:  # newest first
        lang = (s["payload"].get("language") or "").strip()
        if lang:
            lang = lang.split()[0].strip(",.").title()
            if lang in SUPPORTED:
                return lang, "last conversation"

    user_texts = [e["payload"].get("user_message") for e in events if e["source"] == "whatsapp_bot"]
    user_texts += [e["payload"].get("text") for e in events
                   if e["source"] == "session" and e["payload"].get("speaker") == "user"]
    votes = _from_texts([t for t in user_texts if t])
    if sum(votes.values()) >= 2:
        return votes.most_common(1)[0][0], "own messages"

    pns = Counter()
    for e in events:
        if e["source"] == "pns" and e["payload"].get("languages"):
            try:
                langs = ast.literal_eval(e["payload"]["languages"])
            except (ValueError, SyntaxError):
                langs = []
            if {"Hindi", "English"} <= set(langs):
                pns["Hinglish"] += 1
            for lg in langs:
                pns[lg] += 1
    if pns:
        return pns.most_common(1)[0][0], "phone calls"

    state = (profile.get("seller_state") or profile.get("state") or "").lower()
    regional = STATE_LANGUAGE.get(state)
    return "Hinglish", f"default{'; region speaks ' + regional if regional else ''}"


def display_name(profile, role):
    if role == "seller":
        return (profile.get("company_name") or "").strip()
    return (profile.get("first_name") or "").strip().title()
