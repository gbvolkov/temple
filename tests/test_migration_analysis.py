from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from server.config import Settings
from server.db import init_database
from server.migration_policy import gallery_date_proposal, news_archive_proposal
from server.migration_analysis import analyze_migration, render_markdown


ROOT = Path(__file__).resolve().parents[1]


def content(kind="gallery", url="/albums/2024/meeting.html", value="2000-01-01", title="Встреча"):
    return {
        "id": "example", "content_type": kind, "title": title, "legacy_url": url,
        "migration_review_required": True,
        "data": {"event_date" if kind == "gallery" else "publication_date": value},
    }


@pytest.mark.parametrize("url,title,before,after,precision", [
    ("/albums/2024/meeting.html", "Встреча", "2000-01-01", "2024-01-01", "year"),
    ("/albums/2024/meeting.html", "Встреча 12 мая", "2000-05-12", "2024-05-12", "day"),
    ("/albums/2024/12.05.2024.html", "Встреча", "2023-05-12", "2024-05-12", "day"),
    ("/albums/2024/2024.05.12.html", "Встреча", "2000-01-01", "2024-05-12", "day"),
    ("/albums/01.05.16.html", "Встреча", "2000-01-01", "2016-05-01", "day"),
    ("/albums/2024/meeting.html", "Встреча 1 января 2024", "2024-01-01", "2024-01-01", "day"),
    ("/albums/2024/meeting.html?year=2025", "Встреча", "2000-01-01", "2024-01-01", "year"),
    ("/albums/2024/%D0%B2%D1%81%D1%82%D1%80%D0%B5%D1%87%D0%B0-12-%D0%BC%D0%B0%D1%8F.html", "Встреча", "2000-01-01", "2024-05-12", "day"),
])
def test_gallery_dates_use_url_year_and_explicit_day_month(url, title, before, after, precision):
    result = gallery_date_proposal(content(url=url, title=title, value=before))
    assert result["after"] == after
    assert result["precision"] == precision
    assert result["accepted"] is True
    assert "gallery_date_year_only" not in {r["code"] for r in result["reasons"]}


@pytest.mark.parametrize("url,title,code", [
    ("/albums/20241/meeting.html", "Встреча", "gallery_year_unresolved"),
    ("/albums/meeting.html", "Встреча", "gallery_year_unresolved"),
    ("/albums/2023/meeting-2024.html", "Встреча", "gallery_year_conflict"),
    ("/albums/2023/29.02.2023.html", "Встреча", "gallery_date_invalid"),
    ("/albums/2024/meeting.html", "Встреча 12 мая и 13 мая", "gallery_day_month_conflict"),
])
def test_ambiguous_gallery_dates_stay_in_queue(url, title, code):
    result = gallery_date_proposal(content(url=url, title=title))
    assert code in {r["code"] for r in result["reasons"]}


def test_url_year_corrects_year_while_preserving_confirmed_day_month():
    item = content(url="/albums/2023/meeting.html", value="2024-05-12")
    item["data"]["event_date_precision"] = "day"
    result = gallery_date_proposal(item)
    assert result["after"] == "2023-05-12"
    assert result["accepted"] is True


def test_conflicting_explicit_day_month_is_not_guessed():
    item = content(url="/albums/2024/13.05.2024.html", value="2024-05-12")
    item["data"]["event_date_precision"] = "day"
    result = gallery_date_proposal(item)
    assert result["after"] == "2024-05-12"
    assert "gallery_day_month_conflict" in {r["code"] for r in result["reasons"]}


def test_cms_70_saved_gallery_date_is_used_when_url_has_no_year():
    item = content(url='/o-hrame/fotogalereya/20241/06-октября.html',
                   value='2024-10-06', title='06 октября - Престольный праздник')
    result = gallery_date_proposal(item)
    assert result['accepted'] is True
    assert result['after'] == '2024-10-06'
    assert result['year'] == 2024 and result['precision'] == 'day'


