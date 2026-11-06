"""Diagnoses the Nafezly scraper on YOUR network: pagination, listing links, and what gets extracted.
Usage: python check_site.py [--save-html] [--details N]
--save-html stores raw pages in data/diagnostics/ so the selectors can be tuned if extraction is poor."""
import asyncio
import os
import sys

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "")
os.environ.setdefault("GEMINI_API_KEY", "")

import config  # noqa: E402
from scraper import Nafezly, find_next_pages, parse_detail  # noqa: E402


async def main():
    save = "--save-html" in sys.argv
    n_details = int(sys.argv[sys.argv.index("--details") + 1]) if "--details" in sys.argv else 3
    s = Nafezly()
    await s.start()
    try:
        r = await s._get(config.LIST_URL)
        print(f"listing: HTTP {r.status_code}, {len(r.text)} bytes")
        if save:
            os.makedirs(config.DATA_DIR / "diagnostics", exist_ok=True)
            (config.DATA_DIR / "diagnostics" / "listing.html").write_text(r.text, encoding="utf-8")
        print("pagination links:", find_next_pages(r.text, config.LIST_URL)[:5] or "لا يوجد/غير مكتشف")
        scan = await s.scan_listing()
        print(f"scan: {len(scan.urls)} projects over {scan.pages} page(s), pagination={scan.pagination_detected}")
        for u in scan.urls[:5]:
            print(" -", u)
        for i, u in enumerate(scan.urls[:n_details]):
            rr = await s._get(u)
            d = parse_detail(rr.text, config.DETAIL_SELECTOR)
            if save:
                (config.DATA_DIR / "diagnostics" / f"detail{i + 1}.html").write_text(rr.text, encoding="utf-8")
            print(f"\n[{i + 1}] {d['title']}")
            for k in ("status", "budget", "duration", "published", "offers", "skills"):
                print(f"    {k}: {d[k] if d[k] not in (None, [], '') else '— (غير مستخرج)'}")
            print(f"    description: {len(d['description'])} حرف | hash: {d['content_hash']}")
    except Exception as e:
        print("❌", type(e).__name__, e)
        sys.exit(1)
    finally:
        await s.close()


asyncio.run(main())
