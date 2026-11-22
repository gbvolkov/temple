from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from server.baseline import database_report, media_summary, readonly_connection
from server.config import Settings
from server.db import init_database
from server.media_reconciliation import run_reconciliation
from server.migration_acceptance import (
    automatic_acceptance, automatic_acceptance_candidates, execute_audit_run,
    queue_audit, rollback_automatic_acceptance,
)
from server.migration_preparation import (
    PreparationError,
    execute_preparation_run,
    list_preparation_items,
    queue_preparation,
    verify_preparation,
)

from .conftest import ROOT, observe_open_defect


def stable_database_metrics(report: dict) -> dict:
    return {
        "quick_check": report["quick_check"],
        "foreign_key_error_count": report["foreign_key_error_count"],
        "metrics": report["metrics"],
        "review_required": report["review_required"],
        "by_status": report["by_status"],
        "by_type": report["by_type"],
        "by_type_status": report["by_type_status"],
    }


@pytest.mark.source_data
def test_source_database_is_copied_and_migrated_without_mutation(pytestconfig, tmp_path):
    source_db: Path | None = pytestconfig.getoption("--cms-source-db")
    source_media: Path | None = pytestconfig.getoption("--cms-source-media")
    if source_db is None or source_media is None:
        pytest.skip("Pass --cms-source-db and --cms-source-media explicitly")
    source_db = source_db.resolve()
    source_media = source_media.resolve()
    assert source_db.is_file()
    assert source_media.is_dir()
    before = stable_database_metrics(database_report(source_db))
    media_before = media_summary(source_media)
    target = tmp_path / "source-copy.sqlite3"
    with readonly_connection(source_db) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    init_database(target)
    migrated = database_report(target)
    assert migrated["schema_version"] == 15
    assert migrated["quick_check"] == ["ok"]
    assert migrated["foreign_key_error_count"] == 0
    after = stable_database_metrics(database_report(source_db))
    assert after == before
    assert media_summary(source_media) == media_before


def source_paths(pytestconfig):
    source_db: Path | None = pytestconfig.getoption("--cms-source-db")
    source_media: Path | None = pytestconfig.getoption("--cms-source-media")
    if source_db is None or source_media is None:
        pytest.skip("Pass --cms-source-db and --cms-source-media explicitly")
    return source_db.resolve(), source_media.resolve()


@pytest.fixture(scope="module")
def reconciled_source(pytestconfig, tmp_path_factory):
    source_db, source_media = source_paths(pytestconfig)
    before_database = stable_database_metrics(database_report(source_db))
    before_media = media_summary(source_media)
    target_root = tmp_path_factory.mktemp("cms-source-reconciliation")
    target = target_root / "source-copy.sqlite3"
    target_media = target_root / "media"
    with readonly_connection(source_db) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    shutil.copytree(source_media, target_media, copy_function=os.link)
    init_database(target)
    settings = Settings(
        root=ROOT,
        site_dir=ROOT / "site",
        database_path=target,
        media_dir=target_media,
        media_derivatives_dir=target_root / "media-derivatives",
        schema_path=ROOT / "site" / "cms-schema.json",
        legacy_sections_path=ROOT / "current-sections.json",
        legacy_crawl_path=None,
        media_manifest_path=ROOT / "data" / "legacy-media-manifest.json",
        missing_media_report_path=ROOT / "outputs" / "missing-legacy-media.csv",
        environment="test",
        bootstrap_user="admin",
        bootstrap_password="source-copy-only",
    )
    result = run_reconciliation(settings, apply=True)
    assert stable_database_metrics(database_report(source_db)) == before_database
    assert media_summary(source_media) == before_media
    result["_settings"] = settings
    result["_source_database_before"] = before_database
    result["_source_media_before"] = before_media
    result["_source_database"] = source_db
    result["_source_media"] = source_media
    return result


@pytest.mark.source_data
@pytest.mark.defect("GAP-01")
def test_gap_01_real_migration_queue(pytestconfig, defect_registry):
    source_db, _ = source_paths(pytestconfig)
    remaining = database_report(source_db)["review_required"]
    observe_open_defect(
        defect_registry, "GAP-01", bad=remaining > 0, good=remaining == 0,
        detail=f"migration_review_required={remaining}",
    )


