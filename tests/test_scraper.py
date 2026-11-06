import httpx
import pytest

import config
import scraper
from helpers import detail_html, listing_html, run
from scraper import BlockedError, Nafezly, ScrapeError, extract_links, find_next_pages, parse_detail

BASE = "https://nafezly.com/projects"


def make_scraper(handler):
    s = Nafezly(transport=httpx.MockTransport(handler))
    run(s.start())
    return s


def test_extract_links_relative_absolute_dedup():
    html = ('<a href="/project/1-a">x</a><a href="https://nafezly.com/project/2-b#c">y</a>'
            '<a href="/about">z</a><a href="/project/1-a">dup</a>')
    assert extract_links(html, BASE) == ["https://nafezly.com/project/1-a", "https://nafezly.com/project/2-b"]


def test_pagination_detection_rel_next_and_numbered_same_host_only():
    html = ('<link rel="next" href="/projects?page=2"><a href="/projects?page=3">3</a><a href="/projects?page=1">1</a>'
            '<a href="https://evil.com/projects?page=2">ext</a><a href="/projects/page/4">4</a>')
    assert find_next_pages(html, BASE) == ["https://nafezly.com/projects?page=2", "https://nafezly.com/projects?page=3",
                                           "https://nafezly.com/projects/page/4"]
    assert find_next_pages(listing_html([1]), BASE) == []
    assert find_next_pages('<a href="/projects?page=2">2</a><a href="/projects?page=1">1</a>',
                           "https://nafezly.com/projects?page=2") == []


def test_parse_detail_full_arabic_fields():
    html = detail_html("بناء API بـ ASP.NET", "محتاج API بسيط مع SQL Server", budget="$250.00 - $500.00",
                       duration="20 يوما", offers="٧", skills=("C#", "SQL Server", "REST"), published="2026-11-01")
    d = parse_detail(html, "main")
    assert d["title"] == "بناء API بـ ASP.NET" and d["status"] == "open"
    assert d["budget"] == "$250.00 - $500.00" and d["duration"] == "20 يوما" and d["offers"] == 7   # Arabic digits
    assert d["skills"] == ["C#", "SQL Server", "REST"] and d["published"] == "2026-11-01"
    assert d["description"] == "محتاج API بسيط مع SQL Server" and "menu" not in d["text"] and "foot" not in d["text"]


@pytest.mark.parametrize("status,expected", [("مفتوح", "open"), ("مُغلق", "closed"), ("مغلق", "closed"), ("مكتمل", "closed"),
                                              ("قيد التنفيذ", "closed"), ("Open", "open"), ("Closed", "closed"), ("غريب", "unknown")])
def test_status_detection(status, expected):
    assert parse_detail(detail_html("t", "d" * 40, status=status))["status"] == expected


def test_missing_fields_are_none_not_invented():
    d = parse_detail(detail_html("t", "وصف", budget=None, duration=None, status=None, offers=None, skills=()))
    assert d["budget"] is None and d["duration"] is None and d["offers"] is None and d["skills"] == []
    assert d["status"] == "unknown" and d["description"] == "وصف"


def test_inline_label_values_and_apply_button_hint():
    html = "<html><body><main><h1>T</h1><p>الميزانية: 100$</p><p>المهارات: PHP، Laravel / MySQL</p><button>قدم عرضك</button></main></body></html>"
    d = parse_detail(html)
    assert d["budget"] == "100$" and d["skills"] == ["PHP", "Laravel", "MySQL"] and d["status"] == "open"


def test_description_mentioning_closed_words_does_not_close_project():
    d = parse_detail(detail_html("t", "العميل يقول: المشروع السابق مغلق ومكتمل، ونحتاج نسخة جديدة", status="مفتوح"))
    assert d["status"] == "open"


def test_html_structure_change_is_handled_without_crash():
    d = parse_detail("<html><body><div class='x7'><span>نص عشوائي بدون عناوين</span></div></body></html>")
    assert d["status"] == "unknown" and d["budget"] is None and d["description"]
    assert parse_detail("")["title"] == ""                      # empty/garbage HTML
    ld = ('<html><head><script type="application/ld+json">{"name":"LD title","description":"LD desc",'
          '"datePosted":"2026-01-01","skills":["C#"]}</script></head><body></body></html>')
    d2 = parse_detail(ld)
    assert d2["title"] == "LD title" and d2["description"] and d2["published"] == "2026-01-01" and d2["skills"] == ["C#"]