def test_cms_71_album_date_conflict_keeps_source_evidence(tmp_path):
    from contextlib import closing
    from server.db import connect
    from server.migration_preparation import propose_content

    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, 'album-conflict', 'gallery', '/album.html', '2020-01-01', title='Встречи')
    with sqlite3.connect(settings.database_path) as c:
        data = json.loads(c.execute('SELECT data_json FROM contents').fetchone()[0])
        data['body_text'] = 'Встречи\n12 мая и 13 мая — разные даты.'
        c.execute('UPDATE contents SET data_json=?', (json.dumps(data, ensure_ascii=False),))
    with closing(connect(settings.database_path)) as c:
        row = c.execute('SELECT * FROM contents').fetchone()
        proposal = propose_content(c, row, json.loads(settings.schema_path.read_text(encoding='utf-8')), settings.media_dir)
    assert any(r['code'] == 'date_requires_editor' for r in proposal['reasons'])
    assert proposal['proposed']['data']['body_text'] == data['body_text']


def test_saved_gallery_date_without_url_year_checks_conflicting_days():
    item = content(url='/albums/unknown.html', value='2024-10-06', title='07 октября')
    result = gallery_date_proposal(item)
    assert result['after'] == '2024-10-06'
    assert {r['code'] for r in result['reasons']} == {'gallery_day_month_conflict'}


def test_import_date_placeholder_is_not_used_as_detected_date():
    result = gallery_date_proposal(content(url='/albums/unknown.html', value='2000-01-01'))
    assert {r['code'] for r in result['reasons']} == {'gallery_year_unresolved'}


@pytest.mark.parametrize("value,url,queued,archive", [
    ("2026-08-31", "/news/31.08.2026.html", True, True),
    ("2026-09-01", "/news/01.09.2026.html", True, True),
    ("2027-09-30", "/news/30.09.2027.html", True, True),
    ("2000-01-01", "/news/unknown.html", True, True),
    ("", "/news/unknown.html", True, True),
    ("2025-01-01", "/news/2025/unknown.html", False, False),
])
def test_all_queued_news_are_archived_without_publication(value, url, queued, archive):
    item = content("news", url, value)
    item["migration_review_required"] = queued
    result = news_archive_proposal(item)
    assert result["archive"] is archive
    assert result["publish"] is False


def settings_for(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    manifest = tmp_path / "legacy-media-manifest.json"
    manifest.write_text('{"entries":{},"errors":[]}', encoding="utf-8")
    missing = tmp_path / "missing.csv"
    missing.write_text("url,error,directory,references,fallback_mirrored,fallback_url,materials\n", encoding="utf-8")
    return Settings(
        root=ROOT, site_dir=ROOT / "site", database_path=tmp_path / "source.sqlite3",
        media_dir=media, media_derivatives_dir=tmp_path / "derivatives",
        schema_path=ROOT / "site/cms-schema.json", legacy_sections_path=ROOT / "current-sections.json",
        legacy_crawl_path=None, media_manifest_path=manifest, missing_media_report_path=missing,
        environment="test", bootstrap_user="admin", bootstrap_password="unused",
    )


def insert(settings, identifier, kind, url, date_value, *, queued=1, cover="assets/school-maslenitsa.jpg", title=None):
    data = {
        "event_date" if kind == "gallery" else "publication_date": date_value,
        "category": "Жизнь прихода", "summary": "Описание " + identifier,
        "cover": cover, "cover_alt": "Обложка", "photos": [{"image": "assets/school-maslenitsa.jpg", "alt": "Снимок"}],
        "body": [{"type": "paragraph", "data": {"runs": [{"text": "Текст " + identifier, "marks": []}]}}],
    }
    with sqlite3.connect(settings.database_path) as c:
        c.execute("""INSERT INTO contents(id,content_type,slug,title,status,data_json,legacy_url,
            migration_review_required,version,created_at,updated_at)
            VALUES(?,?,?,?,'draft',?,?,?,1,'now','now')""",
            (identifier, kind, identifier, title or identifier, json.dumps(data, ensure_ascii=False), url, queued))


def test_analyzer_partitions_scope_counts_all_reasons_and_preserves_source(tmp_path):
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, "old", "news", "/news/12.05.2024.html", "2024-05-12", cover="")
    insert(settings, "ready", "gallery", "/albums/2024/12.05.2024.html", "2000-01-01")
    insert(settings, "year", "gallery", "/albums/2024/year.html", "2024-01-01", cover="")
    insert(settings, "accepted-year", "gallery", "/albums/2025/year.html", "2025-01-01", queued=0)
    insert(settings, "outside", "news", "/news/12.05.2024-outside.html", "2024-05-12", queued=0)
    report = analyze_migration(settings, work_root=tmp_path / "analysis-work")
    assert report["source_unchanged"] is True
    summary = report["summary"]
    assert summary["scope"] == 3
    assert summary["archive"] == 1
    assert summary["ready_for_publication"] == 1
    assert summary["corrected_and_ready"] == 1
    assert summary["remaining_queue"] == 1
    assert summary["newly_queued"] == 0
    assert summary["scope"] == sum(summary[k] for k in ("archive", "ready_for_publication", "remaining_queue"))
    reasons = {r["code"]: r for r in report["remaining_reasons"]}
    assert "gallery_date_year_only" not in reasons
    assert reasons["required_field_missing"]["materials"] == 1
    assert all(item["reasons"] for item in report["items"] if item["outcome"] == "remaining_queue")
    assert report["source_before"] == report["source_after"]
    assert "Причины" in render_markdown(report)
    assert len(json.loads(json.dumps(report))["items"]) == 3


