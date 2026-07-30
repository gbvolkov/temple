from __future__ import annotations

import json
import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.config import Settings
from server.migration_acceptance import execute_audit_run, queue_audit
from server.migration_preparation import (
    PreparationError,
    execute_preparation_run,
    get_preparation_run,
    list_preparation_items,
    queue_preparation,
    rollback_preparation,
    verify_preparation,
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
        missing_media_report_path=tmp_path / "missing-legacy-media.csv",
        environment="test",
        bootstrap_user="admin",
        bootstrap_password="test-password",
        submission_worker_interval_seconds=3600,
    )


def login(
    client: TestClient,
    username: str = "admin",
    password: str = "test-password",
) -> dict[str, str]:
    response = client.post(
        "/api/admin/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def create_imported_news(
    client: TestClient,
    headers: dict[str, str],
    *,
    title: str = "Приходская встреча 12.05.2018",
    legacy_url: str = "/news/12-05-2018.html",
) -> dict:
    response = client.post(
        "/api/admin/contents",
        headers=headers,
        json={
            "content_type": "news",
            "title": title,
            "data": {
                "publication_date": "2000-05-12",
                "category": "Новости прихода",
                "summary": "Временное описание",
                "cover": "assets/school-maslenitsa.jpg",
                "cover_alt": title,
                "body": [{"type": "paragraph", "data": {"runs": [{"text": "Временный текст", "marks": []}]}}],
            },
        },
    )
    assert response.status_code == 201, response.text
    item = response.json()
    legacy = {
        "publication_date": "2000-05-12",
        "category": "Новости прихода",
        "summary": "",
        "cover": "assets/school-maslenitsa.jpg",
        "cover_alt": title,
        "body": [{"type": "legacy_text", "text": "Приходская встреча состоялась в храме.\n\nУчастники обсудили планы."}],
        "body_text": "Приходская встреча состоялась в храме.\n\nУчастники обсудили планы.",
        "migration_note": "Импортировано из crawl",
    }
    with sqlite3.connect(client.app.state.settings.database_path) as connection:
        connection.execute(
            """UPDATE contents SET data_json=?,legacy_url=?,migration_review_required=1
               WHERE id=?""",
            (json.dumps(legacy, ensure_ascii=False), legacy_url, item["id"]),
        )
    return item


def full_audit(settings: Settings) -> dict:
    run = queue_audit(
        settings.database_path, actor_id=None, scope={"check_external": False}
    )
    return execute_audit_run(
        settings.database_path,
        settings.schema_path,
        settings.media_dir,
        settings.site_dir,
        run["id"],
        check_external=False,
    )


def preview(settings: Settings) -> dict:
    full_audit(settings)
    run = queue_preparation(settings.database_path, actor_id=None)
    return execute_preparation_run(settings, run["id"])


def test_preview_apply_is_idempotent_and_rollback_is_compensating(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers)
        result = preview(settings)
        assert result["counts"] == {
            "auto_ready": 1,
            "changed": 1,
            "contents": 1,
        }
        planned = list_preparation_items(settings.database_path, result["id"])
        assert planned["total"] == 1
        assert planned["items"][0]["classification"] == "auto_ready"
        assert {step["rule"] for step in planned["items"][0]["transformations"]} >= {
            "date_from_evidence",
            "legacy_text_to_blocks",
            "provenance_to_journal",
            "summary_from_body",
        }

        queued = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=result["id"],
        )
        applied = execute_preparation_run(settings, queued["id"])
        same = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=result["id"],
        )
        assert same["id"] == applied["id"]
        assert verify_preparation(settings.database_path)["run_id"] == applied["id"]
        with sqlite3.connect(settings.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM contents WHERE id=?", (item["id"],)
            ).fetchone()
            data = json.loads(row["data_json"])
            assert row["migration_review_required"] == 1
            assert row["version"] == 2
            assert data["publication_date"] == "2018-05-12"
            assert data["summary"].startswith("Приходская встреча")
            assert data["body"][0]["type"] == "paragraph"
            assert "body_text" not in data
            assert connection.execute(
                "SELECT COUNT(*) FROM revisions WHERE content_id=?", (item["id"],)
            ).fetchone()[0] == 2

        rolled_back = rollback_preparation(
            settings.database_path, applied["id"], actor_id=None
        )
        assert rolled_back["status"] == "rolled_back"
        with sqlite3.connect(settings.database_path) as connection:
            row = connection.execute(
                "SELECT version,migration_review_required,data_json FROM contents WHERE id=?",
                (item["id"],),
            ).fetchone()
            assert row[0:2] == (3, 1)
            assert json.loads(row[2])["publication_date"] == "2000-05-12"