@pytest.mark.source_data
def test_gap_02_report_matches_cms_queue(reconciled_source):
    with (ROOT / "outputs" / "missing-legacy-media.csv").open(encoding="utf-8", newline="") as handle:
        report_count = sum(1 for _ in csv.DictReader(handle))
    reconciliation = reconciled_source["reconciliation"]
    assert report_count == reconciliation["manifest_errors"] == 201
    assert reconciliation["missing_report_rows"] == reconciliation["missing_issues"] == 201
    assert reconciliation["report_manifest_mismatch"] == 0
    assert reconciliation["issue_report_mismatch"] == 0


@pytest.mark.source_data
def test_gap_03_manifest_matches_filesystem(reconciled_source):
    reconciliation = reconciled_source["reconciliation"]
    assert reconciliation["manifest_urls"] == 13_746
    assert reconciliation["manifest_unique_files"] == 13_698
    assert reconciliation["duplicate_aliases"] == reconciliation["duplicate_groups"] == 48
    assert reconciliation["physical_files"] == 13_699
    assert reconciliation["unmanaged_files"] == 1
    assert reconciliation["manifest_urls"] == (
        reconciliation["manifest_unique_files"] + reconciliation["duplicate_aliases"]
    )
    assert reconciliation["physical_files"] == (
        reconciliation["manifest_unique_files"] + reconciliation["unmanaged_files"]
    )
    assert reconciliation["missing_manifest_files"] == 0
    assert reconciliation["unregistered_files"] == 0
    assert reconciliation["content_references"] > 0
    assert reconciliation["content_reference_files"] > 0
    assert reconciliation["content_reference_issues"] == 0
    assert reconciliation["unexplained_count"] == 0


@pytest.fixture(scope="module")
def prepared_source(reconciled_source):
    settings: Settings = reconciled_source["_settings"]
    source_db: Path = reconciled_source["_source_database"]
    source_media: Path = reconciled_source["_source_media"]
    before = database_report(settings.database_path)
    audit = queue_audit(
        settings.database_path,
        actor_id=None,
        scope={"check_external": False},
    )
    completed_audit = execute_audit_run(
        settings.database_path,
        settings.schema_path,
        settings.media_dir,
        settings.site_dir,
        audit["id"],
        check_external=False,
    )
    assert completed_audit["status"] == "completed"

    queued_preview = queue_preparation(settings.database_path, actor_id=None)
    preview = execute_preparation_run(settings, queued_preview["id"])
    assert preview["status"] == "completed"
    assert preview["counts"]["contents"] == before["review_required"]
    assert preview["counts"].get("technical_blocker", 0) == 0
    planned = list_preparation_items(
        settings.database_path, preview["id"], limit=200, offset=0
    )
    assert planned["total"] == before["review_required"]

    queued_apply = queue_preparation(
        settings.database_path,
        actor_id=None,
        mode="apply",
        preview_run_id=preview["id"],
    )
    applied = execute_preparation_run(settings, queued_apply["id"])
    after = database_report(settings.database_path)

    post_audit = queue_audit(
        settings.database_path,
        actor_id=None,
        scope={"check_external": False},
    )
    completed_post_audit = execute_audit_run(
        settings.database_path,
        settings.schema_path,
        settings.media_dir,
        settings.site_dir,
        post_audit["id"],
        check_external=False,
    )
    assert completed_post_audit["status"] == "completed"
    assert stable_database_metrics(database_report(source_db)) == reconciled_source["_source_database_before"]
    assert media_summary(source_media) == reconciled_source["_source_media_before"]
    return {
        "settings": settings,
        "before": before,
        "completed_audit": completed_audit,
        "preview": preview,
        "planned": planned,
        "applied": applied,
        "after": after,
        "post_audit": completed_post_audit,
    }


@pytest.mark.source_data
def test_stage6_real_source_automatic_preparation(prepared_source):
    before = prepared_source["before"]
    completed_audit = prepared_source["completed_audit"]
    preview = prepared_source["preview"]
    planned = prepared_source["planned"]
    applied = prepared_source["applied"]
    after = prepared_source["after"]
    post_audit = prepared_source["post_audit"]
    assert completed_audit["status"] == "completed"
    assert preview["status"] == "completed"
    assert preview["counts"]["contents"] == before["review_required"]
    assert preview["counts"].get("technical_blocker", 0) == 0
    assert planned["total"] == before["review_required"]
    assert applied["status"] == "completed"
    assert applied["counts"].get("technical_blocker", 0) == 0
    assert after["review_required"] == before["review_required"]
    assert after["quick_check"] == ["ok"]
    assert after["foreign_key_error_count"] == 0
    assert post_audit["status"] == "completed"


