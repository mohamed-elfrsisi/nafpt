# NafPT 1.0 — Audit & Root-Cause Report

## Architecture found (0.2)
main.py (loop) → scraper.py (HTTP, 1 page) → db.py (flat `projects`) → llm.py (Gemini REST) → bot.py (Telegram). 12 tests, none covering the failures below.

## Bugs and root causes
| # | Symptom | Root cause (verified in code) | Fix in 1.0 |
|---|---|---|---|
| A | Existing projects ignored | `main.cycle(baseline=True)` wrote every listed URL as status `seen` without detail/analysis/notification; only page 1 scanned | No baseline. All listings become `discovered`, are fetched, analyzed, proposed, notified in bounded batches. Pagination. Legacy `seen` rows re-queued by the migration. Closed/updated detection |
| B | Proposals only for score ≥ 7; 4–6 sent without one | `route()` wrote proposals only when `score >= HIGH`; `mid` got a bare card | Every project ≥ `MIN_SCORE` gets analysis + proposal + risks + recommendation. Gemini failure → retry with backoff, never an incomplete notification |
| C | `getUpdates 200` but no replies/notifications | (1) Any command/text from a chat different from `TELEGRAM_CHAT_ID` was **silently ignored** (`_authorized` returned nothing); (2) monitoring silently skipped when the chat id was unset; (3) no startup validation or message; (4) Chat-ID == bot-ID mistakes undetectable | Visible denial replies, `/start` always answers with the real Chat ID, startup validation (token, `@NafPTbot` identity, chat-id diagnosis incl. bot-id and Forbidden/BadRequest), startup message, `check_setup.py`, Conflict handler |
| C2 | Token exposure | `httpx` logs the full `…/bot<TOKEN>/getUpdates` URL at INFO (reproduced during the audit) — this is the "HTTP 200" line | Redacting formatter on all handlers, httpx/httpcore/telegram loggers at WARNING, exact-secret redaction, sanitized exceptions |
| D | No honest startup report | Components not verified | Startup checks (DB, Telegram, Gemini `models.get`, scraper); message lists real state; monitoring not claimed when a prerequisite failed |
| E | Restart/state fragility | statuses were ad hoc | State machine with validated transitions; all work derived from DB state; retry/backoff columns |

## Design decisions / removals (with reasons)
- Removed Playwright/browser mode, `submit()`, `AUTO_SUBMIT`, `login_save.py`: the spec forbids automatic submission, Termux can't run Chromium, and the code path was never verified against the real site.
- Removed price/duration "hints": the profile contains no rates; inventing numbers violates the no-invention rule.
- Proposal identity changed from "senior freelancer" to an honest early-career engineer (validator blocks senior/years claims).

## Could not be verified from the build environment
- nafezly.com HTML (no outbound access to it here): extraction is label-based and tested on synthetic fixtures. Run `python check_site.py --save-html` on your phone.
- Real Telegram delivery and real Gemini calls (no network to those APIs here): covered by mocks; use `check_setup.py --send-test`.
- Python 3.13 (sandbox has 3.12). Dependencies declare 3.13 support.
