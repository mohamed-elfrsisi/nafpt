"""NafPT entry point: validate -> lock -> start Telegram -> verify components -> start monitoring."""
import asyncio
import logging
import os
import sys

from telegram import Update
from telegram.error import InvalidToken, NetworkError, TimedOut
from telegram.ext import Application

import bot
import config
import db
import llm
import logging_setup
import startup
from logging_setup import safe_exc
from monitor import Monitor
from scraper import Nafezly

log = logging.getLogger("main")
EXIT_CONFIG, EXIT_LOCKED = 2, 3


def acquire_lock():
    """Single-instance guard (two instances polling the same token cause Telegram 'Conflict')."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = open(config.LOCK_FILE, "w")
    try:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        pass                                   # non-POSIX: best effort
    except OSError:
        f.close()
        return None
    f.write(str(os.getpid()))
    f.flush()
    return f


async def post_init(app: Application):
    health = startup.new_health()
    app.bot_data["health"] = health
    db_ok = startup.check_database(health)
    me = await startup.check_telegram(app.bot, health)
    gem_ok = await startup.check_gemini(health)
    scraper = Nafezly()
    scr_ok = await startup.check_scraper(scraper, health)
    app.bot_data["scraper"] = scraper

    blocked = None
    if not db_ok:
        blocked = "قاعدة البيانات مش شغّالة"
    elif me is None or health["telegram"]["state"] != "ok":
        blocked = health["telegram"]["detail"]
    elif not scr_ok:
        blocked = health["scraper"]["detail"]
    else:
        blocked = startup.diagnose_chat_id(me.id, config.TG_CHAT_ID)
    if blocked:
        health["monitoring_blocked"] = {"state": "failed", "detail": blocked}
        note = f"❌ Monitoring: مش شغّال — {blocked}"
    else:
        notifier = bot.Notifier(app)
        monitor = Monitor(scraper, notifier)
        app.bot_data["monitor"] = monitor
        app.bot_data["monitor_task"] = asyncio.create_task(monitor.run(), name="nafpt-monitor")
        note = ("⏳ Monitoring: بدأ التهيئة، هبعتلك تقرير أول فحص." if gem_ok else
                "⚠️ Monitoring: الاكتشاف شغّال لكن التحليل متوقف لحد ما Gemini يتظبط.")
    text = startup.startup_text(health, me, note)
    ok, detail = await startup.send_startup_message(app.bot, text, me.id if me else None)
    if not ok:
        health["telegram"] = {"state": "degraded" if health["telegram"]["state"] == "ok" else "failed", "detail": detail}
        log.error("startup message not delivered: %s", detail)
    log.info("startup complete: %s", {k: v["state"] for k, v in health.items() if "state" in v})


async def post_shutdown(app: Application):
    task = app.bot_data.get("monitor_task")
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    if app.bot_data.get("scraper"):
        await app.bot_data["scraper"].close()
    await llm.aclose()
    db.close()
    log.info("NafPT stopped cleanly")


def main():
    errors, warnings = config.validate()
    logging_setup.setup()
    for w in warnings:
        log.warning("config: %s", w)
    if errors:
        for e in errors:
            print(f"❌ {e}", file=sys.stderr)
        print("راجع ملف .env (انسخ .env.example وعدّله) ثم شغّل: python check_setup.py", file=sys.stderr)
        sys.exit(EXIT_CONFIG)
    lock = acquire_lock()
    if lock is None:
        print("❌ NafPT شغّال بالفعل (lock). وقّفه الأول: ./nafpt.sh stop", file=sys.stderr)
        sys.exit(EXIT_LOCKED)
    app = (Application.builder().token(config.TG_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build())
    bot.register(app)
    log.info("%s v%s starting (bot @%s expected)", config.APP_NAME, config.VERSION, config.EXPECTED_BOT_USERNAME)
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=5)
    except InvalidToken:
        log.error("تليجرام رفض التوكن (InvalidToken). انسخه تاني من @BotFather وشغّل: python check_setup.py")
        sys.exit(EXIT_CONFIG)                     # config problem: run_forever.sh will not loop on this
    except (NetworkError, TimedOut) as e:
        log.error("تليجرام مش متاح دلوقتي (%s). هتتعاد المحاولة تلقائيًا.", safe_exc(e))
        sys.exit(1)                               # run_forever.sh restarts with growing delay


if __name__ == "__main__":
    main()
