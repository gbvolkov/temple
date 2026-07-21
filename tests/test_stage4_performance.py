from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings
from server.media_library import index_library
from server.migration_acceptance import execute_audit_run, queue_audit


ROOT = Path(__file__).resolve().parents[1]
MULTIPLIER = float(os.environ.get("CMS_PERF_BUDGET_MULTIPLIER", "1"))


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
        environment="test",
        bootstrap_user="admin",
        bootstrap_password="test-password",
        session_hours=1,
        submission_worker_interval_seconds=3600,
    )


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/admin/login", json={"username": "admin", "password": "test-password"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def _seed_large_content_set(client: TestClient, settings: Settings, count: int = 10_000) -> None:
    headers = login(client)
    created = client.post(
        "/api/admin/contents",
        headers=headers,
        json={
            "content_type": "page",
            "title": "Performance page 00000",
            "data": {"body": [{"id": "p-0", "type": "paragraph", "data": {"text": "Performance 0"}}]},
        },
    )
    assert created.status_code == 201
    with sqlite3.connect(settings.database_path) as connection:
        connection.row_factory = sqlite3.Row
        template = dict(connection.execute("SELECT * FROM contents WHERE id=?", (created.json()["id"],)).fetchone())
        columns = list(template)
        rows = []
        for index in range(1, count):
            item = dict(template)
            item.update({
                "id": f"perf-{index:05d}",
                "slug": f"performance-page-{index:05d}",
                "title": f"Performance page {index:05d}",
                "legacy_url": None,
                "migration_review_required": int(index < 2_000),
                "data_json": json.dumps({
                    "body": [{"id": f"p-{index}", "type": "paragraph", "data": {"text": f"Performance {index}"}}]
                }),
            })
            for nullable_unique in ("published_slug",):
                if nullable_unique in item:
                    item[nullable_unique] = None
            rows.append(tuple(item[column] for column in columns))
        placeholders = ",".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO contents({','.join(columns)}) VALUES({placeholders})",
            rows,
        )


def test_large_content_issue_queries_and_audit_stay_within_budget(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        _seed_large_content_set(client, settings)
        started = time.monotonic()
        page = client.get("/api/admin/content-index?q=Performance&page=1&limit=100&offset=9900")
        assert page.status_code == 200 and len(page.json()["items"]) == 100
        assert time.monotonic() - started <= 2 * MULTIPLIER

        run = queue_audit(settings.database_path, actor_id=None, scope={})
        started = time.monotonic()
        completed = execute_audit_run(
            settings.database_path, settings.schema_path, settings.media_dir, settings.site_dir,
            run["id"], check_external=False,
        )
        assert completed["status"] == "completed"
        assert completed["counts"]["contents"] == 1_999
        assert time.monotonic() - started <= 30 * MULTIPLIER

        with sqlite3.connect(settings.database_path) as connection:
            now = "2026-07-21T00:00:00+00:00"
            connection.executemany(
                """INSERT INTO migration_review_issues(
                       id,audit_run_id,content_id,content_version,code,severity,field_path,fingerprint,
                       status,message,details_json,detected_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        f"perf-issue-{index}", run["id"], f"perf-{index % 1_999 + 1:05d}", 1,
                        "performance_code", "info", "", f"perf-fingerprint-{index}", "open",
                        "Performance issue", "{}", now,
                    )
                    for index in range(10_000)
                ],
            )
        started = time.monotonic()
        issues = client.get("/api/admin/migration/issues?q=performance_code&limit=100")
        assert issues.status_code == 200 and issues.json()["total"] == 10_000
        assert time.monotonic() - started <= 2 * MULTIPLIER


def test_reindex_thousand_files_reports_monotonic_progress_within_budget(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.media_dir.mkdir(parents=True)
    for index in range(1_000):
        (settings.media_dir / f"performance-{index:04d}.txt").write_text(f"file {index}\n", encoding="utf-8")
    from server.db import init_database
    init_database(settings.database_path)
    snapshots: list[tuple[int, int, str]] = []
    started = time.monotonic()
    result = index_library(
        settings.database_path, settings.media_dir, dry_run=True,
        progress=lambda item: snapshots.append((item["processed_files"], item["total_files"], item["phase"])),
    )
    assert result["files"] == result["ready"] == 1_000
    assert snapshots[0] == (0, 1_000, "scanning")
    assert snapshots[-1] == (1_000, 1_000, "scanning")
    assert [item[0] for item in snapshots] == sorted(item[0] for item in snapshots)
    assert time.monotonic() - started <= 30 * MULTIPLIER