def test_unknown_news_date_is_archived_without_rewriting_content(tmp_path):
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, "undated", "news", "/news/unknown.html", "2000-01-01")
    report = analyze_migration(settings, work_root=tmp_path / "analysis-work")
    assert report["summary"]["archive"] == 1
    assert report["summary"]["ready_for_publication"] == 0
    assert report["items"][0]["changed_fields"] == []
    assert report["items"][0]["date_after"] == "2000-01-01"


def test_missing_source_is_not_created(tmp_path):
    settings = settings_for(tmp_path)
    with pytest.raises(FileNotFoundError):
        analyze_migration(settings, work_root=tmp_path / "analysis-work")
    assert not settings.database_path.exists()


def test_cms_63_native_album_draft_is_outside_migration_policy(tmp_path):
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, "native-draft", "gallery", None, "", queued=0)
    report = analyze_migration(settings, work_root=tmp_path / "analysis-work")
    assert report["summary"]["scope"] == 0
    assert report["items"] == []
    assert report["source_unchanged"] is True


def test_cms_62_album_does_not_require_review_of_unused_crawl_text(tmp_path):
    from contextlib import closing
    from server.db import connect
    from server.migration_preparation import propose_content

    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, "album", "gallery", "/albums/2024/12.05.2024.html", "2024-05-12")
    with sqlite3.connect(settings.database_path) as c:
        data = json.loads(c.execute("SELECT data_json FROM contents").fetchone()[0])
        data["body_text"] = "Новости прихода\nТРЕБЫ ОНЛАЙН\nО храме\nЖизнь прихода"
        c.execute("UPDATE contents SET data_json=?", (json.dumps(data, ensure_ascii=False),))
    with closing(connect(settings.database_path)) as c:
        row = c.execute("SELECT * FROM contents").fetchone()
        proposal = propose_content(c, row, json.loads(settings.schema_path.read_text(encoding="utf-8")), settings.media_dir)
    assert "text_extraction_ambiguous" not in {r["code"] for r in proposal["reasons"]}
    assert proposal["classification"] == "auto_ready"
    assert proposal["proposed"]["data"]["photos"] == data["photos"]


