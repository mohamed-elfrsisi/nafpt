"""NafPT Telegram interface. The owner approves/rejects/edits; NOTHING is ever submitted to Nafezly by the bot."""
import asyncio
import html
import json
import logging
import re
import time

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, LinkPreviewOptions, Update
from telegram.error import BadRequest, Conflict, NetworkError, RetryAfter
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import config
import db
import llm
import validator
from logging_setup import safe_exc
from profile_data import CATEGORY_LABELS

log = logging.getLogger("bot")
esc = html.escape
CALLBACK_RE = re.compile(r"^(ap|rj|rg|ed|sb|bk|out):(\d{1,12})(?::(won|silent|lost))?$")
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

HELP_TEXT = (
    "<b>الأوامر</b>\n/start — تعريف + الـ Chat ID\n/status — حالة النظام الفعلية\n/pause — إيقاف المراقبة\n"
    "/resume — استئناف المراقبة\n/stats — إحصائيات\n/retry — إعادة المشاريع الفاشلة\n/help — المساعدة\n\n"
    "<b>الاستخدام</b>\nكل فرصة بتوصلك ببطاقة فيها التحليل والبروبوزال وأزرار: موافقة / رفض / إعادة توليد / تعديل.\n"
    "تقدر كمان تكتب بالكلام (مصري/عربي/إنجليزي): «خليه أقصر»، «ابعت»، «لغي».\n"
    "⚠️ NafPT مش بيقدّم أي عرض على نافذلي بنفسه أبدًا. الموافقة بتسلّمك النص جاهز للنسخ.")


# ------------------------------------------------------------------ helpers
def is_authorized(update):
    chat, user = update.effective_chat, update.effective_user
    if not config.TG_CHAT_ID or chat is None or chat.id != config.TG_CHAT_ID:
        return False
    if getattr(chat, "type", "private") != "private":
        return False
    return user is None or user.id == config.TG_CHAT_ID


def split_message(text, limit=3800):
    chunks, cur = [], ""
    for para in text.split("\n"):
        while len(para) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(para[:limit])
            para = para[limit:]
        if len(cur) + len(para) + 1 > limit:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    return chunks or [""]


def score_label(s):
    s = s or 0
    return ("ممتازة" if s >= config.EXCELLENT_SCORE else "قوية" if s >= config.STRONG_SCORE
            else "محتملة" if s >= config.MIN_SCORE else "ضعيفة")


def _safe_url(url):
    return url if url and re.match(r"^https?://", url) else None


def kb_pending(p):
    rows = [[B("✅ موافقة", callback_data=f"ap:{p['id']}"), B("❌ رفض", callback_data=f"rj:{p['id']}")],
            [B("🔄 إعادة توليد", callback_data=f"rg:{p['id']}"), B("✏️ تعديل", callback_data=f"ed:{p['id']}")]]
    if _safe_url(p.get("url")):
        rows.append([B("🔗 فتح المشروع", url=p["url"])])
    return M(rows)


def kb_approved(p):
    rows = [[B("✅ قدّمت يدويًا", callback_data=f"sb:{p['id']}"), B("↩️ رجوع للمراجعة", callback_data=f"bk:{p['id']}")]]
    if _safe_url(p.get("url")):
        rows.append([B("🔗 فتح المشروع", url=p["url"])])
    return M(rows)


def kb_outcome(pid):
    return M([[B("🏆 اتقبل", callback_data=f"out:{pid}:won"), B("😶 ما ردش", callback_data=f"out:{pid}:silent"),
               B("🚫 اترفض", callback_data=f"out:{pid}:lost")]])


def _bullets(items):
    return "\n".join(f"• {esc(i)}" for i in items) if items else "—"


