from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from server.config import Settings
from server.db import init_database
from server.media_reconciliation import (
    MediaReconciliationError,
    reconcile_media,
    run_reconciliation,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_HEADER = "url,error,directory,references,fallback_mirrored,fallback_url,materials\n"


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        root=ROOT,
        site_dir=ROOT / "site",
        database_path=tmp_path / "cms.sqlite3",
        media_dir=tmp_path / "media",
        media_derivatives_dir=tmp_path / "media-derivatives",
        schema_path=ROOT / "site" / "cms-schema.json",
        legacy_sections_path=ROOT / "current-sections.json",
        legacy_crawl_path=None,
        media_manifest_path=tmp_path / "legacy-media-manifest.json",
        missing_media_report_path=tmp_path / "missing-legacy-media.csv",
        environment="test",
        bootstrap_user="admin",
        bootstrap_password="test-password",
    )


def write_manifest(settings: Settings, entries: dict, errors: list[dict] | None = None) -> None:
    settings.media_manifest_path.write_text(
        json.dumps({"entries": entries, "errors": errors or []}, ensure_ascii=False),
        encoding="utf-8",
    )


def insert_content(database_path: Path, content_id: str, data: dict) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO contents(
                 id,content_type,slug,title,status,data_json,version,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,1,'created','updated')""",
            (
                content_id,
                "page",
                content_id,
                content_id,
                "draft",
                json.dumps(data, ensure_ascii=False),
            ),
        )


def test_reconciliation_classifies_duplicates_missing_unmanaged_and_unsafe_paths(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    (settings.media_dir / "legacy" / "aa").mkdir(parents=True)
    (settings.media_dir / "legacy" / "aa" / "same.txt").write_text("same", encoding="utf-8")
    (settings.media_dir / "outside.txt").write_text("outside", encoding="utf-8")
    (settings.derivatives_dir / "media-id").mkdir(parents=True)
    (settings.derivatives_dir / "media-id" / "thumb.webp").write_bytes(b"thumb")
    write_manifest(
        settings,
        {
            "https://legacy/one.txt": {"status": "mirrored", "stored_path": "aa/same.txt"},
            "https://legacy/alias.txt": {"status": "mirrored", "stored_path": "aa/same.txt"},
            "https://legacy/missing.txt": {"status": "mirrored", "stored_path": "bb/missing.txt"},
            "https://legacy/unsafe.txt": {"status": "mirrored", "stored_path": "../escape.txt"},
        },
        [{"url": "https://legacy/lost.jpg", "error": "404"}],
    )
    settings.missing_report_path.write_text(
        REPORT_HEADER
        + "https://legacy/other.jpg,404,/legacy,1,False,,\n",
        encoding="utf-8",
    )
    insert_content(
        settings.database_path,
        "content-with-missing-media",
        {"asset": "/media/legacy/not-registered.jpg"},
    )
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """INSERT INTO missing_media_issues(
                 id,source_url,error,source_directory,reference_count,status,version,
                 created_at,updated_at
               ) VALUES('stale','https://legacy/stale.jpg','404','',0,'pending',1,'now','now')"""
        )

    result = reconcile_media(
        settings.database_path,
        settings.media_dir,
        settings.media_manifest_path,
        missing_report=settings.missing_report_path,
        derivatives_dir=settings.derivatives_dir,
    )

    assert result["manifest_urls"] == 4
    assert result["manifest_unique_files"] == 2
    assert result["duplicate_aliases"] == result["duplicate_groups"] == 1
    assert result["missing_manifest_files"] == 1
    assert result["unmanaged_files"] == 1
    assert result["unregistered_files"] == 2
    assert result["unsafe_manifest_paths"] == 1
    assert result["report_manifest_mismatch"] == 2
    assert result["issue_report_mismatch"] == 2
    assert result["content_references"] == 1
    assert result["content_unregistered_files"] == 1
    assert result["content_missing_files"] == 1
    assert result["content_reference_issues"] == 2
    assert result["derivative_files"] == 1
    assert result["orphan_derivative_files"] == 1
    assert result["status"] == "warning"
    assert all(not Path(value).is_absolute() for values in result["samples"].values() for value in values)


def test_reconciliation_apply_is_idempotent_and_explains_non_manifest_file(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    (settings.media_dir / "legacy" / "aa").mkdir(parents=True)
    (settings.media_dir / "legacy" / "aa" / "archive.txt").write_text("archive", encoding="utf-8")
    (settings.media_dir / "upload.txt").write_text("upload", encoding="utf-8")
    write_manifest(
        settings,
        {
            "https://legacy/archive.txt": {
                "status": "mirrored",
                "stored_path": "aa/archive.txt",
            }
        },
        [{"url": "https://legacy/lost.jpg", "error": "404"}],
    )
    settings.missing_report_path.write_text(
        REPORT_HEADER
        + "https://legacy/lost.jpg,404,/legacy,1,False,,\n",
        encoding="utf-8",
    )
    insert_content(
        settings.database_path,
        "content-with-media",
        {"asset": "/media/legacy/aa/archive.txt"},
    )

    before = settings.database_path.read_bytes()
    dry_run = run_reconciliation(settings, apply=False)
    assert dry_run["applied"] is False
    assert dry_run["reconciliation"]["content_unregistered_files"] == 1
    assert settings.database_path.read_bytes() == before

    first = run_reconciliation(settings, apply=True)
    second = run_reconciliation(settings, apply=True)

    assert first["reconciliation"]["status"] == "ok"
    assert second["reconciliation"]["status"] == "ok"
    assert second["reconciliation"]["manifest_urls"] == 1
    assert second["reconciliation"]["manifest_unique_files"] == 1
    assert second["reconciliation"]["physical_files"] == 2
    assert second["reconciliation"]["unmanaged_files"] == 1
    assert second["reconciliation"]["registered_files"] == 2
    assert second["reconciliation"]["unregistered_files"] == 0
    assert second["reconciliation"]["missing_issues"] == 1
    assert second["reconciliation"]["report_manifest_mismatch"] == 0
    assert second["reconciliation"]["issue_report_mismatch"] == 0
    assert second["reconciliation"]["content_references"] == 1
    assert second["reconciliation"]["content_reference_files"] == 1
    assert second["reconciliation"]["content_reference_issues"] == 0
    assert second["reconciliation"]["usage_rows"] == 1
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM missing_media_issues").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM media_usages").fetchone()[0] == 1


def test_reconciliation_rejects_malformed_manifest_without_changing_database(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    settings.media_manifest_path.write_text("{broken", encoding="utf-8")
    before = settings.database_path.read_bytes()

    with pytest.raises(MediaReconciliationError, match="Manifest"):
        run_reconciliation(settings, apply=False)

    assert settings.database_path.read_bytes() == before
