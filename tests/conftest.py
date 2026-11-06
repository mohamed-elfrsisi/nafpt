import os
import pathlib
import sys
import tempfile

_TMP = tempfile.mkdtemp()
os.environ.update(
    TELEGRAM_BOT_TOKEN="123456789:AAH_fake_token_for_tests_0123456789abcdef",
    GEMINI_API_KEY="AIzaSyFakeKeyForTests0123456789abcdefghij",
    TELEGRAM_CHAT_ID="555000111",
    NAFPT_DATA_DIR=_TMP, NAFPT_DB=str(pathlib.Path(_TMP) / "boot.db"),
)
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_env(tmp_path, monkeypatch):
    import config
    import db
    import llm
    import scraper
    for name, sub in (("DATA_DIR", ""), ("BACKUP_DIR", "backups"), ("LOG_DIR", "logs")):
        monkeypatch.setattr(config, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(config, "LOCK_FILE", tmp_path / "nafpt.lock")
    monkeypatch.setattr(config, "HEARTBEAT", tmp_path / "heartbeat")
    monkeypatch.setattr(config, "REQUEST_DELAY", 0.0)
    monkeypatch.setattr(config, "GEMINI_DAILY_LIMIT", 10_000)
    sleeps = []

    async def nosleep(delay=0, *a, **k):
        sleeps.append(delay)

    monkeypatch.setattr(llm, "_sleep", nosleep)
    monkeypatch.setattr(scraper, "_sleep", nosleep)
    db.use(tmp_path / "t.db")
    yield sleeps
    db.close()
