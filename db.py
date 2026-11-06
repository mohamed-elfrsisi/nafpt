"""SQLite layer: schema v2, safe migration from the legacy NafPT database, state machine, proposal versions."""
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import config

SCHEMA_VERSION = 2

STATES = ("discovered", "analyzed", "proposal_generated", "pending_approval", "approved",
          "rejected", "submitted", "closed", "failed")

ALLOWED = {
    "discovered": {"analyzed", "rejected", "closed", "failed"},
    "analyzed": {"proposal_generated", "discovered", "rejected", "closed", "failed"},
    "proposal_generated": {"pending_approval", "discovered", "closed", "failed"},
    "pending_approval": {"approved", "rejected", "closed", "failed"},
    "approved": {"submitted", "pending_approval", "closed"},
    "rejected": {"discovered"},
    "closed": {"discovered"},
    "failed": {"discovered"},
    "submitted": set(),
}
TERMINAL_FOR_RECHECK = ("submitted", "rejected", "closed", "failed")


class InvalidTransition(Exception):
    pass


_EXTRA_COLS = {
    "external_id": "TEXT", "content_hash": "TEXT", "published": "TEXT", "budget": "TEXT", "duration": "TEXT",
    "skills": "TEXT", "offers": "INTEGER", "site_status": "TEXT DEFAULT 'unknown'", "categories": "TEXT",
    "decided_by": "TEXT", "reject_reason": "TEXT", "attempts": "INTEGER DEFAULT 0", "last_error": "TEXT",
    "next_retry": "REAL", "notify_status": "TEXT DEFAULT 'none'", "notify_attempts": "INTEGER DEFAULT 0",
    "last_seen": "REAL", "last_checked": "REAL", "analyzed_at": "REAL", "run_id": "INTEGER", "rank": "INTEGER",
    "meta": "TEXT", "outcome": "TEXT", "notified_at": "REAL", "needs_review": "INTEGER DEFAULT 0",
}
_UPDATABLE = set(_EXTRA_COLS) | {"title", "body", "score", "reason", "status", "proposal", "updated"}

_state = {"conn": None, "path": None, "depth": 0}
_lock = threading.RLock()


# ------------------------------------------------------------------ connection
def use(path):
    """Close the current connection and open `path` (used by tests and tools)."""
    close()
    _state["path"] = Path(path)
    return _conn()


def close():
    with _lock:
        if _state["conn"] is not None:
            try:
                _state["conn"].close()
            finally:
                _state["conn"] = None
                _state["depth"] = 0


def _conn():
    with _lock:
        if _state["conn"] is None:
            path = _state["path"] or config.DB_PATH
            _state["path"] = Path(path)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=5000")
            _state["conn"] = c
            _migrate(c)
        return _state["conn"]


@contextmanager
def transaction():
    with _lock:
        c = _conn()
        if _state["depth"] > 0:
            _state["depth"] += 1
            try:
                yield c
            finally:
                _state["depth"] -= 1
            return
        c.execute("BEGIN IMMEDIATE")
        _state["depth"] = 1
        try:
            yield c
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            _state["depth"] = 0


def _rows(sql, args=()):
    with _lock:
        return [dict(r) for r in _conn().execute(sql, args).fetchall()]


def _row(sql, args=()):
    with _lock:
        r = _conn().execute(sql, args).fetchone()
        return dict(r) if r else None


# ------------------------------------------------------------------ schema & migration
_TABLES = [
    """CREATE TABLE IF NOT EXISTS projects(
      id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE, title TEXT, body TEXT, score REAL,
      reason TEXT, status TEXT, proposal TEXT, created REAL, updated REAL)""",
    "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)",
    """CREATE TABLE IF NOT EXISTS project_analysis(
      id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
      content_hash TEXT, stage INTEGER, final_score REAL, data TEXT, model TEXT, created REAL)""",
    """CREATE TABLE IF NOT EXISTS proposals(
      id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
      version INTEGER NOT NULL, text TEXT NOT NULL, source TEXT, model TEXT, validation TEXT,
      is_current INTEGER DEFAULT 1, created REAL, UNIQUE(project_id, version))""",
    """CREATE TABLE IF NOT EXISTS approval_actions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
      proposal_id INTEGER, action TEXT NOT NULL, actor TEXT, note TEXT, created REAL)""",
    """CREATE TABLE IF NOT EXISTS monitoring_runs(
      id INTEGER PRIMARY KEY AUTOINCREMENT, started REAL, finished REAL, ok INTEGER,
      pages INTEGER DEFAULT 0, listed INTEGER DEFAULT 0, new_count INTEGER DEFAULT 0, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS error_logs(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, component TEXT, message TEXT, project_id INTEGER)""",
]
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status, next_retry)",
    "CREATE INDEX IF NOT EXISTS idx_projects_notify ON projects(status, notify_status)",
    "CREATE INDEX IF NOT EXISTS idx_projects_hash ON projects(content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_analysis_project ON project_analysis(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_proposals_project ON proposals(project_id, is_current)",
    "CREATE INDEX IF NOT EXISTS idx_actions_project ON approval_actions(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_errors_ts ON error_logs(ts)",
]


