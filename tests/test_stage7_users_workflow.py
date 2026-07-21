from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings
from server.db import init_database


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
    )


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post("/api/admin/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def create_user(client: TestClient, headers: dict[str, str], username: str, role: str) -> dict:
    response = client.post(
        "/api/admin/users", headers=headers,
        json={"username": username, "password": "Strong-Password-2026!", "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()


def news_data() -> dict:
    return {
        "publication_date": "2026-07-19",
        "category": "Новости прихода",
        "summary": "Материал редакционного workflow.",
        "cover": "assets/school-maslenitsa.jpg",
        "cover_alt": "Приходской праздник",
        "body": [],
        "related_content": [],
    }


def create_review_item(client: TestClient, headers: dict[str, str], title: str) -> dict:
    created = client.post(
        "/api/admin/contents", headers=headers,
        json={"content_type": "news", "title": title, "data": news_data()},
    ).json()
    response = client.post(
        f"/api/admin/contents/{created['id']}/submit-review",
        headers=headers, json={"version": created["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_user_workflow_migration_is_idempotent(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    init_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        assert {"version", "updated_at", "last_login_at", "password_changed_at"} <= columns
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='user_events'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 10
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_admin_user_management_password_and_session_revocation(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    app = create_app(settings)
    with TestClient(app) as admin_client, TestClient(app) as editor_client:
        admin_headers = login(admin_client, "admin", "test-password")
        editor = create_user(admin_client, admin_headers, "editor.one", "editor")
        duplicate = admin_client.post(
            "/api/admin/users", headers=admin_headers,
            json={"username": "EDITOR.ONE", "password": "Another-Strong-2026!", "role": "viewer"},
        )
        assert duplicate.status_code == 409

        editor_headers = login(editor_client, "EDITOR.ONE", "Strong-Password-2026!")
        assert editor_client.get("/api/admin/users").status_code == 403
        assert editor_client.post(
            "/api/admin/change-password", headers=editor_headers,
            json={"current_password": "wrong", "new_password": "New-Strong-Password-2027!"},
        ).status_code == 401
        changed = editor_client.post(
            "/api/admin/change-password", headers=editor_headers,
            json={"current_password": "Strong-Password-2026!", "new_password": "New-Strong-Password-2027!"},
        )
        assert changed.status_code == 200
        assert editor_client.get("/api/admin/session").json() == {"authenticated": False}
        assert editor_client.post(
            "/api/admin/login", json={"username": "editor.one", "password": "Strong-Password-2026!"}
        ).status_code == 401

        editor_headers = login(editor_client, "editor.one", "New-Strong-Password-2027!")
        users = admin_client.get("/api/admin/users").json()["items"]
        editor = next(item for item in users if item["username"] == "editor.one")
        updated = admin_client.patch(
            f"/api/admin/users/{editor['id']}", headers=admin_headers,
            json={"version": editor["version"], "role": "publisher", "is_active": True},
        )
        assert updated.status_code == 200
        editor = updated.json()
        assert editor["role"] == "publisher"
        terminated = admin_client.post(
            f"/api/admin/users/{editor['id']}/terminate-sessions", headers=admin_headers,
            json={"version": editor["version"]},
        )
        assert terminated.status_code == 200
        assert terminated.json()["closed_sessions"] == 1
        assert editor_client.get("/api/admin/session").json() == {"authenticated": False}

        self_update = admin_client.patch(
            f"/api/admin/users/{admin_client.get('/api/admin/session').json()['user']['id']}",
            headers=admin_headers, json={"version": 1, "role": "editor", "is_active": True},
        )
        assert self_update.status_code == 409
        events = admin_client.get("/api/admin/user-events").json()["items"]
        actions = {item["action"] for item in events}
        assert {"login", "password_change", "user_create", "user_update", "sessions_terminated"} <= actions
        assert all(
            not any(secret in key.lower() for secret in ("password", "token", "hash", "csrf"))
            for event in events for key in event["details"]
        )


def test_roles_and_atomic_bulk_workflow(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    app = create_app(settings)
    with TestClient(app) as admin_client, TestClient(app) as editor_client, TestClient(app) as publisher_client:
        admin_headers = login(admin_client, "admin", "test-password")
        create_user(admin_client, admin_headers, "editor.bulk", "editor")
        create_user(admin_client, admin_headers, "publisher.bulk", "publisher")
        editor_headers = login(editor_client, "editor.bulk", "Strong-Password-2026!")
        publisher_headers = login(publisher_client, "publisher.bulk", "Strong-Password-2026!")

        first = editor_client.post(
            "/api/admin/contents", headers=editor_headers,
            json={"content_type": "news", "title": "Первый материал", "data": news_data()},
        ).json()
        second = editor_client.post(
            "/api/admin/contents", headers=editor_headers,
            json={"content_type": "news", "title": "Второй материал", "data": news_data()},
        ).json()
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE contents SET migration_review_required=1 WHERE id IN (?,?)",
                (first["id"], second["id"]),
            )
        reviewed = editor_client.post(
            "/api/admin/content-bulk", headers=editor_headers,
            json={"action": "review", "items": [{"id": first["id"], "version": 1}, {"id": second["id"], "version": 1}]},
        )
        assert reviewed.status_code == 409
        with sqlite3.connect(settings.database_path) as connection:
            # Stage 7's publication assertions continue from records that are
            # already accepted; Stage 10 tests cover the acceptance workflow.
            connection.execute(
                "UPDATE contents SET migration_review_required=0 WHERE id IN (?,?)",
                (first["id"], second["id"]),
            )

        first = editor_client.post(
            f"/api/admin/contents/{first['id']}/submit-review", headers=editor_headers,
            json={"version": first["version"]},
        ).json()
        second = editor_client.post(
            f"/api/admin/contents/{second['id']}/submit-review", headers=editor_headers,
            json={"version": second["version"]},
        ).json()
        forbidden = editor_client.post(
            "/api/admin/content-bulk", headers=editor_headers,
            json={"action": "publish", "items": [{"id": first["id"], "version": first["version"]}]},
        )
        assert forbidden.status_code == 403
        assert publisher_client.get("/api/admin/users").status_code == 403

        conflict = publisher_client.post(
            "/api/admin/content-bulk", headers=publisher_headers,
            json={"action": "publish", "items": [{"id": first["id"], "version": first["version"]}, {"id": second["id"], "version": 99}]},
        )
        assert conflict.status_code == 409
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM contents WHERE id IN (?,?) AND status='in_review'",
                (first["id"], second["id"]),
            ).fetchone()[0] == 2

        published = publisher_client.post(
            "/api/admin/content-bulk", headers=publisher_headers,
            json={"action": "publish", "items": [{"id": first["id"], "version": first["version"]}, {"id": second["id"], "version": second["version"]}]},
        )
        assert published.status_code == 200, published.text
        assert all(item["status"] == "published" for item in published.json()["items"])
        audit = publisher_client.get(
            f"/api/admin/contents/{first['id']}/audit-events"
        ).json()["items"]
        bulk_publish = next(item for item in audit if item["action"] == "publish")
        assert bulk_publish["details"]["bulk"] is True
        assert "batch_id" in bulk_publish["details"]

        archived = publisher_client.post(
            "/api/admin/content-bulk", headers=publisher_headers,
            json={"action": "archive", "items": [{"id": item["id"], "version": item["version"]} for item in published.json()["items"]]},
        )
        assert archived.status_code == 200
        assert all(item["status"] == "archived" for item in archived.json()["items"])