@pytest.mark.source_data
def test_cms_19_auto_ready_is_not_semantically_clean(prepared_source, defect_registry):
    settings: Settings = prepared_source["settings"]
    applied = prepared_source["applied"]
    post_audit = prepared_source["post_audit"]
    with sqlite3.connect(settings.database_path) as connection:
        total_unicode = connection.execute(
            """SELECT COUNT(*) FROM migration_review_issues
               WHERE audit_run_id=? AND status='open' AND code='unicode_not_nfkc'""",
            (post_audit["id"],),
        ).fetchone()[0]
        auto_ready_unicode = connection.execute(
            """SELECT COUNT(DISTINCT pi.content_id)
               FROM migration_preparation_items pi
               JOIN migration_review_issues issue
                 ON issue.content_id=pi.content_id
                AND issue.audit_run_id=?
                AND issue.status='open'
                AND issue.code='unicode_not_nfkc'
               WHERE pi.run_id=? AND pi.classification='auto_ready'""",
            (post_audit["id"], applied["id"]),
        ).fetchone()[0]
    assert defect_registry["CMS-19"]["status"] == "fixed"
    assert auto_ready_unicode == 0 and total_unicode == 0, (
        f"auto_ready_unicode_not_nfkc={auto_ready_unicode}; "
        f"total_unicode_not_nfkc={total_unicode}"
    )


@pytest.mark.source_data
def test_cms_20_verify_fails_closed_on_post_apply_violations(prepared_source, defect_registry):
    settings: Settings = prepared_source["settings"]
    post_audit = prepared_source["post_audit"]
    try:
        verification = verify_preparation(settings.database_path)
    except PreparationError as error:
        verification = {
            "ok": False,
            "error": str(error),
            "code": error.code,
            "details": error.details,
        }
    with sqlite3.connect(settings.database_path) as connection:
        counts = dict(
            connection.execute(
                """SELECT severity,COUNT(*) FROM migration_review_issues
                   WHERE audit_run_id=? AND status='open'
                   GROUP BY severity""",
                (post_audit["id"],),
            )
        )
    blockers = counts.get("blocker", 0)
    warnings = counts.get("warning", 0)
    verify_ok = verification.get("ok") is True
    assert defect_registry["CMS-20"]["status"] == "fixed"
    assert verify_ok, (
        f"verify_ok={verify_ok}; blockers={blockers}; warnings={warnings}; "
        f"verification={verification}"
    )


@pytest.mark.source_data
def test_stage7_automatic_acceptance_on_source_copy(prepared_source, tmp_path):
    settings: Settings = prepared_source["settings"]
    target = tmp_path / "acceptance-copy.sqlite3"
    with readonly_connection(settings.database_path) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    with sqlite3.connect(target) as connection:
        before = {row[0]: row[1:] for row in connection.execute(
            "SELECT id,status,version,published_version,title,data_json,migration_review_required FROM contents"
        )}
        actor = connection.execute("SELECT id FROM users WHERE role='admin' AND is_active=1 LIMIT 1").fetchone()
    operations = []
    accepted = set()
    while True:
        candidates = automatic_acceptance_candidates(target)
        assert candidates["ready"], candidates
        if not candidates["items"]:
            break
        selected = [{"id": row["id"], "version": row["version"]} for row in candidates["items"]]
        assert not (accepted & {row["id"] for row in selected})
        operation_id = f"source-copy-{len(operations)}"
        result = automatic_acceptance(target, operation_id=operation_id, items=selected, actor_id=actor[0] if actor else None)
        assert result["count"] == len(selected)
        operations.append((operation_id, selected))
        accepted.update(row["id"] for row in selected)
    assert accepted, "Source copy must contain auto-ready materials"
    with sqlite3.connect(target) as connection:
        after = {row[0]: row[1:] for row in connection.execute(
            "SELECT id,status,version,published_version,title,data_json,migration_review_required FROM contents"
        )}
        for content_id, original in before.items():
            assert after[content_id][:-1] == original[:-1]
            assert after[content_id][-1] == (0 if content_id in accepted else original[-1])
    operation_id, selected = operations[0]
    assert rollback_automatic_acceptance(target, operation_id=operation_id, actor_id=actor[0] if actor else None)["status"] == "rolled_back"
    with sqlite3.connect(target) as connection:
        assert all(connection.execute("SELECT migration_review_required FROM contents WHERE id=?", (row["id"],)).fetchone()[0] == 1 for row in selected)
