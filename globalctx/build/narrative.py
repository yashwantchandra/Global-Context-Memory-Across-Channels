"""The only LLM steps: the suggested opening line and the summary of our own sessions.

Facts never come from here. If Sarvam is unavailable, deterministic templates
are used, so a rebuild never fails because of the LLM.
"""
import hashlib
import json
import re
from datetime import datetime

from globalctx import config, llm
from globalctx.build.privacy import scrub

GENERIC = {
    "seller": "Namaste! Main IndiaMART se bol rahi hoon. Aapke business mein aaj main kaise madad kar sakti hoon?",
    "buyer": "Namaste! Main IndiaMART se bol rahi hoon. Aap kis product ke liye supplier dhoondh rahe hain?",
}
# A GLID with no history: we do not know yet whether they buy or sell, so the greeting must not assume
COLD_START = ("Namaste! Main IndiaMART se Vani bol rahi hoon. Aap kuch khareedna chahte hain, "
              "ya apne business ke liye madad chahiye?")


def basis(ctx: dict) -> str:
    return hashlib.sha1(json.dumps(ctx, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:16]


def language_rule(lang):
    lang = lang or "Hinglish"
    if lang == "Hinglish":
        return 'natural Hinglish in Roman script, like "Namaste ji! Main IndiaMART se Vani bol rahi hoon, ...".'
    if lang == "English":
        return "simple Indian English."
    if lang == "Hindi":
        return "simple spoken Hindi in Devanagari script."
    return (f"simple spoken {lang} in its native script, keeping product names and 'IndiaMART' in English. "
            f"The assistant's name is Vani.")


def name_rule(role, name, contact=None):
    if contact:
        return f"greet them as '{contact} ji' (the person's own name)."
    if not name:
        return "the name is unknown, so greet politely without a name."
    if role == "seller":
        return f"the business is '{name}'; greet them as '{name} ji' (it is a company name, never invent a person's name)."
    return f"greet them as '{name} ji'."


def template_opening(role, ctx, data_quality):
    if data_quality == "cold_start":
        return COLD_START
    if not any(v for k, v in ctx.items() if k not in ("name", "language")):
        return GENERIC[role]
    # sellers are addressed by company ("Kya main X se baat kar rahi hoon?"), buyers by first name
    name = f" {ctx['name'].title()} ji" if ctx.get("name") and role == "buyer" else ""
    hello = f"Namaste! Kya main {ctx['name']} se baat kar rahi hoon?" if ctx.get("name") and role == "seller" else ""
    if ctx.get("contact"):  # the person's own name, said in an earlier conversation, wins
        name, hello = f" {ctx['contact']} ji", ""
    s = ctx.get("latest_session")
    if s and s.get("callback"):
        topic = f" {s['requirement']} ke baare mein" if s.get("requirement") else ""
        return f"{hello or f'Namaste{name}!'} Aapne {s['callback']} call karne ko kaha tha{topic}, isliye call kiya hai."
    if s and s.get("requirement"):
        return (f"{hello or f'Namaste{name}!'} Pichhli baar humari baat {s['requirement'][:60]} ke baare mein hui thi, "
                f"wahin se aage badhte hain.")
    if role == "seller":
        if ctx.get("unread_enquiries_from_buyers") and ctx.get("latest_enquiry_from_a_buyer"):
            return (f"{hello or 'Namaste!'} Aapke paas {ctx['latest_enquiry_from_a_buyer']} ki nayi enquiry aayi hai, "
                    f"kya main usme aapki madad karun?")
        if ctx.get("categories"):
            return (f"{hello or 'Namaste!'} IndiaMART se bol rahi hoon, aapke {ctx['categories'][0]} business "
                    f"ke baare mein baat karni thi.")
    else:
        if ctx.get("latest_requirement"):
            return f"Namaste{name}! Aapne {ctx['latest_requirement']} ki requirement daali thi, kya aapko sahi supplier mil gaya?"
        if ctx.get("latest_enquiry"):
            return f"Namaste{name}! Aapne {ctx['latest_enquiry']} ke liye enquiry ki thi, kya main aur options dikhaun?"
    if name or hello:  # nothing specific to raise, but we know who they are
        return (f"{hello} " if hello else f"Namaste{name}! ") + "Main IndiaMART se Vani bol rahi hoon. Aaj main aapki kya madad kar sakti hoon?"
    return GENERIC[role]


OPENING_PROMPT = """You are VANI, IndiaMART's voice assistant, placing a call TO this {role}. Write the first sentence VANI says.
- Language: {language_rule} Max 30 words, warm and natural, easy for text-to-speech.
- Address the user by name: {name_rule}
- Speak TO the {role}, never as them.
- Use ONLY the facts below. Never invent numbers, names, products or dates.
- If a previous conversation with us exists, continue from its topic or next step.
- For a seller: enquiries are leads FROM buyers to this seller (never the seller's own need). If the seller has
  pending requests (catalogue or price updates), follow up on those first.
- Otherwise, if the user has an open requirement, name it briefly (product + when posted); if no supplier has
  connected yet, apologise for that in a few words and ask whether they still need it.
- Never mention any other buyer's or seller's name, phone or details.

Facts: {facts}"""

OPENING_SCHEMA = {"type": "json_schema", "json_schema": {"name": "opening", "schema": {
    "type": "object", "properties": {"opening": {"type": "string"}}, "required": ["opening"]}}}


def llm_opening(role, ctx, data_quality):
    if config.NO_LLM or data_quality == "cold_start":
        return template_opening(role, ctx, data_quality), "template"
    try:
        out = llm.chat_json([{"role": "user", "content": OPENING_PROMPT.format(
            role=role, language_rule=language_rule(ctx.get("language")), name_rule=name_rule(role, ctx.get("name"), ctx.get("contact")),
            facts=json.dumps(ctx, ensure_ascii=False, default=str))}],
            max_tokens=200, retries=1, response_format=OPENING_SCHEMA)
        text = scrub(str(out.get("opening", "") if isinstance(out, dict) else out).strip())
        # too thin to be useful (e.g. just a greeting) -> the template, which always names the topic
        if 60 < len(text) < 300 or (10 < len(text) < 300 and not any(ctx.get(k) for k in
                                    ("latest_session", "latest_requirement", "latest_enquiry_from_a_buyer"))):
            return text, "llm"
    except Exception:
        pass
    return template_opening(role, ctx, data_quality), "template"


SUMMARY_PROMPT = """Summarise this conversation between IndiaMART's assistant (bot) and a user who is probably a {role}.
Today is {today}. Hindi number words: saath=60, sattar=70, assi=80, nabbe=90, pachaas=50, chaalis=40, bees=20, sau=100. Convert relative times ("kal shaam 5 baje") to absolute ones ("10 Oct, 5 PM").
Return JSON with exactly these keys. Use null only when the user never mentioned it:
{{"summary": "<= 20 words in English: what the user said and what was agreed",
  "user_role": "seller if the user sells/supplies on IndiaMART in this conversation, buyer if they want to buy",
  "contact_name": "the user's OWN first name only if the USER said it in this conversation (e.g. 'main Rakesh bol raha hoon' -> 'Rakesh'); never take it from the bot's lines; else null",
  "requirement": "product/service the user mentioned needing or selling, e.g. 'PVC ceiling panel'",
  "quantity": "quantity with unit and any price the user said, e.g. '300 pieces @ Rs 85/piece'",
  "callback": "absolute date/time the user asked to be called back",
  "language": "language the user spoke: Hinglish, Hindi, English, Tamil, Telugu, Marathi, Gujarati, Bengali, Kannada, Malayalam, Punjabi or Odia",
  "next_step": "the agreed next step",
  "open_threads": ["short unresolved business items, max 2"],
  "requests": [{{"type": "catalogue_update | price_update | requirement | enquiry",
                "product": "product name", "details": "what exactly to change/post/ask, <= 15 words",
                "quantity": "quantity with unit or null", "price": "price with unit or null",
                "location": "delivery/supply location or null"}}]}}
requests: only actions the user explicitly asked IndiaMART to do.
- catalogue_update: a seller asks to add, remove or edit a product, photo, description or stock in their catalogue.
- price_update: a seller asks to change the listed price of a product.
- requirement: a buyer asks to post a buy requirement, or changes/confirms one they already posted (put what changed in details).
- enquiry: a buyer asks to send an enquiry or get a quote from a particular seller or for a particular listed product.
Return [] when there is no such request. Complaints, fraud reports, feedback and support issues are NOT requests.
Request fields describe only the user's own product and terms: never put another buyer's or seller's name,
company, city or contact in them.
Never write any other buyer's or seller's name, phone number or email anywhere in the output (say "a buyer" / "a seller").
Never record requests for another party's personal details: those are refused by policy and are not open items."""


def _empty(v):
    """The model sometimes writes null/none/'' as text."""
    return v is None or (isinstance(v, str) and v.strip().lower() in ("", "null", "none", "n/a", "na", "unknown"))


def summarise_session(role, turns):
    """turns: [{'speaker': 'user'|'bot', 'text': ...}]. Returns the summary dict (LLM or fallback)."""
    convo = "\n".join(f"{t['speaker']}: {t['text']}" for t in turns if t.get("text"))
    fallback = {
        "summary": scrub("; ".join(t["text"] for t in turns if t["speaker"] == "user")[-120:]) or "short conversation",
        "requirement": None, "quantity": None, "callback": None, "language": None,
        "next_step": None, "open_threads": [], "requests": [], "user_role": None, "contact_name": None,
    }
    if config.NO_LLM or not convo:
        return fallback
    try:
        out = llm.chat_json([
            {"role": "user", "content": SUMMARY_PROMPT.format(role=role, today=datetime.now().strftime("%d %b %Y (%A)")) + "\n\nConversation:\n" + convo[-6000:]},
        ], max_tokens=400, retries=1, response_format={"type": "json_object"})
        if not isinstance(out, dict):
            return fallback
        reqs = [r for r in (out.pop("requests", None) or []) if isinstance(r, dict) and r.get("type") in
                ("catalogue_update", "price_update", "requirement", "enquiry")]
        out = {k: (scrub(v) if isinstance(v, str) else [scrub(x) for x in v if isinstance(x, str)]
                   if isinstance(v, list) else v)
               for k, v in {**fallback, **out}.items()}
        out["requests"] = [{k: scrub(str(v)) if not _empty(v) else None for k, v in r.items()} for r in reqs]
        out = {k: (None if _empty(v) else v) for k, v in out.items()}
        out["open_threads"] = [x for x in out.get("open_threads") or [] if not _empty(x)]
        out["requests"] = out.get("requests") or []
        # a name counts only if the user actually said it (the model sometimes copies it from the bot's lines)
        user_text = " ".join(x["text"] for x in turns if x.get("speaker") == "user").lower()
        if out.get("contact_name") and out["contact_name"].strip().lower() not in user_text:
            out["contact_name"] = None
        if out.get("user_role") not in ("buyer", "seller"):
            out["user_role"] = None
        # belt and braces: drop any thread about other parties' contact details
        out["open_threads"] = [t for t in out.get("open_threads") or []
                               if not re.search(r"phone|number|contact|email|naam|name", t, re.I)]
        return out
    except Exception:
        return fallback
