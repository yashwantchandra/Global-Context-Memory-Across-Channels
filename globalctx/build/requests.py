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
        store.add_event(glid, role, "request", channel,
                        {**{k: v for k, v in r.items() if v}, "type": rtype, "status": "pending",
                         "session_id": session_id}, synthetic=synthetic)
        folders.add(config.REQUEST_FOLDERS[rtype])
    return [render(glid, f) for f in sorted(folders)]


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
             f"pending: {len(pending)}", f"total: {len(rows)}", f"synthetic: {'true' if synthetic else 'false'}", "---",
             f"# {TITLES[folder]} · GLID {glid}", ""]
    for title, group in (("Pending", pending), ("Done", [e for e in rows if e not in pending])):
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