@pytest.mark.parametrize("year", [2000, 2024])
def test_album_preparation_accepts_january_first_and_preserves_every_photo(tmp_path, year):
    import shutil
    from contextlib import closing
    from server.db import connect
    from server.media_library import index_library
    from server.migration_acceptance import execute_audit_run, queue_audit
    from server.migration_policy import PHOTO_COLLECTION_POLICY, POLICY_VERSION
    from server.migration_preparation import execute_preparation_run, propose_content, queue_preparation, verify_preparation

    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    shutil.copy2(ROOT / "site/assets/school-maslenitsa.jpg", settings.media_dir / "photo.jpg")
    index_library(settings.database_path, settings.media_dir)
    insert(settings, "complete-album", "gallery", f"/albums/{year}/holiday.html", "2000-01-01", cover="/media/photo.jpg")
    photos = [{"image": "/media/photo.jpg", "alt": "Фото", "caption": str(i), "order": i} for i in (4, 7, 11)]
    with sqlite3.connect(settings.database_path) as c:
        data = json.loads(c.execute("SELECT data_json FROM contents").fetchone()[0])
        data["photos"] = photos
        data["body_text"] = "Новости прихода\nТРЕБЫ ОНЛАЙН\nО храме"
        c.execute("UPDATE contents SET data_json=?", (json.dumps(data, ensure_ascii=False),))
    def audit():
        run = queue_audit(settings.database_path, actor_id=None, scope={"check_external": False})
        return execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir,
                                 settings.site_dir, run["id"], check_external=False)
    audit()
    preview = queue_preparation(settings.database_path, actor_id=None)
    preview = execute_preparation_run(settings, preview["id"])
    assert preview["counts"].get("editorial_review", 0) == 0
    applying = queue_preparation(settings.database_path, actor_id=None, mode="apply", preview_run_id=preview["id"])
    assert execute_preparation_run(settings, applying["id"])["status"] == "completed"
    checked = audit()
    assert checked["counts"].get("blocker", 0) == checked["counts"].get("warning", 0) == 0
    assert verify_preparation(settings.database_path)["ok"] is True
    with closing(connect(settings.database_path)) as c:
        row = c.execute("SELECT * FROM contents").fetchone()
        after = json.loads(row["data_json"])
        assert after["photos"] == photos
        assert after["event_date"] == f"{year}-01-01"
        assert after["event_date_policy_version"] == POLICY_VERSION
        assert after["photo_collection_policy"] == PHOTO_COLLECTION_POLICY
        assert row["status"] == "draft" and row["published_version"] is None
        repeated = propose_content(c, row, json.loads(settings.schema_path.read_text(encoding="utf-8")), settings.media_dir)
        assert repeated["before_hash"] == repeated["proposed_hash"]


def test_preview_created_under_old_rules_is_not_applied(tmp_path):
    from server.migration_acceptance import execute_audit_run, queue_audit
    from server.migration_preparation import PreparationError, execute_preparation_run, queue_preparation

    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, "album", "gallery", "/albums/2024/holiday.html", "2024-01-01")
    run = queue_audit(settings.database_path, actor_id=None, scope={"check_external": False})
    execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir, settings.site_dir,
                      run["id"], check_external=False)
    preview = queue_preparation(settings.database_path, actor_id=None)
    execute_preparation_run(settings, preview["id"])
    with sqlite3.connect(settings.database_path) as c:
        c.execute("UPDATE migration_preparation_runs SET rules_version='1.2.0' WHERE id=?", (preview["id"],))
    with pytest.raises(PreparationError) as error:
        queue_preparation(settings.database_path, actor_id=None, mode="apply", preview_run_id=preview["id"])
    assert error.value.code == "PREVIEW_RULES_CHANGED"


def test_january_first_without_known_year_still_requires_review(tmp_path):
    from server.migration_acceptance import execute_audit_run, queue_audit
    from server.migration_policy import POLICY_VERSION

    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, "unknown-year", "gallery", "/albums/unknown.html", "2000-01-01")
    with sqlite3.connect(settings.database_path) as c:
        data = json.loads(c.execute("SELECT data_json FROM contents").fetchone()[0])
        data.update(event_date_precision="year", event_date_policy_version=POLICY_VERSION)
        c.execute("UPDATE contents SET data_json=?", (json.dumps(data),))
    run = queue_audit(settings.database_path, actor_id=None, scope={"check_external": False})
    execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir,
                      settings.site_dir, run["id"], check_external=False)
    with sqlite3.connect(settings.database_path) as c:
        codes = {row[0] for row in c.execute("SELECT code FROM migration_review_issues WHERE status='open'")}
    assert "fallback_date_2000" in codes
    assert gallery_date_proposal(content(url="/albums/unknown.html"))["accepted"] is False


def test_missing_manifest_fails_instead_of_claiming_readiness(tmp_path):
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    settings.media_manifest_path.unlink()
    with pytest.raises(FileNotFoundError):
        analyze_migration(settings, work_root=tmp_path / "analysis-work")


def test_missing_media_evidence_blocks_readiness(tmp_path):
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, "ready", "gallery", "/albums/2024/12.05.2024.html", "2024-05-12")
    settings.media_manifest_path.write_text(json.dumps({"entries": {"https://legacy.example/missing.jpg":
        {"status": "mirrored", "stored_name": "legacy/missing.jpg"}}, "errors": []}), encoding="utf-8")
    report = analyze_migration(settings, work_root=tmp_path / "analysis-work")
    assert report["summary"]["ready_for_publication"] == 0
    assert "media_inventory_unreconciled" in {r["code"] for r in report["items"][0]["reasons"]}


