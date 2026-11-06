import asyncio
import json
from types import SimpleNamespace

import pytest
from telegram.error import Conflict, NetworkError, RetryAfter

import bot
import config
import db
import llm
import scraper
from helpers import (ctx_for, make_app, make_callback, make_pending, make_project, make_update, proposal_ar, proposal_en, run)


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    real = asyncio.sleep

    async def fast(d, *a, **k):
        await real(0)
    monkeypatch.setattr(asyncio, "sleep", fast)


class FakeMonitor:
    def __init__(self, **kw):
        self.s = dict(paused=False, running=True, last_scan_ok=1_000_000.0, next_scan=1_000_060.0, interval=90.0,
                      failures=0, last_error=None, summary={}, gemini_paused_until=0)
        self.s.update(kw)

    def snapshot(self):
        return self.s


def keyboard(kw):
    return [[(b.text, b.callback_data or b.url) for b in row] for row in kw["reply_markup"].inline_keyboard]


def last_buttons(app):
    return [m for m in app.bot.sent if m.kw.get("reply_markup")][-1]


# ---------------------------------------------------------------- commands
def test_start_authorized_and_unauthorized_show_chat_id_and_work_immediately():
    app = make_app()
    u = make_update()
    run(bot.cmd_start(u, ctx_for(app)))
    text = u.message.replies[0][0]
    assert str(config.TG_CHAT_ID) in text and "NafPT" in text and "/status" in text and "شغّال" in text
    u2 = make_update(chat_id=777, user_id=777)
    run(bot.cmd_start(u2, ctx_for(app)))
    t2 = u2.message.replies[0][0]
    assert "777" in t2 and "TELEGRAM_CHAT_ID=777" in t2                # tells a wrong-ID user exactly what to configure


def test_status_reports_real_state_not_hardcoded():
    app = make_app()
    app.bot_data.update(health={"telegram": {"state": "ok", "detail": "متصل (@NafPTbot)"}, "database": {"state": "ok", "detail": "جاهزة"},
                                "gemini": {"state": "failed", "detail": "مفتاح غير صالح"}, "scraper": {"state": "ok", "detail": "جاهز"}},
                        monitor=FakeMonitor())
    u = make_update()
    run(bot.cmd_status(u, ctx_for(app)))
    t1 = u.message.replies[0][0]
    assert "@NafPTbot" in t1 and "❌ مفتاح غير صالح" in t1 and "✅ شغال" in t1 and "في انتظار موافقتك: 0" in t1
    make_pending(); make_pending(url="https://nafezly.com/project/2-x")
    db.incr_counter("gemini_calls", 5)
    app.bot_data["monitor"] = FakeMonitor(failures=3, last_error="boom", paused=False)
    u2 = make_update()
    run(bot.cmd_status(u2, ctx_for(app)))
    t2 = u2.message.replies[0][0]
    assert "في انتظار موافقتك: 2" in t2 and "5/" in t2 and "3 متتالية" in t2 and "boom" in t2
    app.bot_data["monitor"] = FakeMonitor(paused=True)
    u3 = make_update(); run(bot.cmd_status(u3, ctx_for(app)))
    assert "متوقف" in u3.message.replies[0][0]


def test_status_when_monitoring_not_started_explains_why():
    app = make_app()
    app.bot_data["health"] = {"monitoring_blocked": {"state": "failed", "detail": "TELEGRAM_CHAT_ID هو رقم البوت"}}
    u = make_update(); run(bot.cmd_status(u, ctx_for(app)))
    assert "غير مفعّل" in u.message.replies[0][0] and "رقم البوت" in u.message.replies[0][0]


def test_pause_resume_stats_help_retry():
    app = make_app()
    u = make_update(); run(bot.cmd_pause(u, ctx_for(app)))
    assert db.is_paused() and "اتوقفت" in u.message.replies[0][0]
    u = make_update(); run(bot.cmd_resume(u, ctx_for(app)))
    assert not db.is_paused()
    make_project("failed", url="https://nafezly.com/project/9-x", attempts=5)
    u = make_update(); run(bot.cmd_retry(u, ctx_for(app)))
    assert db.get_by_url("https://nafezly.com/project/9-x")["status"] == "discovered" and "1" in u.message.replies[0][0]
    pid = make_pending(); db.update(pid, outcome="won")
    u = make_update(); run(bot.cmd_stats(u, ctx_for(app)))
    assert "🏆 1" in u.message.replies[0][0]
    u = make_update(); run(bot.cmd_help(u, ctx_for(app)))
    assert "/pause" in u.message.replies[0][0] and "مش بيقدّم" in u.message.replies[0][0]


