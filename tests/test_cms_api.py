from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlite3

from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings
from server.security import hash_password
from server.workflow import publish_due_content


ROOT = Path(__file__).resolve().parents[1]


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        root=ROOT,
        site_dir=ROOT / "site",
        database_path=tmp_path / "cms.sqlite3",
        media_dir=tmp_path / "media",
        schema_path=ROOT / "site" / "cms-schema.json",
        legacy_sections_path=ROOT / "current-sections.json",
        legacy_crawl_path=None,
        media_manifest_path=tmp_path / "legacy-media-manifest.json",
        environment="test",
        bootstrap_user="admin",
        bootstrap_password="test-password",
        session_hours=1,
    )


def login(client: TestClient, username: str = "admin", password: str = "test-password") -> str:
    response = client.post("/api/admin/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def ready_news(item: dict, *, title: str | None = None) -> dict:
    return {
        "title": title or item["title"],
        "slug": item["slug"],
        "version": item["version"],
        "data": {
            "publication_date": "2026-07-12T10:00:00+03:00",
            "category": "Новости прихода",
            "summary": "Краткое описание новости.",
            "cover": "assets/school-maslenitsa.jpg",
            "cover_alt": "Праздник Воскресной школы",
        },
    }


def test_auth_crud_publish_and_public_read(tmp_path):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/admin/contents").status_code == 401
        assert client.post("/api/admin/login", json={"username": "admin", "password": "wrong"}).status_code == 401

        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        created = client.post(
            "/api/admin/contents",
            headers=headers,
            json={"content_type": "news", "title": "Новая приходская новость", "data": {}},
        )
        assert created.status_code == 201
        item = created.json()
        assert item["status"] == "draft"
        index = client.get("/api/admin/content-index", params={"content_type": "news", "q": "приходская"}).json()
        assert index["total"] == 1
        assert index["items"][0]["id"] == item["id"]

        incomplete = client.post(
            f"/api/admin/contents/{item['id']}/submit-review", headers=headers, json={"version": item["version"]}
        )
        assert incomplete.status_code == 422
        assert "cover_alt" in incomplete.json()["detail"]["fields"]

        payload = ready_news(item)
        updated = client.put(f"/api/admin/contents/{item['id']}", headers=headers, json=payload)
        assert updated.status_code == 200
        item = updated.json()

        unchanged = client.put(
            f"/api/admin/contents/{item['id']}", headers=headers, json=ready_news(item)
        )
        assert unchanged.status_code == 200
        assert unchanged.json()["version"] == item["version"]
        assert client.get(f"/api/admin/contents/{item['id']}/revisions").json()["total"] == 2

        conflict = client.put(f"/api/admin/contents/{item['id']}", headers=headers, json=payload)
        assert conflict.status_code == 409

        submitted = client.post(
            f"/api/admin/contents/{item['id']}/submit-review", headers=headers, json={"version": item["version"]}
        )
        assert submitted.status_code == 200
        assert submitted.json()["status"] == "in_review"
        assert submitted.json()["version"] == item["version"]

        published = client.post(
            f"/api/admin/contents/{item['id']}/publish", headers=headers, json={"version": item["version"]}
        )
        assert published.status_code == 200
        published_item = published.json()
        assert published_item["status"] == "published"
        assert published_item["version"] == item["version"]
        assert published_item["published_version"] == item["version"]

        public = client.get("/api/public/content", params={"content_type": "news"}).json()
        assert [entry["slug"] for entry in public] == [item["slug"]]
        assert client.get(f"/api/public/content/{item['slug']}").status_code == 200

        edited_payload = ready_news(published_item, title="Новая редакция новости")
        edited = client.put(f"/api/admin/contents/{item['id']}", headers=headers, json=edited_payload)
        assert edited.status_code == 200
        draft = edited.json()
        assert draft["status"] == "draft"
        assert draft["version"] == published_item["version"] + 1
        assert draft["published_version"] == published_item["published_version"]
        assert draft["is_public"] is True
        assert draft["has_unpublished_changes"] is True
        live = client.get(f"/api/public/content/{item['slug']}").json()
        assert live["title"] == item["title"]
        assert live["version"] == published_item["published_version"]

        changed_slug = ready_news(draft)
        changed_slug["slug"] = "new-public-url"
        assert client.put(f"/api/admin/contents/{item['id']}", headers=headers, json=changed_slug).status_code == 409

        restored = client.post(
            f"/api/admin/contents/{item['id']}/revisions/2/restore",
            headers=headers,
            json={"version": draft["version"]},
        )
        assert restored.status_code == 200
        restored_item = restored.json()
        assert restored_item["version"] == draft["version"] + 1
        assert restored_item["status"] == "draft"
        assert client.get(f"/api/public/content/{item['slug']}").json()["title"] == item["title"]

        revisions = client.get(f"/api/admin/contents/{item['id']}/revisions").json()
        assert revisions["total"] == 4
        assert any(entry["is_published"] for entry in revisions["items"])
        events = client.get(f"/api/admin/contents/{item['id']}/audit-events").json()
        assert {event["action"] for event in events["items"]} >= {
            "create", "update", "submit_review", "publish", "restore_revision",
        }
        assert all("data" not in event["details"] for event in events["items"])


def test_csrf_media_and_migration(tmp_path):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        csrf = login(client)
        assert client.post(
            "/api/admin/contents", json={"content_type": "page", "title": "Без CSRF", "data": {}}
        ).status_code == 403

        media = client.post(
            "/api/admin/media",
            headers={"X-CSRF-Token": csrf},
            files={"file": ("cover.jpg", b"small-jpeg-payload", "image/jpeg")},
            data={"alt_text": "Обложка"},
        )
        assert media.status_code == 201
        assert client.get(media.json()["url"]).status_code == 200

        dry = client.post("/api/admin/migration/import?dry_run=true", headers={"X-CSRF-Token": csrf})
        assert dry.status_code == 200
        assert dry.json()["records_found"] == 167
        assert dry.json()["dry_run"] is True

        imported = client.post("/api/admin/migration/import?dry_run=false", headers={"X-CSRF-Token": csrf})
        assert imported.status_code == 200
        assert imported.json()["imported"] == 167
        redirect = client.get("/o-hrame/raspisanie-bogosluzheniy.html", follow_redirects=False)
        assert redirect.status_code == 301
        assert redirect.headers["location"] == "/#/schedule"
        assert client.get("/", follow_redirects=False).status_code == 200
        status = client.get("/api/admin/migration").json()
        assert status["totals"]["review_required"] == 167
        assert status["totals"]["reviewed"] == 0
        assert status["by_type"]["leaflet_issue"] == 148
        assert status["review_by_type"]["leaflet_issue"] == {
            "total": 148, "review_required": 148, "reviewed": 0, "published": 0,
        }

        queue = client.get("/api/admin/content-index", params={"review_required": "true", "limit": 200}).json()
        assert queue["total"] == 167
        item = queue["items"][0]
        imported_events = client.get(f"/api/admin/contents/{item['id']}/audit-events").json()["items"]
        assert imported_events[0]["action"] == "import_create"
        assert imported_events[0]["actor_username"] == "admin"
        assert imported_events[0]["details"] == {"source": "legacy_import"}

        saved = client.put(
            f"/api/admin/contents/{item['id']}",
            headers={"X-CSRF-Token": csrf},
            json={
                "title": item["title"],
                "slug": item["slug"],
                "data": item["data"],
                "version": item["version"],
            },
        )
        assert saved.status_code == 200
        item = saved.json()
        assert item["migration_review_required"] is True

        refused = client.post(
            f"/api/admin/contents/{item['id']}/submit-review",
            headers={"X-CSRF-Token": csrf},
            json={"version": item["version"]},
        )
        assert refused.status_code == 409
        assert "отметьте его проверенным" in refused.json()["detail"]

        reviewed = client.post(
            f"/api/admin/contents/{item['id']}/review",
            headers={"X-CSRF-Token": csrf},
            json={"version": item["version"]},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["migration_review_required"] is False
        assert reviewed.json()["version"] == item["version"]
        reviewed_events = client.get(f"/api/admin/contents/{item['id']}/audit-events").json()["items"]
        assert [event["action"] for event in reviewed_events[:2]] == ["migration_review", "import_create"]
        remaining = client.get("/api/admin/content-index", params={"review_required": "true", "limit": 200}).json()
        assert remaining["total"] == 166


def test_scheduled_publication_archive_trash_restore_and_audit(tmp_path):
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        item = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "news", "title": "Отложенная новость", "data": {}},
        ).json()
        item = client.put(
            f"/api/admin/contents/{item['id']}", headers=headers, json=ready_news(item)
        ).json()
        item = client.post(
            f"/api/admin/contents/{item['id']}/submit-review", headers=headers,
            json={"version": item["version"]},
        ).json()
        first_publish = client.post(
            f"/api/admin/contents/{item['id']}/publish", headers=headers,
            json={"version": item["version"]},
        ).json()

        draft = client.put(
            f"/api/admin/contents/{item['id']}", headers=headers,
            json=ready_news(first_publish, title="Будущая редакция"),
        ).json()
        review = client.post(
            f"/api/admin/contents/{item['id']}/submit-review", headers=headers,
            json={"version": draft["version"]},
        ).json()
        scheduled_for = datetime.now(UTC) + timedelta(hours=1)
        scheduled = client.post(
            f"/api/admin/contents/{item['id']}/schedule", headers=headers,
            json={"version": review["version"], "scheduled_at": scheduled_for.isoformat()},
        )
        assert scheduled.status_code == 200
        scheduled_item = scheduled.json()
        assert scheduled_item["status"] == "scheduled"
        assert client.get(f"/api/public/content/{item['slug']}").json()["title"] == "Отложенная новость"

        published_ids = publish_due_content(settings.database_path, now=scheduled_for + timedelta(seconds=1))
        assert published_ids == [item["id"]]
        assert publish_due_content(settings.database_path, now=scheduled_for + timedelta(minutes=2)) == []
        live = client.get(f"/api/public/content/{item['slug']}").json()
        assert live["title"] == "Будущая редакция"
        current = client.get(f"/api/admin/contents/{item['id']}").json()
        assert current["status"] == "published"
        assert current["version"] == current["published_version"]

        archived = client.post(
            f"/api/admin/contents/{item['id']}/archive", headers=headers,
            json={"version": current["version"]},
        ).json()
        assert archived["status"] == "archived"
        assert archived["published_version"] is None
        assert client.get(f"/api/public/content/{item['slug']}").status_code == 404
        restored = client.post(
            f"/api/admin/contents/{item['id']}/restore", headers=headers,
            json={"version": archived["version"]},
        ).json()
        assert restored["status"] == "draft"
        assert restored["is_public"] is False

        trashed = client.post(
            f"/api/admin/contents/{item['id']}/trash", headers=headers,
            json={"version": restored["version"]},
        ).json()
        assert trashed["status"] == "trash"
        assert trashed["deleted_at"]
        restored_again = client.post(
            f"/api/admin/contents/{item['id']}/restore", headers=headers,
            json={"version": trashed["version"]},
        ).json()
        assert restored_again["status"] == "draft"
        assert restored_again["deleted_at"] is None

        events = client.get(f"/api/admin/contents/{item['id']}/audit-events").json()["items"]
        scheduled_event = next(event for event in events if event["action"] == "scheduled_publish")
        assert scheduled_event["actor_id"] is None
        assert {event["action"] for event in events} >= {
            "schedule", "scheduled_publish", "archive", "trash", "restore",
        }


