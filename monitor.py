"""Monitoring engine: scan -> detail -> analysis -> proposal -> notification, with recovery and observability."""
import asyncio
import logging
import random
import time
from datetime import datetime

import config
import db
import pipeline
from logging_setup import safe_exc
from scraper import BlockedError

log = logging.getLogger("monitor")


def poll_interval(hour=None):
    h = datetime.now().hour if hour is None else hour
    base = config.POLL_ACTIVE if config.ACTIVE_FROM <= h < config.ACTIVE_TO else config.POLL_QUIET
    return base + random.uniform(0, base * 0.2)


class Monitor:
    """`notifier` needs: notify_pending(pid), notify_event(text), alert(text), first_scan_report(summary)."""

    def __init__(self, scraper, notifier, clock=time.time):
        self.scraper, self.notifier, self.clock = scraper, notifier, clock
        self.next_scan = 0.0
        self.failures = 0
        self.alerted = False
        self.reported_first = False
        self.last_error = None
        self.last_scan_ok = None
        self.last_scan_summary = {}
        self.started_at = clock()
        self.running = False
        self.interval = 0.0
        saved = db.get_setting("last_scan_ok")
        if saved:
            self.last_scan_ok = float(saved)

    # ------------------------------------------------------------ state for /status
    def snapshot(self):
        return {
            "paused": db.is_paused(), "running": self.running, "last_scan_ok": self.last_scan_ok,
            "next_scan": self.next_scan, "interval": self.interval, "failures": self.failures,
            "last_error": self.last_error, "summary": self.last_scan_summary,
            "gemini_paused_until": float(db.get_setting("gemini_paused_until", 0) or 0),
        }

    # ------------------------------------------------------------ one tick
    async def tick(self):
        if db.is_paused():
            return
        now = self.clock()
        if now >= self.next_scan:
            await self.scan()
        await self.process()
        await self.deliver()

    async def scan(self):
        run_id = db.start_run()
        now = self.clock()
        try:
            scan = await self.scraper.scan_listing()
            new = 0
            for rank, url in enumerate(scan.urls):
                _, is_new = db.upsert_listing(url, run_id, rank)
                new += 1 if is_new else 0
            await self._fetch_details()
            await self._recheck_active()
            db.finish_run(run_id, True, scan.pages, len(scan.urls), new)
            self.interval = poll_interval()
            self.next_scan = now + self.interval
            self.last_scan_ok = now
            db.set_setting("last_scan_ok", now)
            self.last_scan_summary = {"pages": scan.pages, "listed": len(scan.urls), "new": new,
                                      "pagination": scan.pagination_detected}
            self.last_error = None
            if self.failures >= config.ALERT_AFTER_FAILURES:
                await self.notifier.alert("✅ المراقبة رجعت تشتغل طبيعي.")
            self.failures, self.alerted = 0, False
            if not self.reported_first:
                self.reported_first = True
                await self.notifier.first_scan_report({**self.last_scan_summary, "backlog": len(db.work_queue(10_000)) +
                                                       len(db.needs_detail(10_000))})
        except BlockedError as e:
            self.failures += 1
            wait = e.retry_after or min(1800, 300 * 2 ** min(self.failures - 1, 3))
            self.next_scan = now + wait
            self.last_error = f"الموقع رفض الطلبات ({e})"
            db.finish_run(run_id, False, error=self.last_error)
            db.log_error("scraper", self.last_error)
            log.warning("blocked: %s; backing off %ss", e, wait)
            if not self.alerted:
                self.alerted = True
                await self.notifier.alert(f"🛑 {self.last_error}. وقفت المراقبة مؤقتًا {int(wait // 60)} دقيقة. "
                                          "مش بحاول أتخطى الحماية. لو استمر راجع الموقع بنفسك.")
        except Exception as e:
            self.failures += 1
            self.last_error = safe_exc(e)
            self.next_scan = now + min(600, 30 * 2 ** min(self.failures - 1, 4))
            db.finish_run(run_id, False, error=self.last_error)
            db.log_error("scraper", self.last_error)
            log.exception("scan failed")
            if self.failures >= config.ALERT_AFTER_FAILURES and not self.alerted:
                self.alerted = True
                await self.notifier.alert(f"⚠️ المراقبة فشلت {self.failures} مرات ورا بعض: {self.last_error}")

    # ------------------------------------------------------------ details / rechecks
    async def _apply(self, p, d):
        """Store fetched data and react to closed/updated. Returns event or None."""
        pid, now = p["id"], self.clock()
        if d["status"] == "gone" or d["status"] == "closed":
            db.update(pid, site_status=d["status"], last_checked=now)
            if p["status"] not in ("closed", "submitted", "rejected", "failed"):
                was_pending = p["status"] in ("pending_approval", "approved")
                db.transition(pid, "closed", reason="المشروع اتقفل على الموقع")
                if was_pending:
                    await self.notifier.notify_event(
                        f"🔒 المشروع اتقفل ولا يستقبل عروض:\n{p['title'] or p['url']}\n{p['url']}")
                return "closed"
            return None
        change = db.save_detail(pid, d, now)
        fresh = db.get(pid)
        if p["status"] == "closed" and d["status"] == "open":
            db.transition(pid, "discovered", attempts=0, next_retry=None)
            return "reopened"
        if change == "updated":
            if p["status"] in ("discovered", "analyzed", "proposal_generated") or (
                    p["status"] == "rejected" and p["decided_by"] == "auto"):
                db.transition(pid, "discovered", attempts=0, next_retry=None, reject_reason=None)
            elif p["status"] in ("pending_approval", "approved"):
                db.update(pid, needs_review=1)
                await self.notifier.notify_event(
                    f"✏️ المشروع اتعدّل بعد ما عمل بروبوزال:\n{fresh['title']}\n{fresh['url']}\n"
                    "راجع التغيير وجرّب 🔄 إعادة التوليد لو لازم.")
            return "updated"
        return None

    async def _fetch_details(self):
        for p in db.needs_detail(config.MAX_DETAIL_FETCH_PER_TICK, self.clock()):
            try:
                d = await self.scraper.fetch_detail(p["url"])
            except BlockedError:
                raise
            except Exception as e:
                attempts = (p["attempts"] or 0) + 1
                db.log_error("scraper", f"detail {p['url']}: {safe_exc(e)}", p["id"])
                if attempts >= config.MAX_ATTEMPTS:
                    db.transition(p["id"], "failed", attempts=attempts, last_error=safe_exc(e))
                else:
                    db.update(p["id"], attempts=attempts, last_error=safe_exc(e),
                              next_retry=self.clock() + min(3600, 60 * 2 ** attempts))
                continue
            if d["status"] in ("gone", "closed"):
                if d["status"] == "closed":
                    db.save_detail(p["id"], d, self.clock())
                db.update(p["id"], site_status=d["status"], last_checked=self.clock())
                db.transition(p["id"], "closed", reason="المشروع مقفول/محذوف على الموقع")
                continue
            db.save_detail(p["id"], d, self.clock())

    async def _recheck_active(self):
        older = self.clock() - config.RECHECK_HOURS * 3600
        for p in db.recheck_candidates(older, config.RECHECK_PER_TICK):
            try:
                d = await self.scraper.fetch_detail(p["url"])
            except BlockedError:
                raise
            except Exception as e:
                log.warning("recheck failed %s: %s", p["url"], safe_exc(e))
                db.update(p["id"], last_checked=self.clock())
                continue
            await self._apply(p, d)

    # ------------------------------------------------------------ processing / delivery
    async def process(self):
        paused_until = float(db.get_setting("gemini_paused_until", 0) or 0)
        if self.clock() < paused_until:
            return
        for p in db.work_queue(config.MAX_PROCESS_PER_TICK, self.clock()):
            try:
                await pipeline.process_one(p["id"], self.clock())
            except pipeline.QuotaPause as q:
                await self.notifier.alert(f"⏸ Gemini: {q}. هكمل تلقائيًا بعد {time.strftime('%H:%M', time.localtime(q.until))}.")
                return
            row = db.get(p["id"])
            if row and row["status"] == "failed":
                await self.notifier.alert(f"❌ فشل تجهيز مشروع بعد {config.MAX_ATTEMPTS} محاولات:\n"
                                          f"{row['title'] or row['url']}\n{row['last_error']}\nاستخدم /retry لإعادة المحاولة.")

    async def deliver(self):
        for p in db.notify_queue(config.MAX_NOTIFY_PER_TICK):
            try:
                await self.notifier.notify_pending(p["id"])
            except Exception as e:
                row = db.get(p["id"])
                db.update(p["id"], notify_status="failed", notify_attempts=(row["notify_attempts"] or 0) + 1)
                db.log_error("telegram", f"notify failed: {safe_exc(e)}", p["id"])
                log.warning("notify failed for %s: %s", p["id"], safe_exc(e))
                break                                  # likely a Telegram-level problem: retry next tick

    # ------------------------------------------------------------ loop
    def backlog(self):
        return bool(db.needs_detail(1, self.clock()) or db.work_queue(1, self.clock()) or db.notify_queue(1))

    async def run(self, stop_event=None):
        self.running = True
        try:
            while not (stop_event and stop_event.is_set()):
                config.HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
                config.HEARTBEAT.touch()
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception as e:                  # the loop itself must never die silently
                    self.failures += 1
                    self.last_error = safe_exc(e)
                    db.log_error("monitor", self.last_error)
                    log.exception("tick crashed; continuing")
                    if self.failures >= config.ALERT_AFTER_FAILURES and not self.alerted:
                        self.alerted = True
                        await self.notifier.alert(f"⚠️ حلقة المراقبة بتعطل: {self.last_error}")
                sleep = 3 if self.backlog() and not db.is_paused() else max(5, min(30, self.next_scan - self.clock()))
                await asyncio.sleep(sleep)
        finally:
            self.running = False