def test_content_hash_ignores_offers_but_detects_real_edits():
    a = parse_detail(detail_html("T", "الوصف الأول", offers="3"))
    b = parse_detail(detail_html("T", "الوصف الأول", offers="9", published="منذ شهر"))
    c = parse_detail(detail_html("T", "الوصف تغيّر", offers="3"))
    e = parse_detail(detail_html("T", "الوصف الأول", budget="$999"))
    assert a["content_hash"] == b["content_hash"] != c["content_hash"] and a["content_hash"] != e["content_hash"]


def test_scan_listing_follows_pagination_and_discovers_existing_projects(monkeypatch):
    pages = {"/projects": listing_html([1, 2, 3], next_page=2), "/projects?page=2": listing_html([4, 5], next_page=3),
             "/projects?page=3": listing_html([5])}
    seen = []

    def handler(req):
        seen.append(str(req.url))
        key = req.url.path + (f"?{req.url.query.decode()}" if req.url.query else "")
        return httpx.Response(200, text=pages[key])
    monkeypatch.setattr(config, "LIST_URL", BASE)
    scan = run(make_scraper(handler).scan_listing())
    assert len(scan.urls) == 5 and scan.pages == 3 and scan.pagination_detected
    assert scan.urls[0].endswith("/project/1-slug")


def test_scan_respects_max_pages_and_stops_when_no_new_links(monkeypatch):
    monkeypatch.setattr(config, "LIST_URL", BASE)
    monkeypatch.setattr(config, "MAX_PAGES", 1)
    h = lambda req: httpx.Response(200, text=listing_html([1, 2], next_page=2))
    assert run(make_scraper(h).scan_listing()).pages == 1
    monkeypatch.setattr(config, "MAX_PAGES", 10)
    h2 = lambda req: httpx.Response(200, text=listing_html([1, 2], next_page=2))   # page 2 repeats page 1: stop
    assert run(make_scraper(h2).scan_listing()).pages == 2


def test_no_links_raises_actionable_error(monkeypatch):
    monkeypatch.setattr(config, "LIST_URL", BASE)
    with pytest.raises(ScrapeError, match="NAFEZLY_LINK_REGEX"):
        run(make_scraper(lambda r: httpx.Response(200, text="<html>no links</html>")).scan_listing())


def test_timeouts_are_retried_with_exponential_backoff(fresh_env, monkeypatch):
    sleeps, calls = fresh_env, []
    monkeypatch.setattr(config, "LIST_URL", BASE)

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ReadTimeout("slow", request=req)
        return httpx.Response(200, text=listing_html([1]))
    s = make_scraper(handler)
    assert len(run(s.scan_listing()).urls) == 1 and len(calls) == 3 and sleeps[:2] == [1, 2]


def test_persistent_failures_raise_after_retries(monkeypatch):
    monkeypatch.setattr(config, "HTTP_RETRIES", 2)
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503)
    with pytest.raises(ScrapeError, match="فشل"):
        run(make_scraper(handler)._get(BASE))
    assert len(calls) == 3


@pytest.mark.parametrize("resp", [httpx.Response(429, headers={"Retry-After": "120"}), httpx.Response(403),
                                  httpx.Response(200, text="<html>Just a moment... captcha</html>")])
def test_blocking_is_reported_not_bypassed(resp):
    s = make_scraper(lambda r: resp)
    with pytest.raises(BlockedError) as e:
        run(s._get(BASE))
    if resp.status_code == 429:
        assert e.value.retry_after == 120
    assert s.requests_made == 1                    # no retries/hammering when blocked


def test_closed_and_deleted_pages():
    s = make_scraper(lambda r: httpx.Response(410) if "gone" in str(r.url) else httpx.Response(
        200, text=detail_html("t", "d", status="مغلق")))
    assert run(s.fetch_detail("https://nafezly.com/project/1-gone"))["status"] == "gone"
    assert run(s.fetch_detail("https://nafezly.com/project/2-x"))["status"] == "closed"


def test_rate_limiter_spaces_requests(monkeypatch):
    waits = []

    async def rec(d):
        waits.append(d)
    monkeypatch.setattr(scraper, "_sleep", rec)
    monkeypatch.setattr(config, "REQUEST_DELAY", 2.0)
    s = make_scraper(lambda r: httpx.Response(200, text=listing_html([1])))
    run(s._get(BASE))
    run(s._get(BASE))
    assert any(w > 0 for w in waits) and max(waits) <= 2.7


def test_scraper_has_no_submission_capability():
    assert not any("submit" in n.lower() for n in dir(Nafezly))     # NafPT never submits proposals
