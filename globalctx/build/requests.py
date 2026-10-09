"""Action requests raised on our channels -> one md per GLID per folder.

  data/requests/catalogue_updation_requests/<glid>.md   seller: catalogue edits and price changes
  data/requests/requirements/<glid>.md                  buyer: requirements to post
  data/requests/enquiry/<glid>.md                       buyer: enquiries to send

Each file lists that GLID's requests, newest first, with status. Ops teams (or an automation) work from
these files; the profile file shows the pending ones under Open Threads so the bot can follow up.
"""
from datetime import datetime

from globalctx import config, store
from globalctx.build.privacy import scrub

TITLES = {"catalogue_updation_requests": "Catalogue updation requests", "requirements": "Requirements to post",
          "enquiry": "Enquiries to send"}
TYPE_LABEL = {"catalogue_update": "Catalogue update", "price_update": "Price update",
              "requirement": "Requirement", "enquiry": "Enquiry"}


def record(glid, role, channel, session_id, requests, synthetic):
    """Store each request as an event, then re-render the request files it touches. Returns written paths."""
    folders = set()
    for r in requests:
        rtype = r.get("type")
        if rtype not in config.REQUEST_TYPES_BY_ROLE.get(role, set()):
            continue  # e.g. a seller cannot raise an enquiry; complaints are not requests
        payload = {**{k: v for k, v in r.items() if v}, "type": rtype, "status": "pending", "session_id": session_id}
        if _same_pending(glid, role, rtype, r.get("product")):
            # the same request raised again (e.g. asked on two calls): supersede the old one, keep one line
            _close_pending(glid, role, rtype, r.get("product"), status="merged")
        store.add_event(glid, role, "request", channel, payload, synthetic=synthetic)
        folders.add(config.REQUEST_FOLDERS[rtype])
    return [render(glid, f) for f in sorted(folders)]


def _key(product):
    return " ".join(sorted(w for w in (product or "").lower().replace("-", " ").split() if len(w) > 2))


def _same_pending(glid, role, rtype, product):
    return any(e["payload"].get("type") == rtype and e["payload"].get("status", "pending") == "pending"
               and _key(e["payload"].get("product")) == _key(product)
               for e in store.events_for(glid, role, source="request"))


def _close_pending(glid, role, rtype, product, status="merged"):
    import json
    with store.connect() as c:
        for e in store.events_for(glid, role, source="request"):
            p = e["payload"]
            if p.get("type") == rtype and p.get("status", "pending") == "pending" and _key(p.get("product")) == _key(product):
                p["status"] = status
                c.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(p, ensure_ascii=False), e["id"]))


def render(glid, folder):
    types = [t for t, f in config.REQUEST_FOLDERS.items() if f == folder]
    rows, role, synthetic = [], None, False
    for r in ("seller", "buyer"):
        for e in store.events_for(glid, r, source="request"):
            if e["payload"].get("type") in types:
                rows.append(e)
                role = role or r
                synthetic = synthetic or bool(e["synthetic"])
    rows.sort(key=lambda e: e["ts"], reverse=True)
    pending = [e for e in rows if e["payload"].get("status", "pending") == "pending"]
    lines = ["---", f"glid: {glid}", f"role: {role}", f"folder: {folder}",
             f"updated_at: {datetime.now().isoformat(timespec='seconds')}",
             f"pending: {len(pending)}", f"total: {len(pending) + len([e for e in rows if e['payload'].get('status') == 'done'])}", f"synthetic: {'true' if synthetic else 'false'}", "---",
             f"# {TITLES[folder]} · GLID {glid}", ""]
    done = [e for e in rows if e["payload"].get("status") == "done"]
    for title, group in (("Pending", pending), ("Done", done)):
        lines.append(f"## {title}")
        if not group:
            lines.append("- None")
        for e in group:
            p = e["payload"]
            parts = [p.get("product"), p.get("details"), p.get("quantity") and f"qty {p['quantity']}",
                     p.get("price") and f"price {p['price']}", p.get("location") and f"at {p['location']}"]
            lines.append(scrub(f"- {e['ts'][:16].replace('T', ' ')} · {TYPE_LABEL.get(p['type'], p['type'])} · "
                               + " · ".join(x for x in parts if x)
                               + f" · via {e['channel']}" + (" (synthetic)" if e["synthetic"] else "")))
        lines.append("")
    path = config.REQUESTS_DIR / folder / f"{glid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return str(path)
