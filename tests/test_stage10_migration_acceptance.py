from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings
from server.db import init_database
from server.migration_acceptance import (
    AcceptanceError,
    create_batch,
    execute_audit_run,
    get_batch,
    queue_audit,
    verify_acceptance,
)


ROOT = Path(__file__).resolve().parents[1]


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


def news_data(*, year: int = 2014, legacy: bool = False) -> dict:
    body = []
    if legacy:
        body = [{"id": "legacy-1", "type": "legacy_text", "data": {"text": "Архивный текст"}}]
    return {
        "publication_date": f"{year:04d}-07-20",
        "category": "Новости прихода",
        "summary": "Проверенное краткое описание материала.",
        "cover": "assets/school-maslenitsa.jpg",
        "cover_alt": "Приходской праздник",
        "body": body,
        "related_content": [],
    }


def create_imported_news(client: TestClient, headers: dict[str, str], title: str, *, year: int = 2014, legacy: bool = False) -> dict:
    data = news_data(year=year, legacy=legacy)
    data["summary"] = f"{data['summary']} {title}"
    response = client.post(
        "/api/admin/contents",
        headers=headers,
        json={"content_type": "news", "title": title, "data": data},
    )
    assert response.status_code == 201, response.text
    item = response.json()
    settings = client.app.state.settings
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("UPDATE contents SET migration_review_required=1 WHERE id=?", (item["id"],))
    item["migration_review_required"] = True
    return item


def run_audit(settings: Settings, *, content_id: str | None = None) -> dict:
    scope = {"content_id": content_id, "check_external": False} if content_id else {"check_external": False}
    run = queue_audit(settings.database_path, actor_id=None, scope=scope)
    return execute_audit_run(
        settings.database_path,
        settings.schema_path,
        settings.media_dir,
        settings.site_dir,
        run["id"],
        check_external=False,
    )


def test_migration_9_is_idempotent_and_preserves_existing_rows(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        before = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("contents", "revisions", "users", "media", "redirects", "submissions")
        }
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 10
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    init_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        after = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }
        assert before == after
    assert verify_acceptance(settings.database_path) == {
        "ok": True,
        "schema": 9,
        "rules_version": "1.0.0",
    }


def test_audit_rules_repeat_without_content_changes_and_old_bypass_is_closed(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers, "Техническая дата")
        broken = {
            "publication_date": "2000-01-01",
            "category": "Новости прихода",
            "summary": "",
            "cover": "",
            "cover_alt": "",
            "body": [{"id": "old", "type": "legacy_text", "data": {"text": "Главное меню\nО храме\nЖизнь прихода\nВоскресная школа\nКонтакты"}}],
            "target_url": "/does-not-exist",
        }
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET data_json=? WHERE id=?", (json.dumps(broken, ensure_ascii=False), item["id"]))
            baseline = {
                "contents": connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0],
                "revisions": connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0],
                "version": connection.execute("SELECT version FROM contents WHERE id=?", (item["id"],)).fetchone()[0],
            }

        first = run_audit(settings, content_id=item["id"])
        second = run_audit(settings, content_id=item["id"])
        assert first["counts"]["contents"] == second["counts"]["contents"] == 1
        issues = client.get(f"/api/admin/migration/issues?q={item['title']}").json()["items"]
        codes = {issue["code"] for issue in issues}
        assert {"fallback_date_2000", "required_field_missing", "legacy_text", "legacy_navigation_text"} <= codes
        code_search = client.get("/api/admin/migration/issues?q=required_field_missing&severity=blocker").json()
        assert code_search["total"] >= 1
        assert {issue["code"] for issue in code_search["items"]} == {"required_field_missing"}
        assert next(issue for issue in issues if issue["code"] == "fallback_date_2000")["severity"] == "blocker"
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM migration_review_issues WHERE content_id=? AND status='open'",
                (item["id"],),
            ).fetchone()[0] == len(issues)
            assert baseline == {
                "contents": connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0],
                "revisions": connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0],
                "version": connection.execute("SELECT version FROM contents WHERE id=?", (item["id"],)).fetchone()[0],
            }

        bypass = client.post(
            f"/api/admin/contents/{item['id']}/review",
            headers=headers,
            json={"version": item["version"]},
        )
        assert bypass.status_code == 409
        bulk_bypass = client.post(
            "/api/admin/content-bulk",
            headers=headers,
            json={"action": "review", "items": [{"id": item["id"], "version": item["version"]}]},
        )
        assert bulk_bypass.status_code == 409


