"""Thin Sarvam chat-completions client (OpenAI-shaped) with retry and JSON extraction helpers."""
import json
import re
import time

import httpx

from . import config

URL = "https://api.sarvam.ai/v1/chat/completions"


class LLMError(RuntimeError):
    pass


def chat(messages, model=None, temperature=0.2, max_tokens=800, json_schema=None, reasoning_effort=None, retries=3):
    if not config.SARVAM_LLM_API_KEY:
        raise LLMError("SARVAM_LLM_API_KEY not set")
    body = {"model": model or config.CHAT_MODEL, "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens}
    if reasoning_effort == "off":
        body["reasoning_effort"] = None  # fastest replies for live chat
    elif reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    if json_schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": json_schema}}
    last = None
    for i in range(retries):
        try:
            r = httpx.post(URL, json=body, headers={"api-subscription-key": config.SARVAM_LLM_API_KEY}, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504):
                raise LLMError(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
            msg = r.json()["choices"][0]["message"]
            return _strip(msg.get("content") or "")
        except (httpx.HTTPError, LLMError) as e:
            last = e
            if "HTTP 4" in str(e) and "429" not in str(e):
                break
            time.sleep(1.5 * (2 ** i))
    raise LLMError(str(last))


def claude_chat(messages, max_tokens=400, temperature=0.3):
    """Demo-only chat via Anthropic's Messages API (OpenAI-shaped messages in, text out). Synthetic data only."""
    if not config.ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY not set")
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] in ("user", "assistant")]
    while turns and turns[0]["role"] == "assistant":  # the API wants the user first: fold our opening into context
        system += f"\n\nYour first message (already sent): {turns.pop(0)['content']}"
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60,
                   headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
                   json={"model": config.CLAUDE_CHAT_MODEL, "system": system, "messages": turns,
                         "max_tokens": max_tokens, "temperature": temperature})
    if r.status_code >= 400:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()


def _strip(text):
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def json_out(messages, schema, model=None, **kw):
    """Ask for JSON matching `schema`; tolerate models that wrap it in prose or code fences."""
    try:
        text = chat(messages, model=model or config.EXTRACT_MODEL, json_schema=schema, **kw)
    except LLMError as e:
        if "response_format" not in str(e) and "json_schema" not in str(e):
            raise
        text = chat(messages, model=model or config.EXTRACT_MODEL, **kw)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise LLMError("no JSON in reply")
    return json.loads(m.group(0))
