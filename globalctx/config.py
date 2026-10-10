"""Central settings: data paths, lookback windows, size budget and Sarvam IDs."""
import os
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Raw organiser data (real customer data: local only, gitignored)
SELLER_DIR = ROOT / "Global Context - Seller Dataset"
BUYER_FILE = next(ROOT.glob("Global_Context_Problem_Statement_Buyer Side*.csv"), None)

DATA_DIR = ROOT / "data"                # gitignored
DB_PATH = DATA_DIR / "gc.db"
PROFILES_DIR = DATA_DIR / "profiles"    # profiles/<role>/<glid>.md
FRESHNESS_LOG = DATA_DIR / "freshness_log.csv"

# Reference "now" for lookback windows. Override with GC_AS_OF=2026-10-02 to replay.
_as_of = os.environ.get("GC_AS_OF")


def as_of() -> datetime:
    return datetime.fromisoformat(_as_of) if _as_of else datetime.now()


# Lookback window per source, in days (reasons are in APPROACH.md)
LOOKBACK_DAYS = {
    "enquiry": 90,
    "enquiry_message": 30,
    "buyer_call": 90,
    "buylead": 45,
    "whatsapp_bot": 30,
    "whatsapp_msg": 30,
    "vani_call": 90,
    "exec_call": 60,
    "pns": 90,
    "buyer_activity": 30,
    "session": 30,          # our own voice / chat sessions
    "past_need": 365,       # earlier requirements/enquiries (case files), for "same as last time?" prompts
    "contact": 3650,        # name / language set by the team
    "profile": 3650,        # snapshots: latest wins
}


def window_start(source: str) -> datetime:
    return as_of() - timedelta(days=LOOKBACK_DAYS.get(source, 90))


# Size budget
MAX_CHARS = 2500
MAX_ROW_CHARS = 120

# Rows per section, in render order
SECTION_CAPS = {
    "seller": {
        "Identity": 1,
        "Snapshot": 2,
        "Buyer Demand by Product": 4,   # top 3 products + totals
        "Responses & Calls": 2,
        "Past Conversations": 3,
        "Open Threads": 3,
        "Engagement Signals": 2,
        "Do-Not-Ask": 1,
    },
    "buyer": {
        "Identity": 1,
        "Snapshot": 2,
        "Buying Needs": 6,              # requirement + details + 2 more needs + earlier needs + totals
        "KYC": 1,
        "Past Conversations": 3,
        "Open Threads": 3,
        "Engagement Signals": 1,
        "Do-Not-Ask": 1,
    },
}

# Trimmed first when the file is over budget; sections not listed are never trimmed
TRIM_ORDER = [
    "Engagement Signals",
    "Past Conversations",
    "Responses & Calls",
    "Buyer Demand by Product",
    "Buying Needs",
]

# Requests raised on our channels -> one md per GLID per folder (data/requests/<folder>/<glid>.md)
REQUESTS_DIR = DATA_DIR / "requests"
REQUEST_FOLDERS = {"catalogue_update": "catalogue_updation_requests", "price_update": "catalogue_updation_requests",
                   "requirement": "requirements", "enquiry": "enquiry"}
# Which request types each role can raise (anything else is dropped, e.g. a complaint is not an enquiry)
REQUEST_TYPES_BY_ROLE = {"seller": {"catalogue_update", "price_update"}, "buyer": {"requirement", "enquiry"}}
# Role for a GLID with no file at all, until a conversation tells us otherwise
DEFAULT_NEW_ROLE = "buyer"

# Sarvam
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
NARRATIVE_MODEL = "sarvam-105b"
CHAT_MODEL = "sarvam-105b-conversations"
SARVAM_ORG_ID = os.environ.get("SARVAM_ORG_ID", "01a1109e-edab-7ccc-8190-40202cc096f0")
SARVAM_WORKSPACE_ID = os.environ.get("SARVAM_WORKSPACE_ID", "01a1109e-edb1-706f-9f4d-2cd321b5db98")
# Voice Agents platform key (apps.sarvam.ai, X-API-Key): create it in indus.sarvam.ai workspace settings
SARVAM_AGENTS_API_KEY = os.environ.get("SARVAM_AGENTS_API_KEY", "")
SARVAM_APP_ID = os.environ.get("SARVAM_APP_ID", "Conversatio-c49cd61c-ee22")  # team workspace agent

# Conversations in this build are teammates role-playing real GLIDs, so they are labelled synthetic.
# Set GC_ROLEPLAY=0 for real customer conversations.
ROLEPLAY = os.environ.get("GC_ROLEPLAY", "1") == "1"

# Openings come from rules (no LLM); the voice agent rephrases them naturally on the call.
# GC_OPENING=llm switches back to the LLM-written opening (costs one LLM call per changed GLID).
OPENING_MODE = os.environ.get("GC_OPENING", "template")

# Set GC_NO_LLM=1 to build files without calling Sarvam (template opening only)
NO_LLM = os.environ.get("GC_NO_LLM") == "1"
