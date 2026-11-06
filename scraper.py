"""Nafezly scraper: HTTP + BeautifulSoup only (works on Termux). Never bypasses CAPTCHA/auth/rate limits."""
import asyncio
import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

import config
from classifier import normalize
from logging_setup import safe_exc

log = logging.getLogger("scraper")

BLOCK_WORDS = ("captcha", "just a moment", "access denied", "attention required", "unusual traffic", "cf-chl")
NO_LINKS_MSG = ("صفحة المشاريع رجّعت 200 لكن مفيش روابط مشاريع. غالبًا NAFEZLY_LINK_REGEX محتاج تعديل "
                "أو الصفحة بتتحمل بـ JavaScript. شغّل: python check_site.py --save-html")

_sleep = asyncio.sleep          # indirection for tests
_now = time.monotonic


class ScrapeError(RuntimeError):
    pass


class BlockedError(ScrapeError):
    """403/429/CAPTCHA: back off and notify; never try to bypass."""

    def __init__(self, msg, retry_after=None):
        super().__init__(msg)
        self.retry_after = retry_after


@dataclass
class ListingScan:
    urls: list = field(default_factory=list)
    pages: int = 0
    pagination_detected: bool = False


# ------------------------------------------------------------------ pure parsing helpers
def extract_links(html, base):
    found = re.findall(config.LINK_REGEX, html)
    out = []
    for f in found:
        u = urljoin(base, f.split("#")[0])
        if u not in out:
            out.append(u)
    return out


def _page_number(url):
    q = parse_qs(urlparse(url).query)
    for key in ("page", "p", "pg"):
        if key in q:
            try:
                return int(q[key][0])
            except ValueError:
                return None
    m = re.search(r"/page/(\d+)", urlparse(url).path)
    return int(m.group(1)) if m else None


def find_next_pages(html, base):
    """Same-host pagination links (rel=next, ?page=N, /page/N). Ordered by page number."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(base).netloc
    cur = _page_number(base) or 1
    cands = {}
    for tag in soup.find_all(["a", "link"], href=True):
        url = urljoin(base, tag["href"].split("#")[0])
        if urlparse(url).netloc != host or url == base:
            continue
        rel = tag.get("rel") or []
        rel = rel if isinstance(rel, list) else [rel]
        n = _page_number(url)
        text = normalize(tag.get_text(" ", strip=True))
        is_next = "next" in rel or text in ("التالي", "next", "›", "»", ">")
        if n is not None and n > cur:
            cands[url] = n
        elif is_next and n is None:
            cands[url] = cur + 1
    return [u for u, _ in sorted(cands.items(), key=lambda kv: kv[1])]


_LABELS = {
    "status": ["حالة المشروع", "الحالة", "status", "project status"],
    "budget": ["الميزانية", "ميزانية المشروع", "budget", "السعر", "الاجر", "الأجر"],
    "duration": ["مدة التنفيذ", "مده التنفيذ", "مدة المشروع", "المدة", "duration", "delivery time", "وقت التسليم"],
    "published": ["تاريخ النشر", "تاريخ الاضافه", "تاريخ الإضافة", "posted", "published", "تم النشر", "نشر"],
    "offers": ["العروض", "عدد العروض", "عروض", "proposals", "bids", "offers", "المتقدمين", "عدد المتقدمين"],
    "skills": ["المهارات", "المهارات المطلوبة", "skills", "required skills", "التقنيات", "الوسوم", "tags"],
    "description": ["تفاصيل المشروع", "وصف المشروع", "التفاصيل", "description", "project details", "الوصف"],
}
_LABELS_N = {k: {normalize(x) for x in v} for k, v in _LABELS.items()}
_CLOSED = ["مغلق", "مقفول", "تم الاغلاق", "منتهي", "مكتمل", "تم التنفيذ", "تم التعاقد", "تم اختيار", "ملغي", "مغلقه",
           "closed", "completed", "awarded", "cancelled", "canceled", "finished", "in progress", "قيد التنفيذ"]
_OPEN = ["مفتوح", "متاح", "يستقبل", "قيد التقديم", "open", "accepting", "active", "نشط"]
_APPLY_BTN = ["اضف عرضك", "قدم عرضك", "قدم عرض", "تقديم عرض", "اضافه عرض", "submit proposal", "place bid", "apply now"]


def _split_label(line):
    raw = line.strip()
    m = re.match(r"^([^:：]{2,30})[:：]\s*(.*)$", raw)
    cand, inline = (m.group(1), m.group(2)) if m else (raw, "")
    n = normalize(cand).strip()
    for key, labels in _LABELS_N.items():
        if n in labels:
            return key, inline.strip()
    return None, ""


def _to_int(v):
    if v is None:
        return None
    s = str(v).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def _status_from(value, page_lines):
    if value:
        n = normalize(value)
        if any(normalize(w) in n for w in _CLOSED):
            return "closed"
        if any(normalize(w) in n for w in _OPEN):
            return "open"
    joined = normalize("\n".join(page_lines))
    if any(normalize(b) in joined for b in _APPLY_BTN):
        return "open"
    return "unknown"


def _hash(title, description, budget, duration, skills):
    norm = "|".join([normalize(title or ""), normalize(re.sub(r"\s+", " ", description or "")),
                     normalize(budget or ""), normalize(duration or ""), ",".join(sorted(normalize(s) for s in skills))])
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def parse_detail(html, selector=None):
    """Extract project fields. Missing fields stay None/[] (never invented)."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    ld = {}
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
            if isinstance(data, list):
                data = data[0] if data else {}
            if isinstance(data, dict):
                ld = data
                break
        except (ValueError, TypeError):
            continue
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    if not title and soup.title and soup.title.string:
        title = re.split(r"\s[|\-–—]\s", soup.title.string.strip())[0].strip()
    if not title:
        title = str(ld.get("name") or "")
    for t in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "iframe"]):
        t.decompose()
    root = (soup.select_one(selector) if selector else None) or soup.body or soup
    text = re.sub(r"\n{3,}", "\n\n", root.get_text("\n", strip=True))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    fields, skills, desc_lines, desc_started = {}, [], [], False
    i = 0
    while i < len(lines):
        key, inline = _split_label(lines[i])
        if key == "description":
            desc_started = True
            if inline:
                desc_lines.append(inline)
            i += 1
            while i < len(lines) and not _split_label(lines[i])[0]:
                desc_lines.append(lines[i])
                i += 1
            continue
        if key == "skills":
            vals = [s.strip() for s in re.split(r"[،,/·|]", inline) if s.strip()] if inline else []
            j = i + 1
            while not inline and j < len(lines) and len(vals) < 25:   # chips on following lines only when no inline value
                if _split_label(lines[j])[0] or len(lines[j]) > 40:
                    break
                vals.append(lines[j])
                j += 1
            skills = vals
            i = j
            continue
        if key:
            if inline:
                fields[key] = inline
            elif i + 1 < len(lines) and not _split_label(lines[i + 1])[0]:
                fields[key] = lines[i + 1]
                i += 1
        i += 1

    description = "\n".join(desc_lines).strip() if desc_started else text
    if not description and ld.get("description"):
        description = str(ld["description"])
    description = description[:6000]
    if not skills and ld.get("skills"):
        raw = ld["skills"]
        skills = [raw] if isinstance(raw, str) else [str(x) for x in raw]
    published = fields.get("published") or (str(ld["datePosted"]) if ld.get("datePosted") else None)
    status = _status_from(fields.get("status"), lines[:60])
    return {
        "title": title, "description": description, "budget": fields.get("budget"), "duration": fields.get("duration"),
        "published": published, "skills": skills, "status": status, "offers": _to_int(fields.get("offers")),
        "text": text[:6000], "content_hash": _hash(title, description, fields.get("budget"), fields.get("duration"), skills),
    }


