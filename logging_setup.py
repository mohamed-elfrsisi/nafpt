"""Safe logging: redacts secrets everywhere and keeps HTTP libraries from printing tokenized URLs."""
import logging
import re
from logging.handlers import RotatingFileHandler

import config

_PATTERNS = [
    (re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}"), "bot<REDACTED>"),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"), "<REDACTED_TELEGRAM_TOKEN>"),
    (re.compile(r"AIza[0-9A-Za-z_-]{20,}"), "<REDACTED_GEMINI_KEY>"),
    (re.compile(r"(?i)([?&](?:key|api_key|token)=)[^&\s'\"]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(x-goog-api-key|authorization|api[_-]?key|token)(\s*[=:]\s*)([^\s,;'\"]+)"), r"\1\2<REDACTED>"),
]
_SECRETS = set()


def register_secret(value):
    if value and len(value) >= 8:
        _SECRETS.add(value)


def redact(text):
    s = str(text)
    for secret in _SECRETS:
        s = s.replace(secret, "<REDACTED>")
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s


def safe_exc(exc, limit=300):
    """Sanitized one-line description of an exception for logs and Telegram."""
    return redact(f"{type(exc).__name__}: {exc}")[:limit]


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def setup(level=None):
    register_secret(config.TG_TOKEN)
    register_secret(config.GEMINI_KEY)
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_nafpt", False):
            root.removeHandler(h)
    root.setLevel(level or config.LOG_LEVEL)
    fmt = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers = [logging.StreamHandler()]
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(config.LOG_DIR / "nafpt.log", maxBytes=2_000_000,
                                            backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    for h in handlers:
        h.setFormatter(fmt)
        h._nafpt = True
        root.addHandler(h)
    # These libraries log full request URLs (which contain the bot token) at INFO level.
    for noisy in ("httpx", "httpcore", "hpack", "telegram.request", "telegram.ext.Application"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
