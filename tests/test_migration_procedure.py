from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.run_cms_regression import stable_source_report
from server.config import Settings
from server.db import init_database
from server.migration_procedure import (
    ProcedureError, apply_plan, create_plan, operation_status, rollback_operation, verify_operation,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime(tmp_path):
    media = tmp_path / "runtime/media"
    media.mkdir(parents=True)
    manifest = tmp_path / "runtime/manifest.json"
    manifest.write_text('{"entries":{},"errors":[]}', encoding="utf-8")
    missing = tmp_path / "runtime/missing.csv"
    missing.write_text("url,error,directory,references,fallback_mirrored,fallback_url,materials\n", encoding="utf-8")
    settings = Settings(root=ROOT, site_dir=ROOT / "site", database_path=tmp_path / "runtime/cms.sqlite3",
                        media_dir=media, schema_path=ROOT / "site/cms-schema.json",
                        media_derivatives_dir=tmp_path / "runtime/derivatives",
                        legacy_sections_path=ROOT / "current-sections.json", legacy_crawl_path=None,
                        media_manifest_path=manifest, missing_media_report_path=missing,
                        environment="test", bootstrap_user="admin", bootstrap_password="unused")
    init_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as c:
        for role in ("admin", "publisher", "editor", "viewer"):
            c.execute("INSERT INTO users(id,username,password_hash,role,is_active,created_at) VALUES(?,?,?,?,1,'now')",
                      (role, role, "unused", role))
        for identifier, kind, url, value in (
            ("news", "news", "/news/undated.html", "2000-01-01"),
            ("album", "gallery", "/albums/2024/holiday.html", "2000-01-01"),
            ("uncertain", "gallery", "/albums/unknown.html", "2000-01-01"),
        ):
            data = {"publication_date" if kind == "news" else "event_date": value,
                    "category": "Жизнь прихода", "summary": "Описание " + identifier,
                    "cover": "assets/school-maslenitsa.jpg", "cover_alt": "Фото",
                    "photos": [{"image": "assets/school-maslenitsa.jpg", "alt": "Фото", "caption": str(i), "order": i}
                               for i in (2, 7, 9)],
                    "body_text": "Новости прихода\nТРЕБЫ ОНЛАЙН\nО храме"}
            c.execute("""INSERT INTO contents(id,content_type,slug,title,status,data_json,legacy_url,
                migration_review_required,version,created_at,updated_at)
                VALUES(?,?,?,?,'draft',?,?,1,1,'created','updated')""",
                      (identifier, kind, identifier, identifier, json.dumps(data, ensure_ascii=False), url))
    return settings


def rows(settings):
    with sqlite3.connect(settings.database_path) as c:
        c.row_factory = sqlite3.Row
        return {row["id"]: dict(row) for row in c.execute("SELECT * FROM contents")}


def make_plan(runtime, tmp_path):
    bundle = tmp_path / "bundle"
    return bundle, create_plan(runtime, bundle)


def test_plan_is_read_only_and_seals_full_artifacts(runtime, tmp_path):
    before = stable_source_report(runtime.database_path, runtime.media_dir, runtime.media_manifest_path)
    bundle, plan = make_plan(runtime, tmp_path)
    assert stable_source_report(runtime.database_path, runtime.media_dir, runtime.media_manifest_path) == before
    assert plan["summary"]["archive"] == plan["summary"]["ready_for_publication"] == plan["summary"]["remaining_queue"] == 1
    assert (bundle / "baseline.sqlite3").is_file()
    assert (bundle / "projection/projected.sqlite3").is_file()
    assert plan["files"] and len(plan["seal"]) == 64


def test_apply_verify_replay_and_rollback_preserve_photos_and_history(runtime, tmp_path):
    before = rows(runtime)
    bundle, plan = make_plan(runtime, tmp_path)
    result = apply_plan(runtime, bundle, username="admin")
    assert result["status"] == "verified" and result["semantic_ok"] and result["media_ok"]
    after = rows(runtime)
    assert (after["news"]["status"], after["news"]["migration_review_required"], after["news"]["published_version"]) == ("archived", 0, None)
    assert after["news"]["data_json"] == before["news"]["data_json"]
    assert (after["album"]["status"], after["album"]["migration_review_required"], after["album"]["published_version"]) == ("draft", 0, None)
    album = json.loads(after["album"]["data_json"])
    assert album["event_date"] == "2024-01-01"
    assert album["photos"] == json.loads(before["album"]["data_json"])["photos"]
    assert after["uncertain"]["migration_review_required"] == 1
    assert apply_plan(runtime, bundle, username="admin")["replayed"] is True
    assert verify_operation(runtime, plan["operation_id"], username="publisher")["replayed"] is True
    assert rows(runtime) == after
    assert operation_status(runtime, plan["operation_id"])["status"] == "verified"
    assert rollback_operation(runtime, plan["operation_id"], username="admin")["status"] == "rolled_back"
    restored = rows(runtime)
    for identifier, original in before.items():
        assert {k: v for k, v in restored[identifier].items() if k not in {"version", "updated_at"}} == {
            k: v for k, v in original.items() if k not in {"version", "updated_at"}}
        assert restored[identifier]["version"] > after[identifier]["version"] > original["version"]
    assert rollback_operation(runtime, plan["operation_id"], username="admin")["replayed"] is True
    assert apply_plan(runtime, bundle, username="admin")["status"] == "rolled_back"


@pytest.mark.parametrize("username", ["viewer", "editor", "absent"])
def test_unprivileged_actor_cannot_apply(runtime, tmp_path, username):
    bundle, plan = make_plan(runtime, tmp_path)
    before = rows(runtime)
    with pytest.raises(ProcedureError) as error:
        apply_plan(runtime, bundle, username=username)
    assert error.value.code == "FORBIDDEN"
    assert rows(runtime) == before
    assert operation_status(runtime, plan["operation_id"])["status"] == "not_applied"


def test_publisher_can_apply_but_disabled_actor_is_rejected(runtime, tmp_path):
    with sqlite3.connect(runtime.database_path) as c:
        c.execute("UPDATE users SET is_active=0 WHERE username='admin'")
    bundle, _ = make_plan(runtime, tmp_path)
    with pytest.raises(ProcedureError) as error:
        apply_plan(runtime, bundle, username="admin")
    assert error.value.code == "FORBIDDEN"
    assert apply_plan(runtime, bundle, username="publisher")["status"] == "verified"


@pytest.mark.parametrize("change", ["content", "versionless", "manifest", "media"])
def test_stale_plan_fails_before_any_content_is_changed(runtime, tmp_path, change):
    bundle, _ = make_plan(runtime, tmp_path)
    if change in {"content", "versionless"}:
        with sqlite3.connect(runtime.database_path) as c:
            c.execute("UPDATE contents SET title='Чужая правка',version=version+? WHERE id='album'", (int(change == "content"),))
    elif change == "manifest":
        runtime.media_manifest_path.write_text('{"entries":{},"errors":[],"changed":true}', encoding="utf-8")
    else:
        (runtime.media_dir / "unexpected.txt").write_text("file", encoding="utf-8")
    before = rows(runtime)
    with pytest.raises(ProcedureError) as error:
        apply_plan(runtime, bundle, username="admin")
    assert error.value.code == "PLAN_STALE"
    assert rows(runtime) == before


@pytest.mark.parametrize("artifact", ["plan.json", "report.json", "baseline.sqlite3"])
def test_damaged_bundle_is_never_applied(runtime, tmp_path, artifact):
    bundle, _ = make_plan(runtime, tmp_path)
    path = bundle / artifact
    if artifact == "plan.json":
        value = json.loads(path.read_text(encoding="utf-8"))
        value["items"][0]["proposed"]["title"] = "Не утверждено"
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        with path.open("ab") as f:
            f.write(b"changed")
    before = rows(runtime)
    with pytest.raises(ProcedureError) as error:
        apply_plan(runtime, bundle, username="admin")
    assert error.value.code in {"PLAN_TAMPERED", "BUNDLE_TAMPERED"}
    assert rows(runtime) == before


def test_rollback_does_not_overwrite_later_editor_changes(runtime, tmp_path):
    bundle, plan = make_plan(runtime, tmp_path)
    apply_plan(runtime, bundle, username="admin")
    with sqlite3.connect(runtime.database_path) as c:
        c.execute("UPDATE contents SET title='Сохранённая чужая правка' WHERE id='album'")
    before = rows(runtime)
    with pytest.raises(ProcedureError) as error:
        rollback_operation(runtime, plan["operation_id"], username="admin")
    assert error.value.code == "CONTENT_CHANGED"
    assert rows(runtime) == before


def test_sql_failure_rolls_back_every_content_and_journal_row(runtime, tmp_path, monkeypatch):
    import server.migration_procedure as procedure
    bundle, plan = make_plan(runtime, tmp_path)
    before = rows(runtime)
    original = procedure.record_audit
    count = 0
    def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("simulated failure inside the transaction")
        return original(*args, **kwargs)
    monkeypatch.setattr(procedure, "record_audit", fail_second)
    with pytest.raises(RuntimeError):
        apply_plan(runtime, bundle, username="admin")
    assert rows(runtime) == before
    assert operation_status(runtime, plan["operation_id"])["status"] == "not_applied"


def test_commit_can_be_resumed_after_process_interruption(runtime, tmp_path, monkeypatch):
    import server.migration_procedure as procedure
    bundle, plan = make_plan(runtime, tmp_path)
    original = procedure._verify_operation
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("simulated process exit after commit")
    monkeypatch.setattr(procedure, "_verify_operation", interrupted)
    with pytest.raises(KeyboardInterrupt):
        apply_plan(runtime, bundle, username="admin")
    assert operation_status(runtime, plan["operation_id"])["status"] == "applied"
    after_commit = rows(runtime)
    monkeypatch.setattr(procedure, "_verify_operation", original)
    assert apply_plan(runtime, bundle, username="admin")["status"] == "verified"
    assert rows(runtime) == after_commit


def test_failed_postcheck_automatically_restores_content(runtime, tmp_path, monkeypatch):
    import server.migration_procedure as procedure
    bundle, plan = make_plan(runtime, tmp_path)
    before = rows(runtime)
    def fail(*args, **kwargs):
        raise ProcedureError("CHECK_FAILED", "simulated postcheck failure")
    monkeypatch.setattr(procedure, "_verify_operation", fail)
    with pytest.raises(ProcedureError) as error:
        apply_plan(runtime, bundle, username="admin")
    assert error.value.code == "POSTCHECK_FAILED_ROLLED_BACK"
    assert operation_status(runtime, plan["operation_id"])["status"] == "rolled_back"
    for identifier, value in rows(runtime).items():
        assert value["data_json"] == before[identifier]["data_json"]
        assert value["status"] == before[identifier]["status"]
        assert value["migration_review_required"] == before[identifier]["migration_review_required"]


def test_parallel_procedure_is_blocked_by_os_lock(runtime, tmp_path):
    from server.migration_procedure import _procedure_lock
    bundle, _ = make_plan(runtime, tmp_path)
    with _procedure_lock(runtime), pytest.raises(ProcedureError) as error:
        apply_plan(runtime, bundle, username="admin")
    assert error.value.code == "OPERATION_BUSY"


def add_pdf_issue(runtime):
    from PIL import Image
    from server.media_library import index_library
    Image.new("RGB", (60, 80), "white").save(runtime.media_dir / "issue.pdf", "PDF")
    with sqlite3.connect(runtime.database_path) as c:
        c.execute("""INSERT INTO contents(id,content_type,slug,title,status,data_json,migration_review_required,version,created_at,updated_at)
            VALUES('issue','leaflet_issue','issue','Листок','draft',?,1,1,'created','updated')""",
                  (json.dumps({"number": 1, "period": "2024", "publication_date": "2024-05-12", "pdf": "/media/issue.pdf", "cover": ""}),))
    index_library(runtime.database_path, runtime.media_dir, missing_report=runtime.missing_report_path)


def test_generated_media_is_verified_and_retained_on_rollback(runtime, tmp_path):
    add_pdf_issue(runtime)
    bundle, plan = make_plan(runtime, tmp_path)
    assert len(plan["media"]) == 1
    assert apply_plan(runtime, bundle, username="admin")["status"] == "verified"
    asset = plan["media"][0]
    target = runtime.media_dir / asset["relative"]
    assert target.is_file()
    assert json.loads(rows(runtime)["issue"]["data_json"])["cover"].startswith("/media/generated/")
    rollback_operation(runtime, plan["operation_id"], username="admin")
    assert target.is_file()
    assert json.loads(rows(runtime)["issue"]["data_json"])["cover"] == ""
    with sqlite3.connect(runtime.database_path) as c:
        assert c.execute("SELECT COUNT(*) FROM media_events WHERE action='migration_policy_media_retained_on_rollback'").fetchone()[0] == 1


def test_precommit_crash_recovers_generated_files_without_duplication(runtime, tmp_path, monkeypatch):
    import server.migration_procedure as procedure
    add_pdf_issue(runtime)
    bundle, plan = make_plan(runtime, tmp_path)
    original = procedure._write_content
    before = rows(runtime)
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("crash after file creation and before SQL commit")
    monkeypatch.setattr(procedure, "_write_content", interrupted)
    with pytest.raises(KeyboardInterrupt):
        apply_plan(runtime, bundle, username="admin")
    assert rows(runtime) == before
    assert operation_status(runtime, plan["operation_id"])["status"] == "not_applied"
    assert (runtime.media_dir / plan["media"][0]["relative"]).exists()
    monkeypatch.setattr(procedure, "_write_content", original)
    assert apply_plan(runtime, bundle, username="admin")["status"] == "verified"
    with sqlite3.connect(runtime.database_path) as c:
        assert c.execute("SELECT COUNT(*) FROM media WHERE source='migration_preparation'").fetchone()[0] == 1


def test_resealed_plan_payload_must_still_match_projection(runtime, tmp_path):
    from server.migration_procedure import _hash
    bundle, plan = make_plan(runtime, tmp_path)
    plan["items"][0]["proposed"]["title"] = "Изменено после анализа"
    plan["seal"] = _hash({k: v for k, v in plan.items() if k != "seal"})
    (bundle / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ProcedureError) as error:
        apply_plan(runtime, bundle, username="admin")
    assert error.value.code == "PLAN_INVALID"


def test_expired_plan_is_rejected(runtime, tmp_path, monkeypatch):
    import server.migration_procedure as procedure
    from datetime import datetime, timedelta
    bundle, _ = make_plan(runtime, tmp_path)
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=2)
    monkeypatch.setattr(procedure, "datetime", Later)
    with pytest.raises(ProcedureError) as error:
        apply_plan(runtime, bundle, username="admin")
    assert error.value.code == "PLAN_EXPIRED"