def format_card(p, a, prop):
    s = p.get("score") or 0
    icon = "🔥" if s >= config.STRONG_SCORE else "🟡"
    cats = " · ".join(CATEGORY_LABELS.get(c, c) for c in (a.get("categories") or [])) or "—"
    skills = ", ".join(json.loads(p["skills"])) if p.get("skills") else ""
    site = {"open": "مفتوح ✅", "closed": "مقفول 🔒", "gone": "اتحذف 🔒"}.get(
        p.get("site_status"), "غير معروفة ⚠️ (مش قادر أتأكد إنه لسه بيستقبل عروض)")
    sc = a.get("scores", {})
    head = (
        f"{icon} <b>فرصة عمل جديدة — NEW FREELANCE OPPORTUNITY</b>\n\n"
        f"<b>المشروع:</b> {esc(p.get('title') or p['url'])}\n<b>التخصص:</b> {esc(cats)}\n"
        f"<b>درجة التوافق:</b> {s:.1f}/10 ({score_label(s)})\n"
        f"<b>الميزانية:</b> {esc(p.get('budget') or 'غير متاحة')}\n<b>المدة:</b> {esc(p.get('duration') or 'غير متاحة')}\n"
        f"<b>العروض الحالية:</b> {p['offers'] if p.get('offers') is not None else 'غير متاحة'}\n"
        f"<b>حالة المشروع:</b> {site}\n<b>المهارات المطلوبة:</b> {esc(skills or ', '.join(a.get('required_skills', [])) or 'غير متاحة')}\n\n"
        f"<b>ليه مناسب:</b>\n{esc(a.get('score_explanation') or '—')}\n\n"
        f"<b>التقييم الفني:</b>\n{esc(a.get('technical_assessment') or '—')}\n\n"
        f"<b>المخاطر:</b>\n{_bullets(a.get('risks'))}\n\n<b>معلومات ناقصة:</b>\n{_bullets(a.get('missing_information'))}\n\n"
        f"<b>تفاصيل الدرجة:</b> مهارات {sc.get('technical_skill_match', 0):.0f} · خبرة {sc.get('experience_similarity', 0):.0f} · "
        f"تنفيذ {sc.get('feasibility', 0):.0f} · تجاري {sc.get('commercial_attractiveness', 0):.0f}\n")
    if p.get("needs_review"):
        head += "\n⚠️ <b>المشروع اتعدّل بعد كتابة البروبوزال، راجعه.</b>\n"
    errors, warns = validator.validate(prop["text"])
    note = ""
    if errors:
        note += "\n⛔ مخالفات محتملة: " + esc("، ".join(errors))
    if warns:
        note += "\n⚠️ تنبيه: " + esc("، ".join(warns))
    return (f"{head}\n<b>✍️ العرض المقترح (v{prop['version']})</b>\n{esc(prop['text'])}{note}\n\n"
            f"<b>رابط المشروع:</b>\n{esc(p['url'])}")


def ago(ts, now=None):
    if not ts:
        return "لسه ما حصلش"
    d = int((now or time.time()) - ts)
    return f"من {d} ثانية" if d < 120 else f"من {d // 60} دقيقة" if d < 7200 else f"من {d // 3600} ساعة"


def build_status(app, now=None):
    now = now or time.time()
    bd = app.bot_data
    health = bd.get("health", {})
    mon = bd.get("monitor")
    st = db.stats()
    icon = {"ok": "✅", "degraded": "⚠️", "failed": "❌", "unknown": "⏳"}

    def h(name):
        x = health.get(name, {"state": "unknown", "detail": ""})
        return f"{icon.get(x['state'], '⏳')} {esc(x['detail'] or x['state'])}"

    if mon is None:
        mline = "❌ غير مفعّل: " + esc(health.get("monitoring_blocked", {}).get("detail", "لسه بيتجهّز"))
        nxt = interval = last_err = ""
        last_ok = None
    else:
        s = mon.snapshot()
        last_ok = s["last_scan_ok"]
        if s["paused"]:
            mline = "⏸ متوقف (/resume)"
        elif not s["running"]:
            mline = "❌ الحلقة واقفة"
        elif s["failures"]:
            mline = f"⚠️ شغال بأخطاء ({s['failures']} متتالية)"
        elif last_ok is None:
            mline = "⏳ شغال، لسه ما خلصش أول فحص"
        else:
            mline = "✅ شغال"
        nxt = f"\n<b>الفحص الجاي:</b> بعد {max(0, int(s['next_scan'] - now))} ثانية" if s["next_scan"] and not s["paused"] else ""
        interval = f"\n<b>الفاصل الحالي:</b> {int(s['interval'])} ثانية" if s["interval"] else ""
        last_err = f"\n<b>آخر خطأ:</b> {esc(s['last_error'])}" if s["last_error"] else ""
    c = st["counts"]
    gp = float(db.get_setting("gemini_paused_until", 0) or 0)
    gnote = f" — متوقف لحد {time.strftime('%H:%M', time.localtime(gp))}" if gp > now else ""
    return (
        f"<b>{config.APP_NAME} — الحالة</b>\n\n<b>Telegram:</b> {h('telegram')}\n<b>Database:</b> {h('database')}\n"
        f"<b>Gemini:</b> {h('gemini')} (طلبات النهارده {st['gemini_calls_today']}/{config.GEMINI_DAILY_LIMIT}{gnote})\n"
        f"<b>Scraper:</b> {h('scraper')}\n<b>Monitoring:</b> {mline}\n"
        f"<b>آخر فحص ناجح:</b> {ago(last_ok, now)}{nxt}{interval}{last_err}\n\n"
        f"<b>المشاريع:</b> اتكتشف {st['total']} · اتحلل {st['analyzed']} · بروبوزالات {st['proposals']}\n"
        f"• في انتظار موافقتك: {c.get('pending_approval', 0)} · موافق عليها: {c.get('approved', 0)} · اتقدّمت: {c.get('submitted', 0)}\n"
        f"• مرفوضة: {c.get('rejected', 0)} · مقفولة: {c.get('closed', 0)} · في الطابور: {c.get('discovered', 0) + c.get('analyzed', 0)}\n"
        f"<b>عمليات فاشلة:</b> مشاريع فاشلة {st['failed_ops']} · إشعارات فاشلة {st['notify_failed']} · أخطاء 24 ساعة {db.error_count(now - 86400)}")


