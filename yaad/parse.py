"""Parse a buyer.md / seller.md by headings, with no LLM. This is how non-bot consumers reuse the files."""
import re


def parse(md: str) -> dict:
    out = {"meta": {}, "sections": {}}
    m = re.match(r"---\n(.*?)\n---\n", md, flags=re.S)
    if m:
        for ln in m.group(1).splitlines():
            if ": " in ln or ln.endswith(":"):
                k, _, v = ln.partition(":")
                out["meta"][k.strip()] = v.strip()
        md = md[m.end():]
    for block in re.split(r"^## ", md, flags=re.M)[1:]:
        head, _, body = block.partition("\n")
        out["sections"][head.strip()] = body.strip()
    threads = []
    for t in re.finditer(r"^\d+\. \*\*(.+?)\*\* \[(\w+)\] last (.+?) via (.+)\n\s+- (.+)\n\s+- next: (.+)$",
                         out["sections"].get("Open threads", ""), flags=re.M):
        threads.append(dict(zip(["title", "status", "last", "channel", "detail", "next"], t.groups())))
    out["threads"] = threads
    out["opening"] = out["sections"].get("Suggested opening", "").lstrip("> ").strip()
    out["guardrails"] = [l[2:] for l in out["sections"].get("Guardrails", "").splitlines() if l.startswith("- ")]
    return out
