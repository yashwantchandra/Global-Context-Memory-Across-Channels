"""Scrub contact details from any free text before it reaches a file or a prompt."""
import re

PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[email]"),
    (re.compile(r"https?://\S+|www\.\S+|\b\w+\.(?:in|com|net|org)/\S*"), "[link]"),
    (re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}\b"), "[phone]"),   # Indian mobiles
    (re.compile(r"\b0?\d{2,4}[\s-]?\d{6,8}\b"), "[phone]"),                # landlines
    (re.compile(r"\b\d{10,}\b"), "[number]"),                             # any other long id / number
    (re.compile(r"\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b"), "[id]"),               # Aadhaar-like
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[id]"),                       # PAN
]


def scrub(text: str) -> str:
    text = text or ""
    for pat, repl in PATTERNS:
        text = pat.sub(repl, text)
    return text


def has_contact(text: str) -> bool:
    return scrub(text) != (text or "")
