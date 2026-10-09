"""Which file does this GLID use right now: buyer or seller? Nothing is hard-coded per channel.

  - only one role has a file or events      -> that role
  - both roles exist                        -> the role where the user was active most recently
  - neither exists                          -> create a cold-start file under DEFAULT_NEW_ROLE; the first
                                               conversation's summary can move it (sessions.end)
"""
from globalctx import config, store


def _activity(glid, role):
    """(exists, last activity ts) for one role: latest non-profile event, else the profile snapshot."""
    with store.connect() as c:
        live = c.execute("SELECT MAX(ts) FROM events WHERE glid=? AND role=? AND source!='profile'",
                         (str(glid), role)).fetchone()[0]
        any_ev = c.execute("SELECT MAX(ts) FROM events WHERE glid=? AND role=?", (str(glid), role)).fetchone()[0]
    prof = store.get_profile(glid, role)
    exists = bool(any_ev) or bool(prof and "data_quality: cold_start" not in prof["md"])
    return exists, live or any_ev or ""


def resolve(glid):
    """Returns (role, reason)."""
    seller = _activity(glid, "seller")
    buyer = _activity(glid, "buyer")
    if seller[0] and not buyer[0]:
        return "seller", "only a seller file exists"
    if buyer[0] and not seller[0]:
        return "buyer", "only a buyer file exists"
    if seller[0] and buyer[0]:
        role = "seller" if seller[1] >= buyer[1] else "buyer"
        return role, f"both exist; most recent activity as {role} ({max(seller[1], buyer[1])[:16]})"
    for role in ("seller", "buyer"):  # a cold-start file created earlier
        if store.get_profile(glid, role):
            return role, "cold-start file already created"
    return config.DEFAULT_NEW_ROLE, "no file yet: a cold-start file is created on first use"
