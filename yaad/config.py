"""Paths, lookback windows and size budgets. Every number here is a documented design choice."""
import os
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.environ.get("YAAD_DATA_DIR", ROOT / "Global Context - Seller Dataset"))
DB_PATH = Path(os.environ.get("YAAD_DB", ROOT / "data" / "yaad.db"))
PROFILES_DIR = Path(os.environ.get("YAAD_PROFILES", ROOT / "profiles"))

SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")  # Voice Agents platform key (apps.sarvam.ai)
# Model API key (api.sarvam.ai: chat + extraction). A different key from the Voice Agents one.
SARVAM_LLM_API_KEY = os.environ.get("SARVAM_LLM_API_KEY") or SARVAM_API_KEY
SARVAM_ORG_ID = os.environ.get("SARVAM_ORG_ID", "")
SARVAM_WORKSPACE_ID = os.environ.get("SARVAM_WORKSPACE_ID", "")
SARVAM_APP_ID = os.environ.get("SARVAM_APP_ID", "")
# Draft versions are reachable only when pinned; the runtime 404s "latest" until a version is committed.
SARVAM_APP_VERSION = int(os.environ.get("SARVAM_APP_VERSION", "1")) or None
# Extraction runs with reasoning off: with reasoning on, sarvam-105b spent the whole token budget thinking and
# returned empty content. The conversations model with reasoning off: ~650 ms, follows the schema.
EXTRACT_MODEL = os.environ.get("YAAD_EXTRACT_MODEL", "sarvam-105b-conversations")
CHAT_MODEL = "sarvam-105b-conversations"


def as_of() -> datetime:
    """'Now' for lookback windows. Override with YAAD_AS_OF=YYYY-MM-DD to replay history."""
    v = os.environ.get("YAAD_AS_OF")
    return datetime.fromisoformat(v) if v else datetime.now()


# Lookback per source, in days, with the reason (shown on the slide and in the README).
LOOKBACK_DAYS = {
    "bot_call": (180, "objections and dispositions stay relevant for months"),
    "enquiry": (90, "a buyer requirement older than a quarter is usually closed"),
    "pns": (90, "same horizon as enquiries; calls are the other half of a requirement"),
    "bl": (45, "the warehouse keeps only ~45 days of buy-lead purchases"),
    "whatsapp": (30, "chatbot intents (photo upload, callback) go stale within weeks"),
    "exec_call": (60, "a recent human touch changes what the bot should say"),
    "call_extract": (90, "products, prices and specs discussed on calls; requirement horizon"),
    "yaad": (None, "our own conversations are always the freshest truth"),
    "synthetic": (None, "labelled synthetic gap-fill"),
}
DETAIL_DAYS = 30  # hot tier: per-event lines only inside this window


def cutoff(source: str):
    days = LOOKBACK_DAYS.get(source, (90, ""))[0]
    return None if days is None else as_of() - timedelta(days=days)


TOKEN_BUDGET = {"buyer": 450, "seller": 500}
MAX_THREADS = 3
MAX_TIMELINE = 5
