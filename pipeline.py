"""Per-project workflow: discovered -> analyzed -> proposal_generated -> pending_approval (with safe retries)."""
import json
import logging
import time

import classifier
import config
import db
import llm
from logging_setup import safe_exc

log = logging.getLogger("pipeline")


class QuotaPause(Exception):
    """Gemini daily quota/budget is exhausted: stop processing until `until` (epoch seconds)."""

    def __init__(self, until, message):
        super().__init__(message)
        self.until = until


async def _analyze(pid):
    p = db.get(pid)
    cls = classifier.classify(p["title"], p["body"])
    db.update(pid, categories=json.dumps(cls["categories"]))
    if cls["decision"] == "reject":
        db.add_analysis(pid, {"final_score": 0, "stage1": cls, "recommendation": "skip"}, stage=1,
                        content_hash=p["content_hash"])
        db.transition(pid, "rejected", decided_by="auto", reject_reason=cls["reason"], score=0, reason=cls["reason"])
        return "rejected_stage1"
    cached = db.get_analysis(pid)
    if cached and cached.get("_stage") == 2 and cached.get("_content_hash") == p["content_hash"]:
        analysis = cached                      # same content: do not pay for Gemini again
    else:
        analysis = await llm.analyze_project(p, cls)
        db.add_analysis(pid, analysis, stage=2, content_hash=p["content_hash"], model=config.GEMINI_MODEL)
    score = analysis["final_score"]
    db.update(pid, categories=json.dumps(analysis["categories"]))
    if score >= config.MIN_SCORE:
        db.transition(pid, "analyzed", score=score, reason=analysis.get("score_explanation", ""))
        return "analyzed"
    db.transition(pid, "rejected", decided_by="auto", score=score,
                  reject_reason=f"الدرجة {score} أقل من الحد الأدنى {config.MIN_SCORE}",
                  reason=analysis.get("score_explanation", ""))
    return "rejected_low_score"


async def _propose(pid):
    p = db.get(pid)
    analysis = db.get_analysis(pid) or {}
    text, validation = await llm.write_proposal(p, analysis)
    db.add_proposal(pid, text, source="ai", model=config.GEMINI_MODEL, validation=validation)
    db.transition(pid, "proposal_generated")
    db.transition(pid, "pending_approval", notify_status="queued", notify_attempts=0)
    return "pending_approval"


def _record_failure(pid, err, now):
    p = db.get(pid)
    attempts = (p["attempts"] or 0) + 1
    msg = safe_exc(err)
    db.log_error("pipeline", msg, pid)
    if attempts >= config.MAX_ATTEMPTS:
        db.transition(pid, "failed", attempts=attempts, last_error=msg, next_retry=None)
        return "failed"
    delay = min(3600, 60 * 2 ** attempts)
    db.update(pid, attempts=attempts, last_error=msg, next_retry=now + delay)
    return "retry_later"


async def process_one(pid, now=None):
    """Advance one project as far as possible. Raises QuotaPause when Gemini quota is exhausted."""
    now = now or time.time()
    result = "noop"
    while True:
        p = db.get(pid)
        if not p or p["status"] not in ("discovered", "analyzed") or p["site_status"] in ("closed", "gone"):
            return result
        try:
            result = await (_analyze(pid) if p["status"] == "discovered" else _propose(pid))
            db.update(pid, attempts=0, last_error=None, next_retry=None)
        except llm.GeminiError as e:
            if e.kind in ("quota", "budget", "auth"):
                until = (now + (e.retry_after or 3600) if e.kind == "quota"
                         else now + 1800 if e.kind == "auth" else _next_midnight(now))
                db.set_setting("gemini_paused_until", until)
                db.log_error("gemini", f"{e.kind}: {e}", pid)
                raise QuotaPause(until, str(e))
            return _record_failure(pid, e, now)
        except Exception as e:                      # defensive: one bad project must not stop the loop
            log.exception("process_one failed for %s", pid)
            return _record_failure(pid, e, now)
        if result in ("rejected_stage1", "rejected_low_score", "pending_approval"):
            return result


def _next_midnight(now):
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + 1, 0, 5, 0, 0, 0, -1))