def test_apply_rejects_stale_preview_without_partial_changes(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_imported_news(client, headers)
        result = preview(settings)
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE contents SET version=version+1 WHERE id=?", (item["id"],)
            )
        queued = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=result["id"],
        )
        with pytest.raises(PreparationError, match="устарел"):
            execute_preparation_run(settings, queued["id"])
        assert get_preparation_run(settings.database_path, queued["id"])["status"] == "failed"
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM revisions WHERE content_id=?", (item["id"],)
            ).fetchone()[0] == 1


def test_unresolved_date_and_duplicate_are_editorial_not_technical(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        first = create_imported_news(
            client, headers, title="Встреча прихода", legacy_url="/news/vstrecha-a.html"
        )
        second = create_imported_news(
            client, headers, title="Встреча прихода", legacy_url="/news/vstrecha-b.html"
        )
        result = preview(settings)
        assert result["counts"]["editorial_review"] == 2
        assert result["counts"].get("technical_blocker", 0) == 0
        items = list_preparation_items(
            settings.database_path,
            result["id"],
            classification="editorial_review",
        )["items"]
        assert all(item["recommendation"] for item in items)
        assert all(item["options"] for item in items)
        assert all(
            {"date_requires_editor", "duplicate_title"} <=
            {reason["code"] for reason in item["reasons"]}
            for item in items
        )


def test_cover_reused_as_first_photo_is_not_duplicate_media_issue(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        response = client.post(
            "/api/admin/contents",
            headers=headers,
            json={
                "content_type": "gallery",
                "title": "Альбом без ложного дубля",
                "data": {
                    "event_date": "2018-05-12",
                    "category": "Жизнь прихода",
                    "summary": "",
                    "cover": "assets/school-maslenitsa.jpg",
                    "photos": [
                        {
                            "image": "assets/school-maslenitsa.jpg",
                            "alt": "Праздник",
                            "order": 1,
                        }
                    ],
                },
            },
        )
        assert response.status_code == 201, response.text
        item = response.json()
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE contents SET migration_review_required=1 WHERE id=?",
                (item["id"],),
            )
        full_audit(settings)
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                """SELECT COUNT(*) FROM migration_review_issues
                   WHERE content_id=? AND code='duplicate_media_reference' AND status='open'""",
                (item["id"],),
            ).fetchone()[0] == 0


