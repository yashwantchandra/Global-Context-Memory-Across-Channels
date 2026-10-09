"""Demo phone numbers -> GLIDs (teammates role-playing a buyer or seller). Kept in data/ (gitignored).

  python -m globalctx.phonemap add +9198XXXXXXXX --glid 22552651 --role seller
  python -m globalctx.phonemap list
  python -m globalctx.phonemap remove +9198XXXXXXXX

Used by voice/phone.py: `call --phone` finds the GLID, and the poller maps a call's phone number back to
its GLID when the agent's variables don't carry one. Calls from a mapped number are role-play, so their
events are labelled synthetic.
"""
import argparse
import hashlib
import json
import re

from globalctx import config

PATH = config.DATA_DIR / "phone_map.json"


def norm(phone):
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) == 10:
        digits = "91" + digits
    return "+" + digits


def load():
    return json.loads(PATH.read_text()) if PATH.exists() else {}


def save(m):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(m, indent=2))


def add(phone, glid, role=None):
    m = load()
    m[norm(phone)] = {"glid": str(glid), "role": role}  # role None -> resolved at call time
    save(m)
    return norm(phone)


def lookup(phone_or_hash):
    """Accepts a number, or Sarvam's caller ID, which its privacy config stores as sha256('+91XXXXXXXXXX')."""
    m = load()
    key = str(phone_or_hash).strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", key):
        return next((v for p, v in m.items() if hashlib.sha256(p.encode()).hexdigest() == key), None)
    return m.get(norm(phone_or_hash))


def phone_for(glid, role=None):
    return next((p for p, v in load().items() if v["glid"] == str(glid) and role in (None, v.get("role"))), None)


def mask(phone):
    return phone[:5] + "*****" + phone[-2:]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("phone"); a.add_argument("--glid", required=True)
    a.add_argument("--role", choices=["seller", "buyer"], help="optional: resolved from the GLID's files if omitted")
    r = sub.add_parser("remove"); r.add_argument("phone")
    sub.add_parser("list")
    args = ap.parse_args()
    if args.cmd == "add":
        print(f"mapped {mask(add(args.phone, args.glid, args.role))} -> {args.role} {args.glid}")
    elif args.cmd == "remove":
        m = load(); m.pop(norm(args.phone), None); save(m); print("removed")
    else:
        for p, v in load().items():
            print(f"{mask(p)} -> {v['role']} {v['glid']}")
