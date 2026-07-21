from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from server.baseline import database_report, media_summary, readonly_connection
from server.db import init_database

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
    assert migrated["schema_version"] == 10
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
@pytest.mark.defect("GAP-02")
def test_gap_02_report_matches_cms_queue(pytestconfig, defect_registry):
    source_db, _ = source_paths(pytestconfig)
    with (ROOT / "outputs" / "missing-legacy-media.csv").open(encoding="utf-8", newline="") as handle:
        report_count = sum(1 for _ in csv.DictReader(handle))
    with readonly_connection(source_db) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='missing_media_issues'"
        ).fetchone()
        cms_count = connection.execute("SELECT COUNT(*) FROM missing_media_issues").fetchone()[0] if table else 0
    good = report_count == cms_count
    observe_open_defect(
        defect_registry, "GAP-02", bad=report_count > 0 and cms_count == 0, good=good,
        detail=f"report={report_count}; cms={cms_count}",
    )


@pytest.mark.source_data
@pytest.mark.defect("GAP-03")
def test_gap_03_manifest_matches_filesystem(pytestconfig, defect_registry):
    _, source_media = source_paths(pytestconfig)
    manifest = json.loads((ROOT / "data" / "legacy-media-manifest.json").read_text(encoding="utf-8"))
    mirrored = sum(1 for item in manifest["entries"].values() if item.get("status") == "mirrored")
    physical = media_summary(source_media)["files"]
    good = mirrored == physical
    observe_open_defect(
        defect_registry, "GAP-03", bad=mirrored != physical, good=good,
        detail=f"mirrored={mirrored}; physical={physical}; delta={mirrored - physical}",
    )
