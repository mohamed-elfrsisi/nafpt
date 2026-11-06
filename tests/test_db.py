import sqlite3
import time

import pytest

import config
import db
from helpers import make_project, proposal_ar


def _legacy_db(path):
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE projects(id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT UNIQUE, title TEXT, body TEXT,
      score INTEGER, reason TEXT, status TEXT, proposal TEXT, created REAL, updated REAL, meta TEXT, outcome TEXT, notified_at REAL)""")
    rows = [("seen", None, None, None, None), ("new", "t-new", "b", None, None), ("low", "t-low", "b", None, None),
            ("mid", "t-mid", "b", None, None), ("pending", "t-pend", "b", "قديم بروبوزال", 111.0),
            ("approved", "t-app", "b", "approved text", 222.0), ("rejected", "t-rej", "b", "x", 1.0),
            ("ignored", "t-ign", "b", None, None), ("submitted", "t-sub", "b", "sub text", 3.0)]
    for i, (st, title, body, prop, notified) in enumerate(rows):
        c.execute("INSERT INTO projects(url,title,body,score,status,proposal,created,updated,outcome,notified_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (f"https://nafezly.com/project/{i + 1}-x", title, body, 7, st, prop, 1.0, 2.0, "won" if st == "submitted" else None, notified))
    c.commit()
    c.close()


def test_migration_from_legacy_preserves_data_makes_backup_and_requeues_seen(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_db(path)
    db.use(path)
    by_url = {r["url"].rsplit("/", 1)[1]: r for r in db._rows("SELECT * FROM projects")}
    st = {int(k.split("-")[0]): v["status"] for k, v in by_url.items()}
    assert st[1] == "discovered" and st[2] == "discovered"                 # 'seen'/'new' baseline rows now get analyzed
    assert st[3] == "rejected" and by_url["3-x"]["decided_by"] == "auto"   # legacy low
    assert st[4] == "analyzed"                                              # legacy mid (qualified, no proposal yet)
    assert st[5] == "pending_approval" and by_url["5-x"]["notify_status"] == "sent"   # already notified: no duplicate
    assert st[6] == "approved" and st[9] == "submitted"
    assert st[7] == "rejected" and by_url["7-x"]["decided_by"] == "user" and st[8] == "rejected"
    assert by_url["9-x"]["outcome"] == "won" and by_url["5-x"]["proposal"] == "قديم بروبوزال"   # nothing lost
    assert db.current_proposal(db.get_by_url("https://nafezly.com/project/5-x")["id"])["source"] == "legacy"
    backups = list(config.BACKUP_DIR.glob("legacy-v1-*.db"))
    assert len(backups) == 1
    old = sqlite3.connect(backups[0])
    assert old.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 9
    old.close()
    db.use(path)                                                           # idempotent: no second backup, no re-migration
    assert len(list(config.BACKUP_DIR.glob("legacy-v1-*.db"))) == 1
    assert db.get_setting("schema_version") == str(db.SCHEMA_VERSION)


def test_duplicate_prevention():
    a, new_a = db.upsert_listing("https://nafezly.com/project/5-x")
    b, new_b = db.upsert_listing("https://nafezly.com/project/5-x")
    assert a == b and new_a and not new_b and db.stats()["total"] == 1
    assert db.get(a)["external_id"] == "5"
    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as c:
            c.execute("INSERT INTO projects(url,status) VALUES('https://nafezly.com/project/5-x','x')")


def test_state_machine_happy_path_and_invalid_moves():
    pid = make_project("discovered")
    for s in ("analyzed", "proposal_generated", "pending_approval", "approved", "submitted"):
        db.transition(pid, s)
    assert db.get(pid)["status"] == "submitted"
    with pytest.raises(db.InvalidTransition):
        db.transition(pid, "discovered")                 # submitted is final
    other = make_project("discovered", url="https://nafezly.com/project/77-x")
    for bad in ("approved", "pending_approval", "submitted", "proposal_generated"):
        with pytest.raises(db.InvalidTransition):
            db.transition(other, bad)
    with pytest.raises(db.InvalidTransition):
        db.transition(other, "bogus")
    db.transition(other, "rejected", decided_by="auto")
    db.transition(other, "discovered")                   # auto-rejected can be re-opened after content change
    pend = make_project("pending_approval", url="https://nafezly.com/project/78-x")
    db.transition(pend, "rejected", decided_by="user")
    db.transition(pend, "discovered")                    # allowed by machine; monitor only does it for auto rejections


def test_alternative_paths_pending_to_rejected_and_regenerated_edited_keep_pending():
    pid = make_project("pending_approval")
    p1 = db.add_proposal(pid, "v1")
    p2 = db.add_proposal(pid, "v2", source="regenerated")
    p3 = db.add_proposal(pid, "v3", source="user_edit")
    assert db.get(pid)["status"] == "pending_approval" and db.get(pid)["proposal"] == "v3"
    assert [v["version"] for v in db.proposal_versions(pid)] == [1, 2, 3]
    assert [v["is_current"] for v in db.proposal_versions(pid)] == [0, 0, 1]
    assert db.current_proposal(pid)["id"] == p3 and p1 != p2
    db.transition(pid, "rejected", decided_by="user")
    assert db.get(pid)["status"] == "rejected"


def test_state_survives_restart(tmp_path):
    pid = make_project("analyzed", url="https://nafezly.com/project/9-x", score=7.5)
    db.add_analysis(pid, {"final_score": 7.5}, content_hash="h1")
    db.add_proposal(pid, "نص")
    path = db._state["path"]
    db.close()
    db.use(path)
    p = db.get_by_url("https://nafezly.com/project/9-x")
    assert p["status"] == "analyzed" and p["score"] == 7.5 and db.get_analysis(p["id"])["final_score"] == 7.5
    assert db.current_proposal(p["id"])["text"] == "نص"


def test_transaction_rolls_back_on_error():
    with pytest.raises(RuntimeError):
        with db.transaction() as c:
            c.execute("INSERT INTO projects(url,status,created,updated) VALUES('https://x/rollback','discovered',1,1)")
            raise RuntimeError("boom")
    assert db.get_by_url("https://x/rollback") is None
    pid, _ = db.upsert_listing("https://x/after")      # connection still usable, no stuck transaction
    assert pid


def test_nested_transactions_commit_once():
    with db.transaction():
        db.upsert_listing("https://x/a")
        with db.transaction():
            db.upsert_listing("https://x/b")
    assert db.stats()["total"] == 2


def test_save_detail_content_hash_states():
    pid = make_project("discovered", content_hash=None, body=None)
    d = {"title": "T", "description": "desc", "budget": "$5", "skills": ["C#"], "status": "open", "content_hash": "A", "offers": 2}
    assert db.save_detail(pid, d) == "new_content"
    assert db.save_detail(pid, d) == "same"
    assert db.save_detail(pid, {**d, "content_hash": "B"}) == "updated"
    assert db.get(pid)["site_status"] == "open" and db.get(pid)["offers"] == 2


def test_counters_settings_pause_and_errors():
    assert db.get_counter("gemini_calls") == 0
    db.incr_counter("gemini_calls"); db.incr_counter("gemini_calls", 2)
    assert db.get_counter("gemini_calls") == 3
    assert not db.is_paused()
    db.set_setting("paused", "1")
    assert db.is_paused()
    db.log_error("t", "bad token 123456789:AAH_fake_token_for_tests_0123456789abcdef")
    assert "AAH_fake" not in db.recent_errors()[0]["message"]


def test_queues_and_ordering():
    now = time.time()
    a = make_project("discovered", url="https://x/1", body="b", run_id=1, rank=5)
    b = make_project("discovered", url="https://x/2", body="b", run_id=2, rank=1)
    c = make_project("analyzed", url="https://x/3", body="b", run_id=1, rank=9)
    d = make_project("discovered", url="https://x/4", body="b", next_retry=now + 999)
    order = [p["id"] for p in db.work_queue(10, now)]
    assert order == [c, b, a] and d not in order      # analyzed first, then newest run, backoff respected
    n1 = make_project("pending_approval", url="https://x/5", score=6, notify_status="queued")
    n2 = make_project("pending_approval", url="https://x/6", score=9, notify_status="queued")
    n3 = make_project("pending_approval", url="https://x/7", score=9, notify_status="sent")
    assert [p["id"] for p in db.notify_queue(10)] == [n2, n1]   # best opportunities first; already-sent excluded
