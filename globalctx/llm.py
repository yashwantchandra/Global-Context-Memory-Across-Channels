"""Thin wrapper over the Sarvam chat API with retry and JSON parsing."""
import json
import re
import time

from sarvamai import SarvamAI

from globalctx import config

_client = None


def client():
    global _client
    if _client is None:
        if not config.SARVAM_API_KEY:
            raise RuntimeError("SARVAM_API_KEY is not set in .env")
        _client = SarvamAI(api_subscription_key=config.SARVAM_API_KEY)
    return _client


def chat(messages, model=None, temperature=0.2, max_tokens=800, retries=2, **kw):
    """Return the assistant text. Reasoning is switched off for latency unless asked for."""
    kw.setdefault("reasoning_effort", None)
    last = None
    for attempt in range(retries + 1):
        try:
            r = client().chat.completions(
                messages=messages, model=model or config.NARRATIVE_MODEL,
                temperature=temperature, max_tokens=max_tokens, **kw,
            )
            return (r.choices[0].message.content or "").strip()
        except Exception as e:  # network, throttling, 5xx
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


def chat_json(messages, **kw):
    """Ask for JSON and parse it, tolerating code fences or stray text around the object."""
    text = chat(messages, **kw)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)
