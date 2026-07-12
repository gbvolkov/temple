from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings


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


def login(client: TestClient) -> str:
    response = client.post("/api/admin/login", json={"username": "admin", "password": "test-password"})
    assert response.status_code == 200
    return response.json()["csrf_token"]


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
            f"/api/admin/contents/{item['id']}/publish", headers=headers, json={"version": item["version"]}
        )
        assert incomplete.status_code == 422
        assert "cover_alt" in incomplete.json()["detail"]["fields"]

        payload = {
            "title": item["title"],
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
        updated = client.put(f"/api/admin/contents/{item['id']}", headers=headers, json=payload)
        assert updated.status_code == 200
        item = updated.json()

        conflict = client.put(f"/api/admin/contents/{item['id']}", headers=headers, json=payload)
        assert conflict.status_code == 409

        published = client.post(
            f"/api/admin/contents/{item['id']}/publish", headers=headers, json={"version": item["version"]}
        )
        assert published.status_code == 200
        assert published.json()["status"] == "published"

        public = client.get("/api/public/content", params={"content_type": "news"}).json()
        assert [entry["slug"] for entry in public] == [item["slug"]]
        assert client.get(f"/api/public/content/{item['slug']}").status_code == 200


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
            f"/api/admin/contents/{item['id']}/publish",
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
        assert reviewed.json()["version"] == item["version"] + 1
        remaining = client.get("/api/admin/content-index", params={"review_required": "true", "limit": 200}).json()
        assert remaining["total"] == 166
