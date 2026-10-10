"""Settings. Every number here is a documented design choice."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.environ.get("YAAD_DATA_DIR", ROOT / "Global Context - Seller Dataset"))  # organiser CSVs (P3)
DB_PATH = Path(os.environ.get("YAAD_DB", ROOT / "data" / "yaad_v2.db"))

# Sarvam. Two different keys: the Voice Agents platform key (apps.sarvam.ai) and the model API key (api.sarvam.ai).
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
SARVAM_LLM_API_KEY = os.environ.get("SARVAM_LLM_API_KEY") or SARVAM_API_KEY
SARVAM_ORG_ID = os.environ.get("SARVAM_ORG_ID", "")
SARVAM_WORKSPACE_ID = os.environ.get("SARVAM_WORKSPACE_ID", "")
SARVAM_APP_ID = os.environ.get("SARVAM_APP_ID", "")
# An uncommitted agent is reachable only with its version pinned.
SARVAM_APP_VERSION = int(os.environ.get("SARVAM_APP_VERSION", "1")) or None
CHAT_MODEL = "sarvam-105b-conversations"
# Extraction runs with reasoning off (with reasoning on, sarvam-105b spent the token budget thinking).
EXTRACT_MODEL = os.environ.get("YAAD_EXTRACT_MODEL", "sarvam-105b-conversations")

# Thread mapping and ranking
SIBLING_DAYS = 7         # an event on a sibling mcat joins an existing thread if that thread was active this recently
COUNTERPARTY_DAYS = 7    # an event with no mcat joins the thread where the same counterparty appeared this recently
COMPLAINT_LOOKBACK_DAYS = 60  # "a seller I connected with last month" searches sellers connected in this window
FRESH_HOURS = 24         # activity this recent outranks older threads: the customer is in-market right now
STALE_DAYS = 30          # a thread untouched this long is stale (kept, ranked last)

# The context file
TOKEN_BUDGET = {"buyer": 450, "seller": 500}
MAX_THREADS = 3

# Lookback per organiser data source (applied by ingest, P3), with the reason (for the README and slide)
LOOKBACK_DAYS = {
    "bot_call": (180, "objections and dispositions stay relevant for months"),
    "enquiry": (90, "a buyer requirement older than a quarter is usually closed"),
    "pns": (90, "same horizon as enquiries; calls are the other half of a requirement"),
    "bl": (45, "the warehouse keeps only ~45 days of buy-lead purchases"),
    "whatsapp": (30, "chatbot intents (photo upload, callback) go stale within weeks"),
    "exec_call": (60, "a recent human touch changes what the bot should say"),
    "search": (30, "search intent is a short-lived buying signal"),
    "conversation": (None, "our own conversations are always the freshest truth"),
}