@pytest.mark.parametrize("action", [verify_operation, rollback_operation])
def test_editor_cannot_verify_or_rollback(runtime, tmp_path, action):
    bundle, plan = make_plan(runtime, tmp_path)
    apply_plan(runtime, bundle, username="admin")
    before = rows(runtime)
    with pytest.raises(ProcedureError) as error:
        action(runtime, plan["operation_id"], username="editor")
    assert error.value.code == "FORBIDDEN"
    assert rows(runtime) == before


def test_archived_news_disappears_from_public_snapshot_and_rollback_restores_it(runtime, tmp_path):
    from server.public_site import published_by_slug
    news = rows(runtime)["news"]
    snapshot = {"id": "news", "content_type": "news", "slug": "news", "title": "news", "data": json.loads(news["data_json"])}
    with sqlite3.connect(runtime.database_path) as c:
        c.execute("INSERT INTO revisions(content_id,version,snapshot_json,actor_id,created_at) VALUES('news',1,?,'admin','created')", (json.dumps(snapshot),))
        c.execute("UPDATE contents SET status='published',published_slug='news',published_version=1,published_at='2024-01-01' WHERE id='news'")
    assert published_by_slug(runtime.database_path, "news") is not None
    bundle, plan = make_plan(runtime, tmp_path)
    apply_plan(runtime, bundle, username="admin")
    assert published_by_slug(runtime.database_path, "news") is None
    rollback_operation(runtime, plan["operation_id"], username="admin")
    assert published_by_slug(runtime.database_path, "news") is not None
    assert rows(runtime)["news"]["published_version"] == 1


def test_existing_archive_batch_is_not_silently_overridden(runtime, tmp_path):
    with sqlite3.connect(runtime.database_path) as c:
        c.execute("INSERT INTO migration_audit_runs(id,rules_version,status,created_at) VALUES('old-audit','1.2.0','completed','old')")
        c.execute("""INSERT INTO migration_review_batches(id,name,kind,sample_seed,status,created_at,updated_at)
            VALUES('batch','Партия','archive','seed','draft','old','old')""")
        c.execute("INSERT INTO migration_review_batch_items(batch_id,content_id,content_version,audit_run_id) VALUES('batch','news',1,'old-audit')")
    with pytest.raises(ProcedureError) as error:
        make_plan(runtime, tmp_path)
    assert error.value.code == "ACTIVE_BATCH"
    assert rows(runtime)["news"]["status"] == "draft"
