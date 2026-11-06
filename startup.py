"""Startup validation. Reports REAL component state; never claims success it did not verify."""
import logging

from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError, TelegramError

import config
import db
import llm
from logging_setup import safe_exc

log = logging.getLogger("startup")
COMPONENTS = ("database", "telegram", "gemini", "scraper")
ICON = {"ok": "✅", "degraded": "⚠️", "failed": "❌", "unknown": "⏳"}


def new_health():
    return {k: {"state": "unknown", "detail": ""} for k in COMPONENTS}


def _set(health, name, state, detail):
    health[name] = {"state": state, "detail": detail}


def diagnose_chat_id(bot_id, chat_id):
    """Human explanation when the configured chat id looks wrong (or None)."""
    if not chat_id:
        return "TELEGRAM_CHAT_ID مش متظبط: ابعت /start للبوت وخد رقمك وحطه في .env"
    if bot_id and chat_id == bot_id:
        return "TELEGRAM_CHAT_ID هو رقم البوت نفسه مش رقمك. ابعت /start للبوت وخد رقمك"
    if chat_id < 0:
        return "TELEGRAM_CHAT_ID سالب (جروب/قناة). NafPT بيشتغل في محادثة خاصة فقط"
    return None


def check_database(health):
    try:
        st = db.stats()
        _set(health, "database", "ok", f"جاهزة ({st['total']} مشروع محفوظ، schema v{db.SCHEMA_VERSION})")
        return True
    except Exception as e:
        _set(health, "database", "failed", safe_exc(e))
        return False


async def check_telegram(bot, health):
    """Validates the token and that it belongs to the expected bot. Returns the bot user or None."""
    try:
        me = await bot.get_me()
    except InvalidToken:
        _set(health, "telegram", "failed", "التوكن مرفوض من تليجرام (InvalidToken). انسخه تاني من @BotFather")
        return None
    except NetworkError as e:
        _set(health, "telegram", "failed", f"مفيش اتصال بتليجرام: {safe_exc(e, 120)}")
        return None
    except TelegramError as e:
        _set(health, "telegram", "failed", safe_exc(e, 160))
        return None
    if config.EXPECTED_BOT_USERNAME and (me.username or "").lower() != config.EXPECTED_BOT_USERNAME.lower():
        _set(health, "telegram", "failed",
             f"التوكن ده بتاع @{me.username} مش @{config.EXPECTED_BOT_USERNAME}. استخدم توكن @{config.EXPECTED_BOT_USERNAME} الصح")
        return me
    _set(health, "telegram", "ok", f"متصل (@{me.username})")
    return me


async def check_gemini(health):
    ok, msg = await llm.check_model()
    _set(health, "gemini", "ok" if ok else "failed", msg if ok else f"{msg} (الاكتشاف شغال لكن التحليل متوقف)")
    return ok


async def check_scraper(scraper, health):
    try:
        await scraper.start()
        _set(health, "scraper", "ok", f"جاهز (HTTP + BeautifulSoup) — {config.LIST_URL}")
        return True
    except Exception as e:
        _set(health, "scraper", "failed", safe_exc(e))
        return False


def startup_text(health, me, monitoring_note):
    lines = ["✅ NafPT is online. Monitoring initialization started."
             if health["telegram"]["state"] == "ok" else "⚠️ NafPT started with problems.",
             f"{config.APP_NAME} v{config.VERSION} — {config.APP_FULL_NAME}", ""]
    for name, label in (("telegram", "Telegram"), ("database", "Database"), ("gemini", "Gemini"), ("scraper", "Scraper")):
        h = health[name]
        lines.append(f"{ICON[h['state']]} {label}: {h['detail'] or h['state']}")
    lines += ["", monitoring_note]
    return "\n".join(lines)


async def send_startup_message(bot, text, bot_id=None):
    """Returns (ok, detail). Explains the most common chat-id failures instead of failing silently."""
    problem = diagnose_chat_id(bot_id, config.TG_CHAT_ID)
    if problem:
        return False, problem
    try:
        await bot.send_message(config.TG_CHAT_ID, text)
        return True, "تم إرسال رسالة البدء"
    except Forbidden as e:
        return False, f"تليجرام رفض الإرسال ({safe_exc(e, 100)}). افتح @{config.EXPECTED_BOT_USERNAME} وابعتله /start الأول"
    except BadRequest as e:
        return False, f"Chat ID غلط أو لسه ماعملتش /start ({safe_exc(e, 100)})"
    except TelegramError as e:
        return False, safe_exc(e, 160)
