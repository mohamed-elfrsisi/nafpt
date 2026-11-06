import asyncio
import time

import pytest

import config
import db
import llm
import pipeline
from helpers import proposal_ar, run, make_project
from monitor import Monitor
from scraper import BlockedError, ListingScan

T0 = 1_000_000.0


def D(title, desc="محتاج API بسيط مع قاعدة بيانات", status="open", h=None, offers=3):
    return {"title": title, "description": desc, "budget": "$300", "duration": "10 days", "published": "اليوم",
            "skills": ["C#"], "status": status, "offers": offers, "text": desc, "content_hash": h or f"h-{title}-{desc}"}


class FakeScraper:
    def __init__(self, items):
        self.items = dict(items)          # url -> detail dict | Exception
        self.scans, self.detail_calls, self.fail_scan = 0, [], []

    async def scan_listing(self):
        self.scans += 1
        if self.fail_scan:
            exc = self.fail_scan.pop(0)
            if exc:
                raise exc
        return ListingScan(urls=list(self.items), pages=1, pagination_detected=False)

    async def fetch_detail(self, url):
        self.detail_calls.append(url)
        d = self.items[url]
        if isinstance(d, Exception):
            raise d
        return d


class FakeNotifier:
    def __init__(self):
        self.pending, self.events, self.alerts, self.reports, self.fail = [], [], [], [], False

    async def notify_pending(self, pid):
        if self.fail:
            raise RuntimeError("telegram down")
        self.pending.append(pid)
        db.update(pid, notify_status="sent", notified_at=time.time(), notify_attempts=(db.get(pid)["notify_attempts"] or 0) + 1)

    async def notify_event(self, t):
        self.events.append(t)

    async def alert(self, t):
        self.alerts.append(t)

    async def first_scan_report(self, s):
        self.reports.append(s)


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "MAX_PROCESS_PER_TICK", 20)
    monkeypatch.setattr(config, "MAX_NOTIFY_PER_TICK", 20)
    monkeypatch.setattr(config, "MAX_DETAIL_FETCH_PER_TICK", 50)
    calls = {"analyze": 0, "write": 0}
    state = {"scores": lambda p: 9.0 if "ASP" in (p["title"] or "") else 5.0 if "Backend" in (p["title"] or "") else 2.0,
             "write_fail": 0}

    async def analyze(p, cls=None):
        calls["analyze"] += 1
        s = state["scores"](p)
        return {"final_score": s, "categories": ["dotnet"], "scores": {"technical_skill_match": s, "experience_similarity": s,
                "feasibility": s, "commercial_attractiveness": s}, "score_explanation": f"درجة {s}", "language": "ar",
                "matched_skills": [], "similar_projects": [], "risks": [], "missing_information": []}

    async def write(p, a, **kw):
        calls["write"] += 1
        if state["write_fail"] > 0:
            state["write_fail"] -= 1
            raise llm.GeminiError("server", "gemini 503")
        return proposal_ar(), {"errors": [], "warnings": []}
    monkeypatch.setattr(llm, "analyze_project", analyze)
    monkeypatch.setattr(llm, "write_proposal", write)
    return calls, state


def mk(items, clock=None):
    clock = clock or Clock()
    s, n = FakeScraper(items), FakeNotifier()
    return Monitor(s, n, clock=clock), s, n, clock


U = lambda i: f"https://nafezly.com/project/{i}-x"
ITEMS = {U(1): D("مشروع API بـ ASP.NET"), U(2): D("Backend API service"), U(3): D("منخفض API"),
         U(4): D("تصميم لوجو", "لوجو لشركتي")}


def status_of(i):
    return db.get_by_url(U(i))["status"]


def test_first_run_processes_existing_projects_instead_of_marking_them_seen(env):
    calls, _ = env
    m, s, n, c = mk(ITEMS)
    run(m.tick())
    assert [status_of(i) for i in (1, 2, 3, 4)] == ["pending_approval", "pending_approval", "rejected", "rejected"]
    assert "seen" not in db.counts() and len(n.reports) == 1 and n.reports[0]["listed"] == 4
    # BUG FIX CHECK: score 4-6 project (#2) gets a full analysis + customized proposal, not a bare notification
    assert db.current_proposal(db.get_by_url(U(2))["id"]) is not None and db.get_analysis(db.get_by_url(U(2))["id"])
    assert sorted(n.pending) == sorted([db.get_by_url(U(1))["id"], db.get_by_url(U(2))["id"]])
    assert n.pending[0] == db.get_by_url(U(1))["id"]                       # best score delivered first
    assert db.get_by_url(U(4))["reject_reason"].startswith("استبعاد") and calls["analyze"] == 3   # stage 1 saved a call


def test_threshold_is_configurable(env, monkeypatch):
    monkeypatch.setattr(config, "MIN_SCORE", 6.0)
    m, s, n, c = mk(ITEMS)
    run(m.tick())
    assert status_of(2) == "rejected" and "أقل من الحد" in db.get_by_url(U(2))["reject_reason"]