# ------------------------------------------------------------------ delivery
async def _send(bot, text, **kw):
    for attempt in range(3):
        try:
            return await bot.send_message(config.TG_CHAT_ID, text, link_preview_options=NO_PREVIEW, **kw)
        except RetryAfter as e:
            ra = e.retry_after
            ra = ra.total_seconds() if hasattr(ra, "total_seconds") else ra     # PTB >=22.2 may use timedelta
            await asyncio.sleep(min(30, ra))
        except NetworkError:
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
    raise NetworkError("send failed after retries")


class Notifier:
    def __init__(self, app):
        self.app = app

    def _remember(self, msg, pid):
        m = self.app.bot_data.setdefault("msg2pid", {})
        m[msg.message_id] = pid
        if len(m) > 500:
            for k in sorted(m)[:100]:
                m.pop(k, None)

    async def notify_pending(self, pid):
        p, a, prop = db.get(pid), db.get_analysis(pid) or {}, db.current_proposal(pid)
        if not p or not prop:
            raise RuntimeError(f"project {pid} has no proposal to send")
        chunks = split_message(format_card(p, a, prop))
        for i, ch in enumerate(chunks):
            last = i == len(chunks) - 1
            msg = await _send(self.app.bot, ch, parse_mode="HTML", reply_markup=kb_pending(p) if last else None)
            self._remember(msg, pid)
        db.update(pid, notify_status="sent", notified_at=time.time(), notify_attempts=(p["notify_attempts"] or 0) + 1)
        return len(chunks)

    async def notify_event(self, text):
        await _send(self.app.bot, text[:4000])

    async def alert(self, text):
        if not config.TG_CHAT_ID:
            return
        try:
            await _send(self.app.bot, text[:4000])
        except Exception as e:
            log.warning("alert delivery failed: %s", safe_exc(e))

    async def first_scan_report(self, s):
        await self.alert(
            f"🔎 <b>أول فحص خلص</b>\nصفحات اتفحصت: {s['pages']} (ترقيم صفحات: {'مكتشف' if s['pagination'] else 'مش مكتشف'})\n"
            f"مشاريع ظاهرة: {s['listed']} · جديدة في قاعدة البيانات: {s['new']}\n"
            f"في الطابور للتحليل: {s['backlog']} — بتتعالج على دفعات ({config.MAX_PROCESS_PER_TICK} كل دورة) "
            "وبتوصلك الفرص المناسبة تباعًا.".replace("<b>", "").replace("</b>", ""))


# ------------------------------------------------------------------ actions (shared by buttons and chat)
def _busy(app):
    return app.bot_data.setdefault("busy", set())