def test_analyzer_is_repeatable_and_does_not_accept_materials(tmp_path):
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, "year", "gallery", "/albums/2024/year.html", "2024-01-01")
    reports = [analyze_migration(settings, work_root=tmp_path / "analysis-work") for _ in range(2)]
    assert reports[0]["summary"] == reports[1]["summary"]
    assert reports[0]["items"] == reports[1]["items"]
    with sqlite3.connect(settings.database_path) as c:
        assert c.execute("SELECT migration_review_required,status,version FROM contents").fetchone() == (1, "draft", 1)
    assert list((tmp_path / "analysis-work").iterdir()) == []


def test_audit_reports_every_missing_field_and_distinct_material_count(tmp_path):
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, "missing", "gallery", "/albums/2024/12.05.2024.html", "2024-05-12", cover="")
    with sqlite3.connect(settings.database_path) as c:
        row = c.execute("SELECT data_json FROM contents").fetchone()
        data = json.loads(row[0])
        data["photos"] = []
        c.execute("UPDATE contents SET data_json=?", (json.dumps(data),))
    report = analyze_migration(settings, work_root=tmp_path / "analysis-work")
    reason = next(r for r in report["remaining_reasons"] if r["code"] == "required_field_missing")
    assert reason["materials"] == 1
    assert reason["occurrences"] == 2
    assert reason["fields"] == {"cover": 1, "photos": 1}
    assert reason["categories"] == ["editorial"]


def test_manual_date_with_revision_evidence_is_preserved(tmp_path):
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, "manual", "gallery", "/albums/2024/meeting.html", "2024-05-12")
    with sqlite3.connect(settings.database_path) as c:
        c.execute("INSERT INTO users(id,username,password_hash,role,created_at) VALUES('editor','editor','unused','editor','now')")
        original = {"title": "manual", "data": {"event_date": "2024-01-01"}}
        edited = {"title": "manual", "data": {"event_date": "2024-05-12"}}
        c.execute("INSERT INTO revisions(content_id,version,snapshot_json,created_at) VALUES('manual',1,?,'now')", (json.dumps(original),))
        c.execute("INSERT INTO revisions(content_id,version,snapshot_json,actor_id,created_at) VALUES('manual',2,?,'editor','now')", (json.dumps(edited),))
        c.execute("UPDATE contents SET version=2")
    report = analyze_migration(settings, work_root=tmp_path / "analysis-work")
    assert report["items"][0]["date_after"] == "2024-05-12"
    assert report["items"][0]["date_proposal"]["precision"] == "day"


def test_active_batch_is_reported_as_a_separate_reason(tmp_path):
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, "busy", "gallery", "/albums/2024/12.05.2024.html", "2024-05-12")
    with sqlite3.connect(settings.database_path) as c:
        c.execute("INSERT INTO migration_audit_runs(id,rules_version,status,created_at) VALUES('test-audit','1.1.0','completed','now')")
        c.execute("""INSERT INTO migration_review_batches(id,name,kind,filters_json,sample_seed,status,created_at,updated_at)
            VALUES('batch','Партия','priority','{}','seed','draft','now','now')""")
        c.execute("INSERT INTO migration_review_batch_items(batch_id,content_id,content_version,audit_run_id) VALUES('batch','busy',1,'test-audit')")
    report = analyze_migration(settings, work_root=tmp_path / "analysis-work")
    assert "active_review_batch" in {r["code"] for r in report["items"][0]["reasons"]}


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_cli_refuses_overwriting_source_and_sqlite_sidecars(tmp_path, suffix):
    database = tmp_path / "source.sqlite3"
    database.write_bytes(b"source marker")
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "server.migration_analysis",
                             "--database", str(database), "--json", str(database) + suffix],
                            cwd=ROOT, capture_output=True)
    assert result.returncode == 2
    assert database.read_bytes() == b"source marker"


def test_analysis_coverage_contract():
    matrix = json.loads((ROOT / "tests/e2e/coverage.json").read_text(encoding="utf-8"))
    suites = matrix["analysis_suites"]
    assert suites
    ids = [assertion for suite in suites for assertion in suite["assertions"]]
    assert len(ids) == len(set(ids))
    for suite in suites:
        path, function = suite["nodeid"].split("::")
        assert f"def {function}(" in (ROOT / path).read_text(encoding="utf-8")