def test_new_projects_after_start_and_no_duplicate_notifications(env):
    m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET")})
    run(m.tick())
    s.items[U(5)] = D("مشروع ثاني بـ ASP.NET", "API جديد")
    for _ in range(4):                       # many scans over time of the same listing
        c.t += 1000
        run(m.tick())
    ids = [db.get_by_url(U(1))["id"], db.get_by_url(U(5))["id"]]
    assert sorted(n.pending) == sorted(ids) and len(n.pending) == len(set(n.pending)) == 2
    assert len(n.reports) == 1 and db.stats()["total"] == 2 and s.scans == 5


def test_gemini_failures_are_retried_safely_never_sent_incomplete(env):
    calls, state = env
    state["write_fail"] = 2
    m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET")})
    run(m.tick())
    p = db.get_by_url(U(1))
    assert p["status"] == "analyzed" and p["attempts"] == 1 and p["next_retry"] == T0 + 120 and n.pending == []
    run(m.tick())                                                    # backoff window: no new Gemini call
    assert calls["write"] == 1
    c.t += 121
    run(m.tick())
    assert db.get(p["id"])["attempts"] == 2 and db.get(p["id"])["next_retry"] == c.t + 240 and n.pending == []
    c.t += 241
    run(m.tick())
    p = db.get(p["id"])
    assert p["status"] == "pending_approval" and p["attempts"] == 0 and n.pending == [p["id"]]
    assert calls["analyze"] == 1                                       # analysis was cached, only the proposal retried
    assert [e["component"] for e in db.recent_errors()].count("pipeline") == 2


def test_exhausted_retries_mark_failed_alert_and_manual_retry(env, monkeypatch):
    calls, state = env
    monkeypatch.setattr(config, "MAX_ATTEMPTS", 3)
    state["write_fail"] = 99
    m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET")})
    for _ in range(5):
        run(m.tick())
        c.t += 5000
    p = db.get_by_url(U(1))
    assert p["status"] == "failed" and p["attempts"] == 3 and any("فشل تجهيز" in a for a in n.alerts)
    state["write_fail"] = 0
    db.transition(p["id"], "discovered", attempts=0, next_retry=None, last_error=None)   # what /retry does
    run(m.tick())
    assert status_of(1) == "pending_approval"


def test_gemini_quota_pauses_processing_and_resumes(env):
    calls, state = env
    seen = {"raise": True}
    orig = llm.analyze_project

    async def quota(p, cls=None):
        if seen["raise"]:
            raise llm.GeminiError("quota", "daily quota exhausted")
        return await orig(p, cls)
    import pytest as _p
    llm.analyze_project = quota
    try:
        m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET")})
        run(m.tick())
        assert status_of(1) == "discovered" and any("Gemini" in a for a in n.alerts)
        until = float(db.get_setting("gemini_paused_until"))
        assert until > c.t
        run(m.tick())
        assert db.get_by_url(U(1))["attempts"] in (0, None)               # quota is not a project failure
        seen["raise"] = False
        c.t = until + 1
        run(m.tick())
        assert status_of(1) == "pending_approval"
    finally:
        llm.analyze_project = orig


def test_closed_projects_detected_on_discovery_and_recheck(env):
    m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET"), U(6): D("API قديم ASP.NET", status="closed"),
                     U(7): D("x", status="gone")})
    run(m.tick())
    assert status_of(6) == "closed" and status_of(7) == "closed" and status_of(1) == "pending_approval"
    assert db.get_by_url(U(6))["site_status"] == "closed"
    s.items[U(1)] = D("مشروع API بـ ASP.NET", status="closed")
    c.t += config.RECHECK_HOURS * 3600 + 10
    run(m.tick())
    assert status_of(1) == "closed" and any("اتقفل" in e for e in n.events)
    assert db.notify_queue(10) == []


def test_unknown_status_is_kept_and_flagged_not_assumed_closed(env):
    m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET", status="unknown")})
    run(m.tick())
    assert status_of(1) == "pending_approval" and db.get_by_url(U(1))["site_status"] == "unknown"


def test_project_updates_are_detected(env):
    calls, state = env
    m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET", h="v1"), U(2): D("Backend API service", h="b1")})
    run(m.tick())
    first = db.get_by_url(U(1))["id"]
    s.items[U(1)] = D("مشروع API بـ ASP.NET", "وصف اتغيّر بالكامل", h="v2")
    c.t += config.RECHECK_HOURS * 3600 + 10
    run(m.tick())
    p = db.get(first)
    assert p["needs_review"] == 1 and p["status"] == "pending_approval" and any("اتعدّل" in e for e in n.events)
    # auto-rejected project whose description improves is re-analyzed
    state["scores"] = lambda pr: 8.0 if "محسنة" in (pr["body"] or "") else 2.0
    m2, s2, n2, c2 = mk({U(8): D("مشروع API", "وصف ضعيف", h="w1")})
    run(m2.tick())
    assert status_of(8) == "rejected"
    s2.items[U(8)] = D("مشروع API", "نسخة محسنة من الوصف", h="w2")
    c2.t += config.RECHECK_HOURS * 3600 + 10
    db.update(db.get_by_url(U(8))["id"], last_checked=1.0)               # force recheck of terminal-auto project
    p8 = db.get_by_url(U(8))
    db.transition(p8["id"], "discovered")                                # (recheck only covers active states; reopen path)
    run(m2.tick())
    assert status_of(8) == "pending_approval"