def _create_schema(c):
    """Runs inside the migration transaction (no executescript: it would COMMIT implicitly)."""
    for stmt in _TABLES:
        c.execute(stmt)
    cols = {r[1] for r in c.execute("PRAGMA table_info(projects)")}
    for name, typ in _EXTRA_COLS.items():
        if name not in cols:
            c.execute(f"ALTER TABLE projects ADD COLUMN {name} {typ}")
    for stmt in _INDEXES:                      # after the columns exist (legacy tables lack them)
        c.execute(stmt)


def _backup(c, version):
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_path = config.BACKUP_DIR / f"{Path(_state['path']).stem}-v{version}-{stamp}.db"
    dest = sqlite3.connect(str(dest_path))
    try:
        c.backup(dest)
    finally:
        dest.close()
    return dest_path


def _migrate(c):
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    version = 0
    if "settings" in tables:
        row = c.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()
        version = int(row[0]) if row else (1 if "projects" in tables else 0)
    elif "projects" in tables:
        version = 1
    if "projects" in tables and version < SCHEMA_VERSION:
        _backup(c, version)
    c.execute("BEGIN IMMEDIATE")
    try:
        _create_schema(c)
        if version == 1:
            _migrate_legacy(c)
        c.execute("INSERT INTO settings(key,value) VALUES('schema_version',?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA_VERSION),))
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def _migrate_legacy(c):
    """v1 (NafPT 0.x) -> v2. Existing data is preserved; baseline-'seen' projects are re-queued for analysis."""
    c.execute("UPDATE projects SET decided_by='user' WHERE status IN ('rejected','ignored')")
    c.execute("UPDATE projects SET status='rejected' WHERE status='ignored'")
    c.execute("UPDATE projects SET status='discovered' WHERE status IN ('seen','new')")
    c.execute("UPDATE projects SET status='rejected', decided_by='auto', reject_reason='legacy: low score/excluded' "
              "WHERE status='low'")
    c.execute("UPDATE projects SET status='analyzed' WHERE status='mid'")
    c.execute("UPDATE projects SET status='pending_approval', notify_status='sent' WHERE status='pending'")
    c.execute("UPDATE projects SET notify_status='sent' WHERE notified_at IS NOT NULL AND notify_status='none'")
    c.execute("UPDATE projects SET site_status='unknown' WHERE site_status IS NULL")
    legacy = c.execute("SELECT id, proposal, updated FROM projects WHERE proposal IS NOT NULL AND proposal<>''").fetchall()
    for r in legacy:
        exists = c.execute("SELECT 1 FROM proposals WHERE project_id=?", (r["id"],)).fetchone()
        if not exists:
            c.execute("INSERT INTO proposals(project_id,version,text,source,is_current,created) VALUES(?,?,?,?,1,?)",
                      (r["id"], 1, r["proposal"], "legacy", r["updated"] or time.time()))


# ------------------------------------------------------------------ settings & counters
def get_setting(key, default=None):
    r = _row("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    with transaction() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))


def _counter_key(name):
    return f"counter:{name}:{datetime.now().strftime('%Y-%m-%d')}"


def incr_counter(name, by=1):
    key = _counter_key(name)
    with transaction() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+?", (key, str(by), by))


def get_counter(name):
    return int(get_setting(_counter_key(name), 0) or 0)


def is_paused():
    return get_setting("paused", "0") == "1"


# ------------------------------------------------------------------ projects
def upsert_listing(url, run_id=None, rank=None):
    """Returns (project_id, is_new). Duplicate URLs never create a second row."""
    now = time.time()
    with transaction() as c:
        r = c.execute("SELECT id FROM projects WHERE url=?", (url,)).fetchone()
        if r:
            c.execute("UPDATE projects SET last_seen=? WHERE id=?", (now, r["id"]))
            return r["id"], False
        m = re.search(r"/project/(\d+)", url)
        cur = c.execute(
            "INSERT INTO projects(url,external_id,status,created,updated,last_seen,run_id,rank,site_status) "
            "VALUES(?,?,?,?,?,?,?,?,'unknown')",
            (url, m.group(1) if m else None, "discovered", now, now, now, run_id, rank))
        return cur.lastrowid, True


def get(pid):
    return _row("SELECT * FROM projects WHERE id=?", (pid,))


def get_by_url(url):
    return _row("SELECT * FROM projects WHERE url=?", (url,))


def update(pid, **fields):
    bad = set(fields) - _UPDATABLE
    if bad:
        raise ValueError(f"unknown project fields: {sorted(bad)}")
    fields["updated"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with transaction() as c:
        c.execute(f"UPDATE projects SET {cols} WHERE id=?", (*fields.values(), pid))


def transition(pid, new_status, **fields):
    """Validated state change. Raises InvalidTransition for illegal moves."""
    if new_status not in STATES:
        raise InvalidTransition(f"unknown state {new_status}")
    with transaction() as c:
        r = c.execute("SELECT status FROM projects WHERE id=?", (pid,)).fetchone()
        if not r:
            raise InvalidTransition(f"project {pid} not found")
        cur = r["status"]
        if new_status != cur and new_status not in ALLOWED.get(cur, set()):
            raise InvalidTransition(f"{cur} -> {new_status} غير مسموح")
        fields["status"] = new_status
        update(pid, **fields)


def by_status(status, limit=100):
    return _rows("SELECT * FROM projects WHERE status=? ORDER BY id LIMIT ?", (status, limit))


def needs_detail(limit, now=None):
    now = now or time.time()
    return _rows("SELECT * FROM projects WHERE status='discovered' AND body IS NULL "
                 "AND (next_retry IS NULL OR next_retry<=?) "
                 "ORDER BY COALESCE(run_id,0) DESC, COALESCE(rank,0) ASC, id ASC LIMIT ?", (now, limit))


def work_queue(limit, now=None):
    now = now or time.time()
    return _rows("SELECT * FROM projects WHERE status IN ('discovered','analyzed') AND body IS NOT NULL "
                 "AND (next_retry IS NULL OR next_retry<=?) "
                 "ORDER BY (status='analyzed') DESC, COALESCE(run_id,0) DESC, COALESCE(rank,0) ASC, id ASC LIMIT ?",
                 (now, limit))


def notify_queue(limit):
    return _rows("SELECT * FROM projects WHERE status='pending_approval' AND notify_status IN ('queued','failed','none') "
                 "AND notify_attempts<? ORDER BY score DESC, id ASC LIMIT ?", (config.MAX_NOTIFY_ATTEMPTS, limit))


def recheck_candidates(older_than, limit):
    marks = ",".join("?" for _ in TERMINAL_FOR_RECHECK)
    return _rows(f"SELECT * FROM projects WHERE status NOT IN ({marks}) AND body IS NOT NULL "
                 "AND COALESCE(last_checked,0)<? ORDER BY COALESCE(last_checked,0) ASC LIMIT ?",
                 (*TERMINAL_FOR_RECHECK, older_than, limit))


def save_detail(pid, d, now=None):
    """Store parsed page data. Returns 'new_content' | 'updated' | 'same' (content-hash based)."""
    now = now or time.time()
    p = get(pid)
    old_hash = p["content_hash"]
    new_hash = d.get("content_hash")
    fields = dict(
        title=d.get("title") or p["title"], body=d.get("description") or d.get("text") or p["body"] or "",
        budget=d.get("budget"), duration=d.get("duration"), published=d.get("published"),
        skills=json.dumps(d.get("skills") or [], ensure_ascii=False), offers=d.get("offers"),
        site_status=d.get("status") or "unknown", content_hash=new_hash, last_checked=now)
    update(pid, **fields)
    if old_hash is None:
        return "new_content"
    return "same" if old_hash == new_hash else "updated"


def latest_pending_approval():
    r = _row("SELECT id FROM projects WHERE status='pending_approval' ORDER BY COALESCE(notified_at, updated) DESC LIMIT 1")
    return r["id"] if r else None


# ------------------------------------------------------------------ analysis & proposals
def add_analysis(pid, data, stage=2, content_hash=None, model=None):
    with transaction() as c:
        c.execute("INSERT INTO project_analysis(project_id,content_hash,stage,final_score,data,model,created) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (pid, content_hash, stage, data.get("final_score"), json.dumps(data, ensure_ascii=False),
                   model, time.time()))
    update(pid, analyzed_at=time.time())


def get_analysis(pid):
    r = _row("SELECT * FROM project_analysis WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,))
    if not r:
        return None
    data = json.loads(r["data"])
    data["_content_hash"], data["_stage"] = r["content_hash"], r["stage"]
    return data


def add_proposal(pid, text, source="ai", model=None, validation=None):
    with transaction() as c:
        v = c.execute("SELECT COALESCE(MAX(version),0)+1 AS v FROM proposals WHERE project_id=?", (pid,)).fetchone()["v"]
        c.execute("UPDATE proposals SET is_current=0 WHERE project_id=?", (pid,))
        cur = c.execute("INSERT INTO proposals(project_id,version,text,source,model,validation,is_current,created) "
                        "VALUES(?,?,?,?,?,?,1,?)",
                        (pid, v, text, source, model, json.dumps(validation or {}, ensure_ascii=False), time.time()))
        update(pid, proposal=text)
        return cur.lastrowid


def current_proposal(pid):
    return _row("SELECT * FROM proposals WHERE project_id=? AND is_current=1 ORDER BY version DESC LIMIT 1", (pid,))


def proposal_versions(pid):
    return _rows("SELECT id,version,source,model,is_current,created,text FROM proposals WHERE project_id=? ORDER BY version",
                 (pid,))


def log_action(pid, action, proposal_id=None, actor=None, note=None):
    with transaction() as c:
        c.execute("INSERT INTO approval_actions(project_id,proposal_id,action,actor,note,created) VALUES(?,?,?,?,?,?)",
                  (pid, proposal_id, action, str(actor) if actor is not None else None, note, time.time()))


def actions(pid):
    return _rows("SELECT * FROM approval_actions WHERE project_id=? ORDER BY id", (pid,))


# ------------------------------------------------------------------ runs & errors
def start_run():
    with transaction() as c:
        return c.execute("INSERT INTO monitoring_runs(started) VALUES(?)", (time.time(),)).lastrowid


def finish_run(run_id, ok, pages=0, listed=0, new_count=0, error=None):
    with transaction() as c:
        c.execute("UPDATE monitoring_runs SET finished=?, ok=?, pages=?, listed=?, new_count=?, error=? WHERE id=?",
                  (time.time(), 1 if ok else 0, pages, listed, new_count, error, run_id))


def last_run(ok=None):
    q = "SELECT * FROM monitoring_runs WHERE finished IS NOT NULL"
    if ok is not None:
        q += f" AND ok={1 if ok else 0}"
    return _row(q + " ORDER BY id DESC LIMIT 1")


def log_error(component, message, pid=None):
    from logging_setup import redact
    with transaction() as c:
        c.execute("INSERT INTO error_logs(ts,component,message,project_id) VALUES(?,?,?,?)",
                  (time.time(), component, redact(message)[:500], pid))


def recent_errors(limit=5):
    return _rows("SELECT * FROM error_logs ORDER BY id DESC LIMIT ?", (limit,))


def error_count(since=None):
    return _row("SELECT COUNT(*) AS n FROM error_logs WHERE ts>=?", (since or 0,))["n"]


# ------------------------------------------------------------------ stats
def counts():
    return {r["status"]: r["n"] for r in _rows("SELECT status, COUNT(*) AS n FROM projects GROUP BY status")}


def stats():
    c = counts()
    outcomes = {r["outcome"]: r["n"] for r in _rows(
        "SELECT outcome, COUNT(*) AS n FROM projects WHERE outcome IS NOT NULL GROUP BY outcome")}
    return {
        "counts": c,
        "total": sum(c.values()),
        "analyzed": _row("SELECT COUNT(*) AS n FROM projects WHERE analyzed_at IS NOT NULL")["n"],
        "proposals": _row("SELECT COUNT(DISTINCT project_id) AS n FROM proposals")["n"],
        "proposal_versions": _row("SELECT COUNT(*) AS n FROM proposals")["n"],
        "awaiting": c.get("pending_approval", 0),
        "outcomes": outcomes,
        "failed_ops": c.get("failed", 0),
        "notified": _row("SELECT COUNT(*) AS n FROM projects WHERE notify_status='sent'")["n"],
        "notify_failed": _row("SELECT COUNT(*) AS n FROM projects WHERE notify_status='failed'")["n"],
        "gemini_calls_today": get_counter("gemini_calls"),
        "gemini_errors_today": get_counter("gemini_errors"),
    }
