"""Size, schema and privacy checks on every rendered file. Redacts rather than crashing."""
import re

PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
REQUIRED = ["## Snapshot", "## Open problems", "## Threads", "## Guardrails", "## Suggested opening"]


def tokens(text: str) -> int:
    """Cheap estimate: ~4 chars/token for Latin text, ~1.5 for Indic scripts."""
    indic = sum(1 for ch in text if "ऀ" <= ch <= "෿")
    return int((len(text) - indic) / 4 + indic / 1.5)


class GuardReport(dict):
    pass


def check(text: str, m):
    report = GuardReport(redactions=0, schema_ok=True, tokens=0)
    text, n1 = PHONE.subn("[redacted-phone]", text)
    text, n2 = EMAIL.subn("[redacted-email]", text)
    n3 = 0
    for s in sorted(m.forbidden, key=len, reverse=True):
        if s and len(s) >= 4 and s in text:
            n3 += text.count(s)
            text = text.replace(s, "[redacted]")
    report["redactions"] = n1 + n2 + n3
    report["schema_ok"] = all(h in text for h in REQUIRED)
    report["tokens"] = tokens(text)
    return text, report