def test_workflow_roles_and_invalid_transitions(tmp_path):
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        with sqlite3.connect(settings.database_path) as connection:
            for user_id, username, role in (
                ("viewer-id", "viewer", "viewer"),
                ("editor-id", "editor", "editor"),
                ("publisher-id", "publisher", "publisher"),
            ):
                connection.execute(
                    "INSERT INTO users(id,username,password_hash,role,is_active,created_at) VALUES(?,?,?,?,1,?)",
                    (user_id, username, hash_password("role-password"), role, datetime.now(UTC).isoformat()),
                )

        editor_csrf = login(client, "editor", "role-password")
        editor_headers = {"X-CSRF-Token": editor_csrf}
        item = client.post(
            "/api/admin/contents", headers=editor_headers,
            json={"content_type": "news", "title": "Ролевая новость", "data": {}},
        ).json()
        item = client.put(
            f"/api/admin/contents/{item['id']}", headers=editor_headers, json=ready_news(item)
        ).json()
        item = client.post(
            f"/api/admin/contents/{item['id']}/submit-review", headers=editor_headers,
            json={"version": item["version"]},
        ).json()
        assert client.post(
            f"/api/admin/contents/{item['id']}/publish", headers=editor_headers,
            json={"version": item["version"]},
        ).status_code == 403

        client.cookies.clear()
        publisher_csrf = login(client, "publisher", "role-password")
        published = client.post(
            f"/api/admin/contents/{item['id']}/publish",
            headers={"X-CSRF-Token": publisher_csrf}, json={"version": item["version"]},
        )
        assert published.status_code == 200
        assert client.post(
            f"/api/admin/contents/{item['id']}/publish",
            headers={"X-CSRF-Token": publisher_csrf}, json={"version": item["version"]},
        ).status_code == 409

        client.cookies.clear()
        viewer_csrf = login(client, "viewer", "role-password")
        assert client.get(f"/api/admin/contents/{item['id']}").status_code == 200
        assert client.post(
            f"/api/admin/contents/{item['id']}/archive",
            headers={"X-CSRF-Token": viewer_csrf}, json={"version": item["version"]},
        ).status_code == 403