def test_batch_sampling_stale_guard_and_atomic_archive(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        items = [create_imported_news(client, headers, f"Архив {index:02d}") for index in range(10)]
        run_audit(settings)
        with sqlite3.connect(settings.database_path) as connection:
            actor_id = connection.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
            revisions_before = connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
        batch = create_batch(
            settings.database_path,
            name="Архив 2014",
            kind="archive",
            content_ids=[item["id"] for item in items],
            actor_id=actor_id,
            sample_rate=0.1,
            batch_id="00000000-0000-4000-8000-000000000010",
        )
        assert batch["progress"]["items"] == 10
        assert batch["progress"]["sampled"] == 1
        assert get_batch(settings.database_path, batch["id"])["items"] == batch["items"]

        stale_item = batch["items"][0]
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("UPDATE contents SET version=version+1 WHERE id=?", (stale_item["content_id"],))
        stale = client.patch(
            f"/api/admin/migration/batches/{batch['id']}/items/{stale_item['content_id']}",
            headers=headers,
            json={"version": stale_item["version"], "manual_reviewed": True, "disposition": "archive", "note": "Дубликат"},
        )
        assert stale.status_code == 409

        cancelled = client.post(
            f"/api/admin/migration/batches/{batch['id']}/cancel",
            headers=headers,
            json={"version": batch["version"]},
        )
        assert cancelled.status_code == 200, cancelled.text

        # Use a fresh, fully audited one-item batch to exercise atomic finalization.
        target = next(item for item in items if item["id"] != stale_item["content_id"])
        run_audit(settings, content_id=target["id"])
        final_batch = create_batch(
            settings.database_path,
            name="Решение по архиву",
            kind="archive",
            content_ids=[target["id"]],
            actor_id=actor_id,
            sample_rate=0.1,
        )
        row = final_batch["items"][0]
        final_batch = client.patch(
            f"/api/admin/migration/batches/{final_batch['id']}/items/{target['id']}",
            headers=headers,
            json={"version": row["version"], "manual_reviewed": True, "disposition": "archive", "note": "Редакционное решение"},
        ).json()
        final_batch = client.post(
            f"/api/admin/migration/batches/{final_batch['id']}/submit",
            headers=headers,
            json={"version": final_batch["version"]},
        ).json()
        finalized = client.post(
            f"/api/admin/migration/batches/{final_batch['id']}/finalize",
            headers=headers,
            json={"version": final_batch["version"], "warning_acknowledgements": {}},
        )
        assert finalized.status_code == 200, finalized.text
        with sqlite3.connect(settings.database_path) as connection:
            status, flag, version = connection.execute(
                "SELECT status,migration_review_required,version FROM contents WHERE id=?",
                (target["id"],),
            ).fetchone()
            assert (status, flag, version) == ("archived", 0, target["version"])
            assert connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == revisions_before


def test_priority_material_is_always_in_manual_sample(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        priority = create_imported_news(client, headers, "Новая приоритетная новость", year=2026)
        archive = [create_imported_news(client, headers, f"Старый материал {index}") for index in range(9)]
        run_audit(settings)
        with sqlite3.connect(settings.database_path) as connection:
            actor_id = connection.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        # Priority batches are manual in full; the year rule is enforced even with a low requested sample.
        batch = create_batch(
            settings.database_path,
            name="Приоритет 2026",
            kind="priority",
            content_ids=[priority["id"], *[item["id"] for item in archive]],
            actor_id=actor_id,
            sample_rate=0.1,
        )
        assert batch["progress"]["sampled"] == 10
        assert all(item["sampled"] for item in batch["items"])


def test_api_roles_csrf_and_pilot_does_not_clear_flags(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        response = client.post(
            "/api/admin/contents",
            headers=headers,
            json={
                "content_type": "page",
                "title": "История храма",
                "data": {"placement": "about_history", "body": []},
            },
        )
        assert response.status_code == 201, response.text
        item = response.json()
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE contents SET migration_review_required=1,legacy_url='/o-hrame/istoriya/' WHERE id=?",
                (item["id"],),
            )
        run_audit(settings)
        assert client.post("/api/admin/migration/batches/pilot").status_code == 403
        pilot = client.post("/api/admin/migration/batches/pilot", headers=headers)
        assert pilot.status_code == 201, pilot.text
        assert len(pilot.json()["items"]) == 1
        assert pilot.json()["status"] == "draft"
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                "SELECT migration_review_required,status FROM contents WHERE id=?", (item["id"],)
            ).fetchone() == (1, "draft")