def test_recovery_after_restart_resumes_every_stage(env):
    p1 = make_project("discovered", url=U(11), title="مشروع ASP.NET", body="API", content_hash="a")
    p2 = make_project("analyzed", url=U(12), title="مشروع ASP.NET 2", body="API", content_hash="b", score=8)
    db.add_analysis(p2, {"final_score": 8, "language": "ar"}, content_hash="b")
    p3 = make_project("pending_approval", url=U(13), title="ASP.NET 3", body="API", score=7, notify_status="queued")
    db.add_proposal(p3, proposal_ar())
    path = db._state["path"]
    db.close()
    db.use(path)                                                          # "process restarted"
    m, s, n, c = mk({})
    run(m.tick())
    assert sorted(n.pending) == sorted([p1, p2, p3])
    assert all(db.get(p)["status"] == "pending_approval" for p in (p1, p2, p3))


def test_scan_failures_alert_then_recover(env, monkeypatch):
    monkeypatch.setattr(config, "ALERT_AFTER_FAILURES", 2)
    m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET")})
    s.fail_scan = [RuntimeError("net down"), RuntimeError("net down"), None]
    for _ in range(3):
        run(m.tick())
        c.t += 700
    assert any("فشلت 2 مرات" in a for a in n.alerts) and any("رجعت تشتغل" in a for a in n.alerts)
    assert m.failures == 0 and status_of(1) == "pending_approval"
    assert db.last_run(ok=False)["error"] and db.last_run(ok=True)


def test_blocked_backoff_alerts_once_and_never_hammers(env):
    m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET")})
    s.fail_scan = [BlockedError("HTTP 429"), BlockedError("HTTP 429")]
    run(m.tick())
    assert m.next_scan >= c.t + 300 and len(n.alerts) == 1 and "مش بحاول أتخطى" in n.alerts[0]
    scans = s.scans
    run(m.tick())                                                         # still inside the backoff window
    assert s.scans == scans
    c.t = m.next_scan + 1
    run(m.tick())
    assert len(n.alerts) == 1 and m.next_scan >= c.t + 600                # longer wait, no alert spam


def test_pause_and_resume_survive_via_settings(env):
    m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET")})
    db.set_setting("paused", "1")
    run(m.tick())
    assert s.scans == 0 and db.stats()["total"] == 0
    db.set_setting("paused", "0")
    run(m.tick())
    assert s.scans == 1 and status_of(1) == "pending_approval"


def test_telegram_failure_is_retried_without_duplicates(env):
    m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET")})
    n.fail = True
    run(m.tick())
    p = db.get_by_url(U(1))
    assert p["status"] == "pending_approval" and p["notify_status"] == "failed" and p["notify_attempts"] == 1
    n.fail = False
    run(m.tick())
    run(m.tick())
    assert n.pending == [p["id"]] and db.get(p["id"])["notify_status"] == "sent"


def test_detail_fetch_errors_back_off_then_fail(env, monkeypatch):
    monkeypatch.setattr(config, "MAX_ATTEMPTS", 2)
    m, s, n, c = mk({U(1): RuntimeError("500")})
    run(m.tick())
    p = db.get_by_url(U(1))
    assert p["attempts"] == 1 and p["next_retry"] > c.t and p["status"] == "discovered"
    c.t += 1000
    run(m.tick())
    assert status_of(1) == "failed"


def test_run_loop_survives_a_crashing_tick(env, monkeypatch):
    m, s, n, c = mk({})
    calls = [0]
    stop = asyncio.Event()

    async def tick():
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("boom")
        if calls[0] >= 3:
            stop.set()
    m.tick = tick
    real = asyncio.sleep

    async def fast(d):
        await real(0)
    monkeypatch.setattr(asyncio, "sleep", fast)
    run(m.run(stop))
    assert calls[0] == 3 and m.failures == 1 and not m.running and config.HEARTBEAT.exists()
    assert any(e["component"] == "monitor" for e in db.recent_errors())


def test_snapshot_reports_real_state(env):
    m, s, n, c = mk({U(1): D("مشروع API بـ ASP.NET")})
    assert m.snapshot()["last_scan_ok"] is None
    run(m.tick())
    snap = m.snapshot()
    assert snap["last_scan_ok"] == T0 and snap["summary"]["listed"] == 1 and snap["next_scan"] > T0
