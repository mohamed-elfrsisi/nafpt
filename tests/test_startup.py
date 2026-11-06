import asyncio
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError

import config
import llm
import main
import startup
from helpers import FakeBot, make_app, run

BOT_ID = config.bot_id_from_token()


def test_diagnose_chat_id():
    assert "مش متظبط" in startup.diagnose_chat_id(BOT_ID, 0)
    assert "رقم البوت" in startup.diagnose_chat_id(BOT_ID, BOT_ID)
    assert "جروب" in startup.diagnose_chat_id(BOT_ID, -100123)
    assert startup.diagnose_chat_id(BOT_ID, 555000111) is None


def test_token_validation_identity_and_failures():
    h = startup.new_health()
    me = run(startup.check_telegram(FakeBot("NafPTbot"), h))
    assert me.username == "NafPTbot" and h["telegram"]["state"] == "ok" and "@NafPTbot" in h["telegram"]["detail"]
    h = startup.new_health()
    run(startup.check_telegram(FakeBot("SomeOtherBot"), h))
    assert h["telegram"]["state"] == "failed" and "@SomeOtherBot" in h["telegram"]["detail"] and "@NafPTbot" in h["telegram"]["detail"]

    class Bad:
        def __init__(self, exc): self.exc = exc
        async def get_me(self): raise self.exc
    for exc, needle in ((InvalidToken("bad"), "مرفوض"), (NetworkError("no route"), "اتصال")):
        h = startup.new_health()
        assert run(startup.check_telegram(Bad(exc), h)) is None
        assert h["telegram"]["state"] == "failed" and needle in h["telegram"]["detail"]


def test_startup_message_delivery_diagnoses_each_failure_mode():
    bot = FakeBot()
    ok, _ = run(startup.send_startup_message(bot, "hi", BOT_ID))
    assert ok and bot.sent[0].text == "hi"
    bot.fail = Forbidden("bot can't initiate conversation with a user")
    ok, d = run(startup.send_startup_message(bot, "hi", BOT_ID))
    assert not ok and "/start" in d
    bot.fail = BadRequest("Chat not found")
    ok, d = run(startup.send_startup_message(bot, "hi", BOT_ID))
    assert not ok and "Chat ID غلط" in d
    ok, d = run(startup.send_startup_message(FakeBot(), "hi", 555000111))     # chat id == bot id
    assert not ok and "رقم البوت" in d


def test_startup_text_is_honest():
    h = startup.new_health()
    for k in ("database", "telegram", "gemini", "scraper"):
        h[k] = {"state": "ok", "detail": "تمام"}
    t = startup.startup_text(h, None, "⏳ Monitoring: بدأ")
    assert t.startswith("✅ NafPT is online. Monitoring initialization started.") and "✅ Gemini" in t
    h["gemini"] = {"state": "failed", "detail": "مفتاح غلط"}
    h["telegram"] = {"state": "failed", "detail": "توكن مرفوض"}
    t2 = startup.startup_text(h, None, "❌ Monitoring: مش شغّال")
    assert "NafPT is online" not in t2 and "❌ Gemini: مفتاح غلط" in t2 and "problems" in t2


def test_config_validation(monkeypatch):
    errors, warnings = config.validate()
    assert errors == []
    monkeypatch.setattr(config, "TG_TOKEN", "")
    assert any("TELEGRAM_BOT_TOKEN" in e for e in config.validate()[0])
    monkeypatch.setattr(config, "TG_TOKEN", "not-a-token")
    assert any("شكله غلط" in e for e in config.validate()[0])
    monkeypatch.setattr(config, "TG_TOKEN", "123456789:AAH_fake_token_for_tests_0123456789abcdef")
    monkeypatch.setattr(config, "GEMINI_KEY", "")
    assert any("GEMINI_API_KEY" in e for e in config.validate()[0])
    monkeypatch.setattr(config, "GEMINI_KEY", "k")
    monkeypatch.setattr(config, "TG_CHAT_ID", 0)
    monkeypatch.setattr(config, "TG_CHAT_ID_RAW", "0")
    assert any("TELEGRAM_CHAT_ID" in w for w in config.validate()[1])
    monkeypatch.setattr(config, "TG_CHAT_ID_RAW", "12abc")
    assert any("رقم فقط" in e for e in config.validate()[0])


def test_single_instance_lock():
    first = main.acquire_lock()
    assert first is not None
    assert main.acquire_lock() is None                 # second instance refused
    first.close()
    again = main.acquire_lock()
    assert again is not None
    again.close()


class StubMonitor:
    def __init__(self, scraper, notifier):
        pass

    async def run(self):
        await asyncio.sleep(3600)


class StubScraper:
    fail = False

    async def start(self):
        if StubScraper.fail:
            raise RuntimeError("cannot init")

    async def close(self):
        pass


@pytest.fixture
def boot(monkeypatch):
    monkeypatch.setattr(main, "Monitor", StubMonitor)
    monkeypatch.setattr(main, "Nafezly", StubScraper)
    StubScraper.fail = False

    async def ok_model():
        return True, "الموديل gemini متاح"
    monkeypatch.setattr(llm, "check_model", ok_model)


def post_init(app):
    run(main.post_init(app))
    return app


def test_startup_all_components_ok(boot):
    app = post_init(make_app())
    text = app.bot.sent[0].text
    assert "NafPT is online. Monitoring initialization started." in text and app.bot.sent[0].chat_id == config.TG_CHAT_ID
    for label in ("Telegram", "Database", "Gemini", "Scraper"):
        assert f"✅ {label}" in text
    assert "monitor" in app.bot_data and "هبعتلك تقرير أول فحص" in text


def test_gemini_failure_is_reported_and_not_claimed_as_working(boot, monkeypatch):
    async def bad():
        return False, "مفتاح/صلاحية Gemini غير صالحة"
    monkeypatch.setattr(llm, "check_model", bad)
    app = post_init(make_app())
    text = app.bot.sent[0].text
    assert "❌ Gemini" in text and "التحليل متوقف" in text and "✅ Gemini" not in text
    assert "هبعتلك تقرير" not in text and app.bot_data["health"]["gemini"]["state"] == "failed"


def test_scraper_failure_blocks_monitoring_and_says_so(boot):
    StubScraper.fail = True
    app = post_init(make_app())
    text = app.bot.sent[0].text
    assert "monitor" not in app.bot_data and "❌ Scraper" in text and "Monitoring: مش شغّال" in text


def test_wrong_chat_id_blocks_monitoring_with_clear_diagnosis(boot, monkeypatch):
    monkeypatch.setattr(config, "TG_CHAT_ID", BOT_ID)
    app = post_init(make_app())
    assert "monitor" not in app.bot_data and app.bot.sent == []             # nobody to message: logged, not faked
    assert "رقم البوت" in app.bot_data["health"]["monitoring_blocked"]["detail"]
    assert app.bot_data["health"]["telegram"]["state"] == "degraded"


def test_wrong_bot_token_identity_blocks_monitoring(boot):
    app = make_app()
    app.bot = FakeBot("AnotherBot", 999999)
    post_init(app)
    assert "monitor" not in app.bot_data and app.bot_data["health"]["telegram"]["state"] == "failed"
    assert "NafPT is online" not in app.bot.sent[0].text


def test_shutdown_is_clean(boot):
    app = post_init(make_app())
    run(main.post_shutdown(app))
