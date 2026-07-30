from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from server.baseline import database_report, media_summary, readonly_connection
from server.config import Settings
from server.db import init_database
from server.media_reconciliation import run_reconciliation

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
    assert migrated["schema_version"] == 11
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
    with readonly_connection(source_db) as source, sqlite3.connect(target) as destination:
        source.backup(destination)
    init_database(target)
    settings = Settings(
        root=ROOT,
        site_dir=ROOT / "site",
        database_path=target,
        media_dir=source_media,
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