def test_unauthorized_users_get_a_visible_denial_and_no_effect():
    app = make_app()
    for cmd in (bot.cmd_status, bot.cmd_pause, bot.cmd_resume, bot.cmd_stats, bot.cmd_help, bot.cmd_retry):
        u = make_update(chat_id=42, user_id=42)
        run(cmd(u, ctx_for(app)))
        assert "غير مصرّح" in u.message.replies[0][0]
    assert not db.is_paused()
    t = make_update(text="ابعت", chat_id=42, user_id=42)
    run(bot.on_text(t, ctx_for(app)))
    assert "غير مصرّح" in t.message.replies[0][0]


def test_group_chats_are_never_authorized():
    assert not bot.is_authorized(make_update(chat_type="group"))
    assert bot.is_authorized(make_update())
    assert not bot.is_authorized(make_update(user_id=12345))


# ---------------------------------------------------------------- notifications
def test_notification_card_has_everything_and_tracks_delivery():
    pid = make_pending(score=9.2, budget="$500", offers=4, duration="10 أيام", skills='["C#","SQL Server"]')
    app = make_app()
    run(bot.Notifier(app).notify_pending(pid))
    msg = app.bot.sent[0]
    t = msg.text
    for needle in ("فرصة عمل جديدة", "9.2/10", "ممتازة", "$500", "C#, SQL Server", "ليه مناسب", "التقييم الفني", "المخاطر",
                   "العرض المقترح (v1)", "https://nafezly.com/project/", "السلام عليكم"):
        assert needle in t, needle
    assert msg.kw["parse_mode"] == "HTML" and msg.chat_id == config.TG_CHAT_ID
    kb = keyboard(msg.kw)
    assert [b[1] for b in kb[0] + kb[1]] == [f"ap:{pid}", f"rj:{pid}", f"rg:{pid}", f"ed:{pid}"] and kb[2][0][1].startswith("https://")
    p = db.get(pid)
    assert p["notify_status"] == "sent" and p["notified_at"] and app.bot_data["msg2pid"][1] == pid


def test_missing_budget_and_unknown_status_are_stated_not_invented():
    pid = make_pending(site_status="unknown", budget=None, offers=None)
    t = bot.format_card(db.get(pid), db.get_analysis(pid), db.current_proposal(pid))
    assert "غير متاحة" in t and "مش قادر أتأكد" in t


def test_long_proposal_is_split_with_buttons_only_on_last_chunk():
    pid = make_pending()
    db.add_proposal(pid, ("جملة طويلة جدا للاختبار " * 40 + "\n") * 12)
    app = make_app()
    n = run(bot.Notifier(app).notify_pending(pid))
    assert n > 1 and all(len(m.text) <= 4096 for m in app.bot.sent)
    assert [bool(m.kw.get("reply_markup")) for m in app.bot.sent] == [False] * (n - 1) + [True]


def test_split_message_properties():
    assert bot.split_message("abc") == ["abc"]
    chunks = bot.split_message("x" * 9000)
    assert all(len(c) <= 3800 for c in chunks) and "".join(chunks) == "x" * 9000
    text = "\n".join(f"line {i}" for i in range(2000))
    assert "\n".join(bot.split_message(text)) == text


def test_card_escapes_html_and_warns_about_policy_issues_and_stale_proposals():
    pid = make_pending(title="<img src=x onerror=1>", needs_review=1)
    db.add_proposal(pid, proposal_ar() + " كلمني على 01012345678")
    t = bot.format_card(db.get(pid), db.get_analysis(pid), db.current_proposal(pid))
    assert "<img" not in t and "⛔" in t and "اتعدّل بعد كتابة البروبوزال" in t


def test_delivery_failure_does_not_mark_sent_and_retry_after_is_honored():
    pid = make_pending()
    app = make_app()
    app.bot.fail = RuntimeError("chat not found")
    with pytest.raises(RuntimeError):
        run(bot.Notifier(app).notify_pending(pid))
    assert db.get(pid)["notify_status"] != "sent"
    calls = []

    class Flaky:
        async def send_message(self, chat_id, text, **kw):
            calls.append(1)
            if len(calls) < 3:
                raise (RetryAfter(1) if len(calls) == 1 else NetworkError("blip"))
            return SimpleNamespace(message_id=1)
    assert run(bot._send(Flaky(), "hi"))
    assert len(calls) == 3


# ---------------------------------------------------------------- approval workflow (buttons)
def press(app, data, **kw):
    upd = make_callback(data, **kw)
    run(bot.on_button(upd, ctx_for(app)))
    return upd.callback_query