# ------------------------------------------------------------------ network
class Nafezly:
    def __init__(self, transport=None):
        self._transport = transport
        self._last_req = 0.0
        self._lock = asyncio.Lock()
        self.http = None
        self.requests_made = 0

    async def start(self):
        self.http = httpx.AsyncClient(
            headers={"User-Agent": config.USER_AGENT, "Accept-Language": "ar,en;q=0.8"},
            timeout=config.HTTP_TIMEOUT, follow_redirects=True, transport=self._transport)

    async def close(self):
        if self.http:
            await self.http.aclose()

    async def _throttle(self):
        async with self._lock:
            wait = config.REQUEST_DELAY + random.uniform(0, config.REQUEST_DELAY * 0.3) - (_now() - self._last_req)
            if wait > 0:
                await _sleep(wait)
            self._last_req = _now()

    async def _get(self, url):
        last = None
        for attempt in range(config.HTTP_RETRIES + 1):
            await self._throttle()
            self.requests_made += 1
            try:
                r = await self.http.get(url)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = e
            else:
                if r.status_code in (403, 429):
                    ra = r.headers.get("Retry-After")
                    raise BlockedError(f"HTTP {r.status_code}", int(ra) if ra and ra.isdigit() else None)
                if r.status_code in (404, 410):
                    return r
                if r.status_code >= 500:
                    last = ScrapeError(f"HTTP {r.status_code}")
                elif r.status_code != 200:
                    raise ScrapeError(f"HTTP {r.status_code}")
                else:
                    if any(w in r.text[:4000].lower() for w in BLOCK_WORDS):
                        raise BlockedError("صفحة حماية/كابتشا")
                    return r
            if attempt < config.HTTP_RETRIES:
                await _sleep(min(30, 2 ** attempt))
        raise ScrapeError(f"فشل الطلب بعد المحاولات: {safe_exc(last)}")

    async def scan_listing(self, max_pages=None):
        max_pages = max_pages or config.MAX_PAGES
        scan, seen, visited, queue = ListingScan(), set(), set(), [config.LIST_URL]
        while queue and scan.pages < max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            r = await self._get(url)
            if r.status_code != 200:
                if scan.pages == 0:
                    raise ScrapeError(f"صفحة المشاريع رجّعت HTTP {r.status_code}")
                break
            links = extract_links(r.text, url)
            nxt = find_next_pages(r.text, url)
            scan.pages += 1
            if nxt:
                scan.pagination_detected = True
            fresh = [u for u in links if u not in seen]
            if not fresh:
                if scan.pages == 1:
                    raise ScrapeError(NO_LINKS_MSG)
                break
            for u in fresh:
                seen.add(u)
                scan.urls.append(u)
            for n in nxt:
                if n not in visited and n not in queue:
                    queue.append(n)
        return scan

    async def fetch_detail(self, url):
        """Returns parsed detail dict. status='gone' when the page no longer exists (404/410)."""
        r = await self._get(url)
        if r.status_code in (404, 410):
            return {"title": None, "description": None, "budget": None, "duration": None, "published": None,
                    "skills": [], "status": "gone", "offers": None, "text": "", "content_hash": None}
        return parse_detail(r.text, config.DETAIL_SELECTOR)
