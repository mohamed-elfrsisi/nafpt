"""NafPT configuration. Never raises on import; call validate() at startup."""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

APP_NAME = "NafPT"
APP_FULL_NAME = "Nafezly Proposal Telegram"
VERSION = "1.0.0"


def _env(name, default=""):
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else v.strip()


def _int(name, default):
    try:
        return int(float(_env(name, str(default))))
    except ValueError:
        return default


def _float(name, default):
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _bool(name, default=False):
    return _env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


# ---------------- Telegram ----------------
TG_TOKEN = _env("TELEGRAM_BOT_TOKEN")
_chat_raw = _env("TELEGRAM_CHAT_ID", "0")
TG_CHAT_ID_RAW = _chat_raw
TG_CHAT_ID = int(_chat_raw) if re.fullmatch(r"-?\d+", _chat_raw) else 0
EXPECTED_BOT_USERNAME = _env("EXPECTED_BOT_USERNAME", "NafPTbot").lstrip("@")

# ---------------- Gemini ----------------
GEMINI_KEY = _env("GEMINI_API_KEY")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_TIMEOUT = _int("GEMINI_TIMEOUT", 60)
GEMINI_RETRIES = _int("GEMINI_RETRIES", 4)
GEMINI_DAILY_LIMIT = _int("GEMINI_DAILY_LIMIT", 250)  # عدد الطلبات اليومي المسموح للبوت (حماية الحصة المجانية)

# ---------------- Nafezly ----------------
LIST_URL = _env("NAFEZLY_LIST_URL", "https://nafezly.com/projects")
LINK_REGEX = _env("NAFEZLY_LINK_REGEX", r"""href=["']([^"']*?/project/[^"']+)["']""")
DETAIL_SELECTOR = _env("NAFEZLY_DETAIL_SELECTOR", "main")
MAX_PAGES = _int("MAX_PAGES", 3)
REQUEST_DELAY = _float("REQUEST_DELAY", 1.5)       # أقل فاصل بين طلبين للموقع (ثواني)
HTTP_RETRIES = _int("HTTP_RETRIES", 3)
HTTP_TIMEOUT = _int("HTTP_TIMEOUT", 20)
USER_AGENT = _env("USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---------------- Scoring thresholds (قابلة للتعديل) ----------------
MIN_SCORE = _float("MIN_SCORE", _float("MID_SCORE", 4.0))          # أقل درجة لتوليد بروبوزال
STRONG_SCORE = _float("STRONG_SCORE", _float("HIGH_SCORE", 7.0))
EXCELLENT_SCORE = _float("EXCELLENT_SCORE", 9.0)
ENABLE_WORDPRESS = _bool("ENABLE_WORDPRESS", False)
ENABLE_MOBILE = _bool("ENABLE_MOBILE", False)

# ---------------- Monitoring ----------------
POLL_ACTIVE = _int("POLL_ACTIVE", 90)
POLL_QUIET = _int("POLL_QUIET", 300)
ACTIVE_FROM = _int("ACTIVE_FROM", 10)
ACTIVE_TO = _int("ACTIVE_TO", 24)
MAX_DETAIL_FETCH_PER_TICK = _int("MAX_DETAIL_FETCH_PER_TICK", 15)
MAX_PROCESS_PER_TICK = _int("MAX_PROCESS_PER_TICK", 3)
MAX_NOTIFY_PER_TICK = _int("MAX_NOTIFY_PER_TICK", 3)
RECHECK_HOURS = _float("RECHECK_HOURS", 6.0)
RECHECK_PER_TICK = _int("RECHECK_PER_TICK", 5)
MAX_ATTEMPTS = _int("MAX_ATTEMPTS", 5)
MAX_NOTIFY_ATTEMPTS = _int("MAX_NOTIFY_ATTEMPTS", 5)
ALERT_AFTER_FAILURES = _int("ALERT_AFTER_FAILURES", 5)

# ---------------- Proposal validation ----------------
MIN_WORDS = _int("MIN_WORDS", 80)
MAX_WORDS = _int("MAX_WORDS", 260)
MAX_REWRITES = _int("MAX_REWRITES", 2)

# ---------------- Paths ----------------
DATA_DIR = Path(_env("NAFPT_DATA_DIR", str(BASE / "data")))
DB_PATH = Path(_env("NAFPT_DB", str(DATA_DIR / "bot.db")))
BACKUP_DIR = DATA_DIR / "backups"
LOG_DIR = DATA_DIR / "logs"
LOCK_FILE = DATA_DIR / "nafpt.lock"
HEARTBEAT = DATA_DIR / "heartbeat"
LOG_LEVEL = _env("LOG_LEVEL", "INFO").upper()


def profile_text():
    p = BASE / "profile.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def policy_text():
    p = BASE / "policies.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def bot_id_from_token(token=None):
    token = token if token is not None else TG_TOKEN
    m = re.match(r"^(\d+):", token or "")
    return int(m.group(1)) if m else None


def validate():
    """Returns (errors, warnings). errors = can't run at all."""
    errors, warnings = [], []
    if not TG_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN مش موجود في .env")
    elif not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", TG_TOKEN):
        errors.append("TELEGRAM_BOT_TOKEN شكله غلط (المفروض أرقام ثم : ثم حروف). انسخه تاني من @BotFather")
    if not GEMINI_KEY:
        errors.append("GEMINI_API_KEY مش موجود في .env")
    if TG_CHAT_ID_RAW not in ("", "0") and not re.fullmatch(r"-?\d+", TG_CHAT_ID_RAW):
        errors.append("TELEGRAM_CHAT_ID لازم يكون رقم فقط")
    if not TG_CHAT_ID:
        warnings.append("TELEGRAM_CHAT_ID مش متظبط: ابعت /start للبوت وخد الرقم، المراقبة هتفضل واقفة لحد ما تحطه")
    bid = bot_id_from_token()
    if bid and TG_CHAT_ID == bid:
        errors.append("TELEGRAM_CHAT_ID هو نفسه رقم البوت (ID) مش رقمك. ابعت /start للبوت وخد رقمك")
    prof = profile_text()
    if not prof.strip():
        errors.append("profile.md فاضي أو مش موجود")
    elif "(اكتب" in prof or "(مثال" in prof:
        warnings.append("profile.md لسه فيه حقول placeholder")
    if not policy_text().strip():
        warnings.append("policies.md فاضي")
    return errors, warnings