def test_approve_never_submits_and_delivers_copyable_text():
    pid = make_pending()
    app = make_app()
    q = press(app, f"ap:{pid}")
    assert db.get(pid)["status"] == "approved" and not db.get(pid)["outcome"]
    assert "تمت الموافقة" in app.bot.sent[0].text and "مش بيقدّم عروض بنفسه" in app.bot.sent[0].text
    assert app.bot.sent[1].text == proposal_ar()                      # clean text for copy
    assert [b[1] for b in keyboard(app.bot.sent[0].kw)[0]] == [f"sb:{pid}", f"bk:{pid}"]
    assert db.actions(pid)[0]["action"] == "approved" and db.actions(pid)[0]["actor"] == str(config.TG_CHAT_ID)
    assert not any(hasattr(scraper.Nafezly, n) for n in ("submit", "submit_proposal"))
    press(app, f"ap:{pid}")                                           # double tap: rejected as invalid, nothing re-sent
    assert len(app.bot.sent) == 2


def test_manual_submit_confirmation_is_a_separate_step_and_outcome_tracking():
    pid = make_pending()
    app = make_app()
    q = press(app, f"sb:{pid}")
    assert db.get(pid)["status"] == "pending_approval" and q.answers[-1][1] is True     # can't submit before approving
    press(app, f"ap:{pid}")
    press(app, f"sb:{pid}")
    assert db.get(pid)["status"] == "submitted" and db.actions(pid)[-1]["action"] == "marked_submitted"
    press(app, f"out:{pid}:won")
    assert db.get(pid)["outcome"] == "won"


def test_back_to_review_and_reject():
    pid = make_pending()
    app = make_app()
    press(app, f"ap:{pid}")
    press(app, f"bk:{pid}")
    assert db.get(pid)["status"] == "pending_approval"
    press(app, f"rj:{pid}")
    p = db.get(pid)
    assert p["status"] == "rejected" and p["decided_by"] == "user" and db.actions(pid)[-1]["action"] == "rejected"
    q = press(app, f"rj:{pid}")                                        # already rejected
    assert q.answers[-1][1] is True


def test_regenerate_creates_new_version_and_resends(monkeypatch):
    pid = make_pending()
    app = make_app()
    seen = {}

    async def write(p, a, instructions=None, previous=None, language=None):
        seen.update(instr=instructions, prev=previous)
        return proposal_en(), {"errors": [], "warnings": []}
    monkeypatch.setattr(llm, "write_proposal", write)
    press(app, f"rg:{pid}")
    vs = db.proposal_versions(pid)
    assert [v["version"] for v in vs] == [1, 2] and vs[1]["source"] == "regenerated" and vs[1]["is_current"] == 1
    assert seen["prev"] == proposal_ar() and "(v2)" in app.bot.sent[-1].text and db.get(pid)["status"] == "pending_approval"
    assert db.actions(pid)[-1]["action"] == "regenerated"


def test_regenerate_failure_keeps_old_proposal_and_hides_secrets(monkeypatch):
    pid = make_pending()
    app = make_app()

    async def boom(*a, **k):
        raise llm.GeminiError("server", f"down key={config.GEMINI_KEY}")
    monkeypatch.setattr(llm, "write_proposal", boom)
    q = press(app, f"rg:{pid}")
    assert len(db.proposal_versions(pid)) == 1 and db.get(pid)["proposal"] == proposal_ar()
    assert config.GEMINI_KEY not in q.message.replies[-1][0] and "خطأ" in q.message.replies[-1][0]


def test_regeneration_is_not_run_twice_concurrently(monkeypatch):
    pid = make_pending()
    app = make_app()
    app.bot_data["busy"] = {pid}
    with pytest.raises(RuntimeError):
        run(bot.do_regenerate(app, pid, 1))


def test_edit_button_then_full_text_saves_user_version():
    pid = make_pending()
    app = make_app()
    press(app, f"ed:{pid}")
    assert app.bot_data["awaiting_edit"] == pid
    new = proposal_ar("تحياتي (نسخة المستخدم)")
    t = make_update(text=new)
    run(bot.on_text(t, ctx_for(app)))
    vs = db.proposal_versions(pid)
    assert vs[-1]["source"] == "user_edit" and vs[-1]["text"] == new and "awaiting_edit" not in app.bot_data
    assert db.actions(pid)[-1]["action"] == "edited" and "(v2)" in app.bot.sent[-1].text


def test_edit_button_then_short_instruction_uses_ai_revision(monkeypatch):
    pid = make_pending()
    app = make_app()
    got = {}

    async def write(p, a, instructions=None, previous=None, language=None):
        got["i"] = instructions
        return proposal_ar("تحياتي (مختصر)"), {"errors": [], "warnings": []}
    monkeypatch.setattr(llm, "write_proposal", write)
    press(app, f"ed:{pid}")
    run(bot.on_text(make_update(text="خليه أقصر"), ctx_for(app)))
    assert got["i"] == "خليه أقصر" and db.proposal_versions(pid)[-1]["source"] == "edited_ai"


