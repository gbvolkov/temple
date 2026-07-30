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
from server.migration_acceptance import execute_audit_run, queue_audit
from server.migration_preparation import (
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
    assert migrated["schema_version"] == 12
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


@pytest.mark.source_data
def test_stage6_real_source_automatic_preparation(reconciled_source):
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
    assert applied["status"] == "completed"
    assert applied["counts"].get("technical_blocker", 0) == 0
    assert verify_preparation(settings.database_path)["run_id"] == applied["id"]
    after = database_report(settings.database_path)
    assert after["review_required"] == before["review_required"]
    assert after["quick_check"] == ["ok"]
    assert after["foreign_key_error_count"] == 0

    post_audit = queue_audit(
        settings.database_path,
        actor_id=None,
        scope={"check_external": False},
    )
    assert execute_audit_run(
        settings.database_path,
        settings.schema_path,
        settings.media_dir,
        settings.site_dir,
        post_audit["id"],
        check_external=False,
    )["status"] == "completed"
    assert stable_database_metrics(database_report(source_db)) == reconciled_source["_source_database_before"]
    assert media_summary(source_media) == reconciled_source["_source_media_before"]