async def do_approve(app, pid, actor):
    p = db.get(pid)
    if p["status"] != "pending_approval":
        raise db.InvalidTransition(f"الحالة الحالية {p['status']}")
    prop = db.current_proposal(pid)
    db.transition(pid, "approved")
    db.log_action(pid, "approved", prop["id"] if prop else None, actor)
    await _send(app.bot, "✅ <b>تمت الموافقة</b>\nNafPT مش بيقدّم عروض بنفسه: انسخ النص ده وقدّمه على الموقع بإيدك، وبعدين اضغط «قدّمت يدويًا».\n"
                f"{esc(p['url'])}", parse_mode="HTML", reply_markup=kb_approved(p))
    for ch in split_message(prop["text"]):
        await _send(app.bot, ch)


async def do_reject(app, pid, actor):
    p = db.get(pid)
    if p["status"] not in ("pending_approval", "approved"):
        raise db.InvalidTransition(f"الحالة الحالية {p['status']}")
    db.transition(pid, "rejected", decided_by="user", reject_reason="رفضه صاحب الحساب")
    db.log_action(pid, "rejected", actor=actor)


async def do_regenerate(app, pid, actor, instructions=None, source="regenerated"):
    if pid in _busy(app):
        raise RuntimeError("البروبوزال بيتجهّز دلوقتي")
    _busy(app).add(pid)
    try:
        p, a, cur = db.get(pid), db.get_analysis(pid) or {}, db.current_proposal(pid)
        text, validation = await llm.write_proposal(
            p, a, instructions=instructions or "Write a clearly different version.", previous=cur["text"] if cur else None)
        prop_id = db.add_proposal(pid, text, source=source, model=config.GEMINI_MODEL, validation=validation)
        db.log_action(pid, "regenerated" if source == "regenerated" else "edited", prop_id, actor)
        db.update(pid, needs_review=0)
    finally:
        _busy(app).discard(pid)
    await Notifier(app).notify_pending(pid)


async def do_set_text(app, pid, actor, text):
    prop_id = db.add_proposal(pid, text.strip(), source="user_edit")
    db.log_action(pid, "edited", prop_id, actor, note="manual text")
    db.update(pid, needs_review=0)
    await Notifier(app).notify_pending(pid)


# ------------------------------------------------------------------ handlers
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    ok = is_authorized(update)
    extra = ("\n✅ أنت المستخدم المصرّح له." if ok else
             f"\n⚠️ الرقم ده مش هو المضبوط في TELEGRAM_CHAT_ID. حطه في ملف .env وأعد تشغيل NafPT:\n<code>TELEGRAM_CHAT_ID={cid}</code>")
    await update.message.reply_text(
        f"👋 أهلًا بيك في <b>{config.APP_NAME}</b> — {config.APP_FULL_NAME}\nأنا بدوّر على مشاريع نافذلي المناسبة لمهاراتك، "
        f"وبحلّلها وبكتبلك بروبوزال جاهز للمراجعة. إنت اللي بتوافق، وأنا مش بقدّم أي حاجة لوحدي.\n\n"
        f"<b>Chat ID بتاعك:</b> <code>{cid}</code>{extra}\n\n{HELP_TEXT}\n\n✅ البوت شغّال ويرد عليك.", parse_mode="HTML")


async def _deny(update):
    log.warning("unauthorized access attempt chat=%s user=%s", getattr(update.effective_chat, "id", None),
                getattr(update.effective_user, "id", None))
    if update.message:
        await update.message.reply_text("⛔ غير مصرّح. ابعت /start عشان تشوف الـ Chat ID وتظبطه في .env.")


async def cmd_help(update, ctx):
    if not is_authorized(update):
        return await _deny(update)
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")


async def cmd_status(update, ctx):
    if not is_authorized(update):
        return await _deny(update)
    await update.message.reply_text(build_status(ctx.application), parse_mode="HTML")


async def cmd_pause(update, ctx):
    if not is_authorized(update):
        return await _deny(update)
    db.set_setting("paused", "1")
    await update.message.reply_text("⏸ المراقبة اتوقفت بأمان (الحالة محفوظة). /resume عشان تكمّل.")


async def cmd_resume(update, ctx):
    if not is_authorized(update):
        return await _deny(update)
    db.set_setting("paused", "0")
    await update.message.reply_text("▶️ المراقبة كملت.")