def test_edit_not_allowed_outside_pending():
    pid = make_pending()
    db.update(pid, status="approved")
    q = press(make_app(), f"ed:{pid}")
    assert q.answers[-1][1] is True


def test_invalid_and_unknown_callbacks_are_rejected_safely():
    app = make_app()
    assert press(app, "ap:1;DROP TABLE").answers[-1][1] is True
    assert press(app, "ap:999999").answers[-1][1] is True
    assert press(app, f"ap:{make_pending()}", chat_id=5, user_id=5).answers[-1] == ("⛔ غير مصرّح", True)


# ---------------------------------------------------------------- free-text chat
def patch_interpret(monkeypatch, **res):
    async def fake(msg, project, proposal):
        return {"intent": "chat", "proposal": None, "reply": "تمام", **res}
    monkeypatch.setattr(llm, "interpret", fake)


def test_chat_approve_reject_and_plain_chat(monkeypatch):
    pid = make_pending()
    app = make_app()
    patch_interpret(monkeypatch, intent="chat", reply="مفيش مشكلة")
    u = make_update(text="إيه رأيك؟"); run(bot.on_text(u, ctx_for(app)))
    assert u.message.replies[0][0] == "مفيش مشكلة" and db.get(pid)["status"] == "pending_approval"
    patch_interpret(monkeypatch, intent="approve", reply="تمام هبعتلك النص")
    run(bot.on_text(make_update(text="ابعت"), ctx_for(app)))
    assert db.get(pid)["status"] == "approved"
    pid2 = make_pending(url="https://nafezly.com/project/2-x")
    patch_interpret(monkeypatch, intent="reject", reply="اتلغى")
    run(bot.on_text(make_update(text="لغي مش هقدر عليه"), ctx_for(app)))
    assert db.get(pid2)["status"] == "rejected"


def test_chat_edit_valid_and_policy_violating(monkeypatch):
    pid = make_pending()
    app = make_app()
    patch_interpret(monkeypatch, intent="edit", proposal=proposal_ar("تحياتي (معدّل)"), reply="عدّلت")
    run(bot.on_text(make_update(text="عدّل الخاتمة"), ctx_for(app)))
    assert db.proposal_versions(pid)[-1]["text"].endswith("(معدّل)")
    seen = {}

    async def write(p, a, instructions=None, previous=None, language=None):
        seen["i"] = instructions
        return proposal_ar("تحياتي (آمن)"), {"errors": [], "warnings": []}
    monkeypatch.setattr(llm, "write_proposal", write)
    patch_interpret(monkeypatch, intent="edit", proposal=proposal_ar() + " كلمني على 01012345678", reply="تمام")
    run(bot.on_text(make_update(text="حط رقمي في العرض"), ctx_for(app)))
    last = db.proposal_versions(pid)[-1]["text"]
    assert "01012345678" not in last and seen["i"] == "حط رقمي في العرض" and last.endswith("(آمن)")


def test_reply_to_a_specific_card_targets_that_project(monkeypatch):
    app = make_app()
    p1, p2 = make_pending(), make_pending(url="https://nafezly.com/project/2-x")
    run(bot.Notifier(app).notify_pending(p1))
    run(bot.Notifier(app).notify_pending(p2))
    first_msg_id = app.bot.sent[0].message_id
    patch_interpret(monkeypatch, intent="reject", reply="ok")
    u = make_update(text="لغي ده", reply_to=SimpleNamespace(message_id=first_msg_id))
    run(bot.on_text(u, ctx_for(app)))
    assert db.get(p1)["status"] == "rejected" and db.get(p2)["status"] == "pending_approval"


def test_text_with_nothing_pending_and_handler_errors_are_reported(monkeypatch):
    app = make_app()
    u = make_update(text="hi"); run(bot.on_text(u, ctx_for(app)))
    assert "مفيش بروبوزال" in u.message.replies[0][0]
    make_pending()

    async def boom(*a, **k):
        raise RuntimeError(f"fail {config.TG_TOKEN}")
    monkeypatch.setattr(llm, "interpret", boom)
    u2 = make_update(text="hi"); run(bot.on_text(u2, ctx_for(app)))
    assert "خطأ" in u2.message.replies[0][0] and config.TG_TOKEN not in u2.message.replies[0][0]


# ---------------------------------------------------------------- errors
def test_polling_conflict_is_reported_once():
    app = make_app()
    ctx = SimpleNamespace(application=app, error=Conflict("terminated by other getUpdates request"))
    run(bot.on_error(None, ctx))
    run(bot.on_error(None, ctx))
    assert len([m for m in app.bot.sent if "Conflict" in m.text]) == 1
    assert any(e["component"] == "telegram" for e in db.recent_errors())