def test_preparation_api_roles_preview_apply_items_and_rollback(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        admin_headers = login(client)
        item = create_imported_news(client, admin_headers)
        full_audit(settings)
        password = "Strong-Password-2026!"
        for username, role in (("publisher.prepare", "publisher"), ("viewer.prepare", "viewer")):
            response = client.post(
                "/api/admin/users",
                headers=admin_headers,
                json={"username": username, "password": password, "role": role},
            )
            assert response.status_code == 201, response.text

        publisher_headers = login(client, "publisher.prepare", password)
        response = client.post(
            "/api/admin/migration/preparations",
            headers=publisher_headers,
            json={"mode": "preview"},
        )
        assert response.status_code == 202, response.text
        preview_run = execute_preparation_run(settings, response.json()["id"])
        assert preview_run["status"] == "completed"
        forbidden = client.post(
            "/api/admin/migration/preparations",
            headers=publisher_headers,
            json={"mode": "apply", "preview_run_id": preview_run["id"]},
        )
        assert forbidden.status_code == 403

        viewer_headers = login(client, "viewer.prepare", password)
        assert client.get(
            f"/api/admin/migration/preparations/{preview_run['id']}",
            headers=viewer_headers,
        ).status_code == 200
        items = client.get(
            f"/api/admin/migration/preparations/{preview_run['id']}/items"
            "?classification=auto_ready",
            headers=viewer_headers,
        )
        assert items.status_code == 200
        assert items.json()["items"][0]["content_id"] == item["id"]
        assert client.post(
            "/api/admin/migration/preparations",
            headers=viewer_headers,
            json={"mode": "preview"},
        ).status_code == 403

        admin_headers = login(client)
        response = client.post(
            "/api/admin/migration/preparations",
            headers=admin_headers,
            json={"mode": "apply", "preview_run_id": preview_run["id"]},
        )
        assert response.status_code == 202, response.text
        applied = execute_preparation_run(settings, response.json()["id"])
        rollback = client.post(
            f"/api/admin/migration/preparations/{applied['id']}/rollback",
            headers=admin_headers,
        )
        assert rollback.status_code == 200, rollback.text
        assert rollback.json()["status"] == "rolled_back"


def test_leaflet_pdf_cover_is_generated_and_registered(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    stream = io.BytesIO()
    Image.new("RGB", (80, 120), "white").save(stream, "PDF")
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        uploaded = client.post(
            "/api/admin/media",
            headers=headers,
            files={"file": ("leaflet.pdf", stream.getvalue(), "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text
        pdf = uploaded.json()
        created = client.post(
            "/api/admin/contents",
            headers=headers,
            json={
                "content_type": "leaflet_issue",
                "title": "Приходской листок № 1",
                "data": {
                    "number": 1,
                    "period": "Июль 2026",
                    "publication_date": "2026-07-30",
                    "cover": "assets/school-maslenitsa.jpg",
                    "pdf": pdf["url"],
                    "featured": False,
                },
            },
        )
        assert created.status_code == 201, created.text
        content_id = created.json()["id"]
        with sqlite3.connect(settings.database_path) as connection:
            data = json.loads(connection.execute(
                "SELECT data_json FROM contents WHERE id=?", (content_id,)
            ).fetchone()[0])
            data["cover"] = ""
            connection.execute(
                """UPDATE contents SET data_json=?,migration_review_required=1
                   WHERE id=?""",
                (json.dumps(data, ensure_ascii=False), content_id),
            )
        result = preview(settings)
        item = list_preparation_items(settings.database_path, result["id"])["items"][0]
        cover_rule = next(
            rule for rule in item["transformations"]
            if rule["rule"] == "leaflet_pdf_cover"
        )
        apply_run = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=result["id"],
        )
        applied = execute_preparation_run(settings, apply_run["id"])
        assert applied["status"] == "completed"
        generated = settings.media_dir / cover_rule["stored_name"]
        assert generated.is_file()
        with sqlite3.connect(settings.database_path) as connection:
            connection.row_factory = sqlite3.Row
            media = connection.execute(
                "SELECT * FROM media WHERE id=?", (cover_rule["media_id"],)
            ).fetchone()
            content = json.loads(connection.execute(
                "SELECT data_json FROM contents WHERE id=?", (content_id,)
            ).fetchone()[0])
        assert media["mime_type"] == "image/webp"
        assert media["source"] == "migration_preparation"
        assert content["cover"] == cover_rule["url"]


def test_rollback_conflict_stops_before_changing_any_material(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        first = create_imported_news(
            client,
            headers,
            title="Первая встреча 12.05.2018",
            legacy_url="/news/first-12-05-2018.html",
        )
        second = create_imported_news(
            client,
            headers,
            title="Вторая встреча 13.05.2018",
            legacy_url="/news/second-13-05-2018.html",
        )
        result = preview(settings)
        queued = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=result["id"],
        )
        applied = execute_preparation_run(settings, queued["id"])
        with sqlite3.connect(settings.database_path) as connection:
            before_versions = dict(connection.execute(
                "SELECT id,version FROM contents WHERE id IN (?,?)",
                (first["id"], second["id"]),
            ))
            connection.execute(
                "UPDATE contents SET version=version+1 WHERE id=?",
                (second["id"],),
            )
        with pytest.raises(PreparationError, match="изменён после"):
            rollback_preparation(
                settings.database_path,
                applied["id"],
                actor_id=None,
            )
        with sqlite3.connect(settings.database_path) as connection:
            after_versions = dict(connection.execute(
                "SELECT id,version FROM contents WHERE id IN (?,?)",
                (first["id"], second["id"]),
            ))
        assert after_versions[first["id"]] == before_versions[first["id"]]
        assert after_versions[second["id"]] == before_versions[second["id"]] + 1
        assert get_preparation_run(
            settings.database_path, applied["id"]
        )["status"] == "completed"