async def cmd_stats(update, ctx):
    if not is_authorized(update):
        return await _deny(update)
    s = db.stats()
    o = s["outcomes"]
    decided = sum(o.values())
    rate = f"{o.get('won', 0) * 100 // decided}%" if decided else "—"
    c = s["counts"]
    await update.message.reply_text(
        f"📊 <b>إحصائيات NafPT</b>\nمشاريع: {s['total']} · اتحللت: {s['analyzed']} · بروبوزالات: {s['proposals']} "
        f"({s['proposal_versions']} نسخة)\nإشعارات اتبعتت: {s['notified']}\n"
        f"في الانتظار: {c.get('pending_approval', 0)} · موافق: {c.get('approved', 0)} · اتقدّمت: {c.get('submitted', 0)} · "
        f"مرفوضة: {c.get('rejected', 0)}\nالنتايج: 🏆 {o.get('won', 0)} · 😶 {o.get('silent', 0)} · 🚫 {o.get('lost', 0)} (نسبة القبول {rate})\n"
        f"Gemini النهارده: {s['gemini_calls_today']} طلب ({s['gemini_errors_today']} خطأ)", parse_mode="HTML")


async def cmd_retry(update, ctx):
    if not is_authorized(update):
        return await _deny(update)
    n = 0
    for p in db.by_status("failed", 500):
        db.transition(p["id"], "discovered", attempts=0, next_retry=None, last_error=None)
        n += 1
    await update.message.reply_text(f"🔁 رجّعت {n} مشروع فاشل للطابور." if n else "مفيش مشاريع فاشلة.")


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q is None:
        return
    chat = update.effective_chat
    user = update.effective_user
    authorized = (config.TG_CHAT_ID and chat is not None and chat.id == config.TG_CHAT_ID
                  and user is not None and user.id == config.TG_CHAT_ID)
    if not authorized:
        log.warning("unauthorized callback from user=%s", getattr(user, "id", None))
        await q.answer("⛔ غير مصرّح", show_alert=True)
        return
    m = CALLBACK_RE.match(q.data or "")
    if not m:
        await q.answer("زر غير صالح", show_alert=True)
        return
    act, pid, extra = m.group(1), int(m.group(2)), m.group(3)
    p = db.get(pid)
    if not p:
        await q.answer("المشروع مش موجود", show_alert=True)
        return
    app, actor = ctx.application, user.id
    try:
        if act == "ap":
            await do_approve(app, pid, actor)
            await q.answer("تمت الموافقة")
        elif act == "rj":
            await do_reject(app, pid, actor)
            await q.answer("اترفض")
            await q.message.reply_text("❌ المشروع اترفض.")
        elif act == "rg":
            if p["status"] != "pending_approval":
                raise db.InvalidTransition(f"الحالة الحالية {p['status']}")
            await q.answer("بكتب نسخة جديدة...")
            await do_regenerate(app, pid, actor)
        elif act == "ed":
            if p["status"] != "pending_approval":
                raise db.InvalidTransition(f"الحالة الحالية {p['status']}")
            app.bot_data["awaiting_edit"] = pid
            await q.answer()
            await q.message.reply_text(
                "✏️ ابعت النص الجديد كامل (60 كلمة أو أكتر) وأنا هحفظه كنسخة جديدة، أو ابعت تعليمات قصيرة "
                "زي «خليه أقصر واذكر ERP» وأنا أعدّله.")
        elif act == "sb":
            if p["status"] != "approved":
                raise db.InvalidTransition(f"الحالة الحالية {p['status']}")
            db.transition(pid, "submitted")
            db.log_action(pid, "marked_submitted", actor=actor, note="manual confirmation by owner")
            await q.answer("اتسجّل")
            await q.message.reply_text("📨 تمام، سجّلت إنك قدّمت يدويًا. لما تعرف النتيجة:", reply_markup=kb_outcome(pid))
        elif act == "bk":
            if p["status"] != "approved":
                raise db.InvalidTransition(f"الحالة الحالية {p['status']}")
            db.transition(pid, "pending_approval")
            db.log_action(pid, "back_to_review", actor=actor)
            await q.answer("رجع للمراجعة")
            await Notifier(app).notify_pending(pid)
        elif act == "out":
            db.update(pid, outcome=extra)
            db.log_action(pid, f"outcome_{extra}", actor=actor)
            await q.answer("اتسجّلت النتيجة 👍")
        if act in ("ap", "rj", "sb", "bk", "out"):
            try:
                await q.edit_message_reply_markup(None)
            except BadRequest:
                pass
    except db.InvalidTransition as e:
        await q.answer(f"مش ممكن دلوقتي: {e}", show_alert=True)
    except Exception as e:
        log.warning("button %s failed: %s", act, safe_exc(e))
        db.log_error("telegram", f"button {act}: {safe_exc(e)}", pid)
        await q.message.reply_text(f"⚠️ حصل خطأ: {esc(safe_exc(e, 160))}")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if not is_authorized(update):
        return await _deny(update)
    app, text, actor = ctx.application, update.message.text.strip(), update.effective_user.id
    try:
        awaiting = app.bot_data.pop("awaiting_edit", None)
        if awaiting:
            p = db.get(awaiting)
            if p and p["status"] == "pending_approval":
                if len(text.split()) >= 60:
                    await do_set_text(app, awaiting, actor, text)
                else:
                    await update.message.reply_text("⏳ بعدّل البروبوزال...")
                    await do_regenerate(app, awaiting, actor, instructions=text, source="edited_ai")
                return
        pid = None
        if update.message.reply_to_message:
            pid = app.bot_data.get("msg2pid", {}).get(update.message.reply_to_message.message_id)
        pid = pid or db.latest_pending_approval()
        if not pid:
            await update.message.reply_text("مفيش بروبوزال مفتوح دلوقتي. أول ما فرصة جديدة توصل هتلاقيها هنا.")
            return
        p = db.get(pid)
        cur = db.current_proposal(pid)
        res = await llm.interpret(text, p, cur["text"] if cur else "")
        intent, reply = res.get("intent"), str(res.get("reply") or "تمام")[:500]
        if intent == "approve" and p["status"] == "pending_approval":
            await update.message.reply_text(reply)
            await do_approve(app, pid, actor)
        elif intent == "reject":
            await do_reject(app, pid, actor)
            await update.message.reply_text(reply)
        elif intent == "regenerate" and p["status"] == "pending_approval":
            await update.message.reply_text("⏳ بكتب نسخة جديدة...")
            await do_regenerate(app, pid, actor)
        elif intent == "edit" and res.get("proposal") and p["status"] == "pending_approval":
            errors, _ = validator.validate(str(res["proposal"]))
            if errors:                                   # model's edit broke policy: redo through the guarded path
                await update.message.reply_text("⏳ التعديل خالف السياسات، بعيد صياغته...")
                await do_regenerate(app, pid, actor, instructions=text, source="edited_ai")
            else:
                await update.message.reply_text(reply)
                await do_set_text(app, pid, actor, str(res["proposal"]))
        else:
            await update.message.reply_text(reply)
    except db.InvalidTransition as e:
        await update.message.reply_text(f"مش ممكن دلوقتي: {e}")
    except Exception as e:
        log.warning("text handler failed: %s", safe_exc(e))
        db.log_error("telegram", f"text: {safe_exc(e)}")
        await update.message.reply_text(f"⚠️ حصل خطأ: {safe_exc(e, 160)}")


async def on_error(update, ctx: ContextTypes.DEFAULT_TYPE):
    err = ctx.error
    if isinstance(err, Conflict):
        now = time.time()
        if now - ctx.application.bot_data.get("last_conflict_alert", 0) > 600:
            ctx.application.bot_data["last_conflict_alert"] = now
            log.error("Telegram Conflict: another NafPT instance is polling with the same token")
            db.log_error("telegram", "Conflict: another instance polling")
            try:
                await _send(ctx.application.bot, "⚠️ Conflict: فيه نسخة NafPT تانية شغّالة بنفس التوكن (جهاز تاني أو جلسة tmux قديمة). "
                            "وقّف النسخ الزيادة، وإلا الردود هتضيع.")
            except Exception:
                pass
    elif isinstance(err, NetworkError):
        log.warning("network error (will retry): %s", safe_exc(err))
    else:
        log.error("handler error: %s", safe_exc(err))
        db.log_error("telegram", safe_exc(err))


def register(app: Application):
    for name, fn in (("start", cmd_start), ("help", cmd_help), ("status", cmd_status), ("pause", cmd_pause),
                     ("resume", cmd_resume), ("stats", cmd_stats), ("retry", cmd_retry)):
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
