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


def publish_item(client: TestClient, headers: dict[str, str], content_type: str, title: str, data: dict) -> dict:
    item = client.post(
        "/api/admin/contents",
        headers=headers,
        json={"content_type": content_type, "title": title, "data": data},
    ).json()
    item = client.post(
        f"/api/admin/contents/{item['id']}/submit-review",
        headers=headers,
        json={"version": item["version"]},
    ).json()
    response = client.post(
        f"/api/admin/contents/{item['id']}/publish",
        headers=headers,
        json={"version": item["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


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
        assert redirect.headers["location"] == "/schedule"
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


def test_server_rendered_public_routes_use_published_snapshot(tmp_path):
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        for route in ("/", "/schedule", "/about", "/parish", "/school", "/news", "/gallery", "/leaflet", "/media"):
            response = client.get(route)
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/html")

        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        item = client.post(
            "/api/admin/contents",
            headers=headers,
            json={"content_type": "news", "title": "Опубликованная SSR-новость", "data": {}},
        ).json()
        payload = ready_news(item)
        payload["data"]["category"] = "Воскресная школа"
        payload["data"]["summary"] = "Книга & приход"
        payload["data"]["body"] = [
            {
                "id": "00000000-0000-4000-8000-000000000001",
                "type": "paragraph",
                "data": {"runs": [{"text": "Первый абзац", "marks": ["bold"]}]},
            },
            {
                "id": "00000000-0000-4000-8000-000000000002",
                "type": "paragraph",
                "data": {"runs": [{"text": "Второй абзац", "marks": []}]},
            },
        ]
        item = client.put(f"/api/admin/contents/{item['id']}", headers=headers, json=payload).json()
        item = client.post(
            f"/api/admin/contents/{item['id']}/submit-review",
            headers=headers,
            json={"version": item["version"]},
        ).json()
        item = client.post(
            f"/api/admin/contents/{item['id']}/publish",
            headers=headers,
            json={"version": item["version"]},
        ).json()

        clean_url = f"/news/{item['published_slug']}"
        listing = client.get("/news")
        detail = client.get(clean_url)
        assert listing.status_code == detail.status_code == 200
        assert "Опубликованная SSR-новость" in listing.text
        assert "Опубликованная SSR-новость" in detail.text
        assert "Опубликованная SSR-новость" in client.get("/school").text
        assert 'href="/styles.css"' in detail.text
        assert 'src="/app.js"' in detail.text
        assert "Книга &amp; приход" in detail.text
        assert "<strong>Первый абзац</strong>" in detail.text
        assert client.get(f"/gallery/{item['published_slug']}").status_code == 404
        assert client.get("/news/not-a-real-slug").status_code == 404

        edited_payload = ready_news(item, title="Неопубликованная новая редакция")
        edited = client.put(f"/api/admin/contents/{item['id']}", headers=headers, json=edited_payload)
        assert edited.status_code == 200
        live = client.get(clean_url)
        assert "Опубликованная SSR-новость" in live.text
        assert "Неопубликованная новая редакция" not in live.text

        trailing = client.get("/news/?source=test", follow_redirects=False)
        assert trailing.status_code == 308
        assert trailing.headers["location"] == "/news?source=test"
        missing = client.get("/unknown-public-page")
        assert missing.status_code == 404
        assert missing.headers["content-type"].startswith("text/html")
        api_missing = client.get("/api/unknown-public-page")
        assert api_missing.status_code == 404
        assert api_missing.headers["content-type"].startswith("application/json")

        cms = client.get("/cms.html")
        assert cms.status_code == 200
        assert f'href="{settings.public_base_url}/"' in cms.text


def test_public_base_url_is_normalized_and_validated():
    assert Settings.normalize_public_base_url("https://example.test:8443/") == "https://example.test:8443"
    for invalid in ("example.test", "ftp://example.test", "https://example.test/path", "https://example.test?x=1"):
        try:
            Settings.normalize_public_base_url(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"PUBLIC_BASE_URL accepted invalid value: {invalid}")


def test_stage4_public_sections_use_only_published_cms_data(tmp_path):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        empty_about = client.get("/about").text
        assert "Контакты готовятся к публикации" in empty_about
        assert "+7 (499) 480-09-89" not in empty_about

        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        contact = publish_item(client, headers, "site_contact", "Контакты храма", {
            "address": "Проверенный адрес храма",
            "metro": "Метро Проверенное",
            "phone": "+7 000 111-22-33",
            "email": "contact@example.test",
            "opening_hours": "Ежедневно",
            "map_coordinates": "55.0, 37.0",
            "legal_details": "Проверенные реквизиты",
            "social_links": [{"network": "telegram", "url": "https://t.me/example", "enabled": True}],
        })
        assert client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "site_contact", "title": "Дубль", "data": {}},
        ).status_code == 409

        history = publish_item(client, headers, "page", "Проверенная история", {
            "placement": "about_history",
            "summary": "История из CMS",
            "body": [{"type": "paragraph", "data": {"text": "Исторический текст из опубликованной ревизии."}}],
            "navigation_order": 10,
        })
        school = publish_item(client, headers, "page", "Основная страница школы", {
            "placement": "school_home",
            "summary": "Школа из CMS",
            "body": [{"type": "paragraph", "data": {"text": "Описание школы из CMS."}}],
            "schedule": [{"weekday": "7", "time": "12:30", "title": "Занятия", "note": "После литургии"}],
        })
        schedule_info = publish_item(client, headers, "page", "Пояснение к расписанию", {
            "placement": "schedule_info",
            "body": [{"type": "paragraph", "data": {"text": "Расписание может меняться в праздники."}}],
            "pdf": "/media/schedule.pdf",
        })
        section = publish_item(client, headers, "parish_section", "Социальная служба", {
            "summary": "Помощь прихожанам",
            "body": [{"type": "paragraph", "data": {"text": "Полное описание направления."}}],
            "contact_name": "Мария",
            "phone": "+7 000 222-33-44",
            "email": "section@example.test",
            "schedule": [{"weekday": "2", "time": "18:00", "title": "Встреча", "note": "Еженедельно"}],
            "order": 5,
        })
        related_news = publish_item(client, headers, "news", "Новость социальной службы", {
            "publication_date": "2026-07-18T10:00:00+03:00",
            "category": "Социальная служба",
            "summary": "Связанная новость",
            "body": [{"type": "paragraph", "data": {"text": "Текст новости."}}],
            "cover": "assets/school-maslenitsa.jpg",
            "cover_alt": "Событие",
            "related_section": section["id"],
        })
        related_gallery = publish_item(client, headers, "gallery", "Фото социальной службы", {
            "event_date": "2026-07-17",
            "category": "Жизнь прихода",
            "summary": "Связанный альбом",
            "cover": "assets/school-maslenitsa.jpg",
            "photos": [{"image": "assets/school-maslenitsa.jpg", "alt": "Фото", "order": 1}],
            "related_section": section["id"],
        })
        future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        past = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        future_service = publish_item(client, headers, "service", "Будущая литургия", {
            "starts_at": future, "service_type": "liturgy", "location": "Главный храм", "note": "Исповедь заранее",
        })
        publish_item(client, headers, "service", "Прошедшая служба", {
            "starts_at": past, "service_type": "vigil", "location": "Главный храм",
        })

        about = client.get("/about").text
        assert history["title"] in about
        assert "Исторический текст из опубликованной ревизии." in about
        assert "Проверенный адрес храма" in about
        assert contact["data"]["phone"] in about

        school_page = client.get("/school").text
        assert school["title"] in school_page
        assert "Воскресенье · 12:30" in school_page
        schedule_page = client.get("/schedule").text
        assert schedule_info["title"] in schedule_page
        assert future_service["title"] in schedule_page
        assert "Прошедшая служба" not in schedule_page
        assert "Литургия" in schedule_page

        parish = client.get(f"/parish/{section['published_slug']}").text
        assert "Полное описание направления." in parish
        assert related_news["title"] in parish
        assert related_gallery["title"] in parish
        assert "Вторник · 18:00" in parish


def test_stage4_singleton_placement_validation_and_published_snapshot_reservation(tmp_path):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        headers = {"X-CSRF-Token": login(client)}
        first = publish_item(client, headers, "page", "Первая история", {
            "placement": "about_history",
            "body": [{"type": "paragraph", "data": {"text": "Первая версия истории"}}],
        })
        changed = client.put(
            f"/api/admin/contents/{first['id']}", headers=headers,
            json={
                "title": first["title"], "slug": first["published_slug"], "version": first["version"],
                "data": {"placement": "standalone", "body": [{"type": "paragraph", "data": {"text": "Новый черновик"}}]},
            },
        )
        assert changed.status_code == 200
        assert "Первая версия истории" in client.get("/about").text
        assert "Новый черновик" not in client.get("/about").text

        second = client.post(
            "/api/admin/contents", headers=headers,
            json={
                "content_type": "page", "title": "Вторая история",
                "data": {"placement": "about_history", "body": [{"type": "paragraph", "data": {"text": "Конфликт"}}]},
            },
        ).json()
        second = client.post(
            f"/api/admin/contents/{second['id']}/submit-review", headers=headers,
            json={"version": second["version"]},
        ).json()
        conflict = client.post(
            f"/api/admin/contents/{second['id']}/publish", headers=headers,
            json={"version": second["version"]},
        )
        assert conflict.status_code == 409

        invalid_placement = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "page", "title": "Ошибка", "data": {"placement": "unknown", "body": ["Текст"]}},
        )
        assert invalid_placement.status_code == 422
        invalid_schedule = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "parish_section", "title": "Ошибка расписания", "data": {"schedule": [{"weekday": 8, "time": "27:90"}]}},
        )
        assert invalid_schedule.status_code == 422
        invalid_relation = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "news", "title": "Ошибка связи", "data": {"related_section": "missing"}},
        )
        assert invalid_relation.status_code == 422
        invalid_service = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "service", "title": "Ошибка даты", "data": {"starts_at": "not-a-date"}},
        )
        assert invalid_service.status_code == 422

        old_contact = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "site_contact", "title": "Старые контакты", "data": {}},
        ).json()
        old_contact = client.post(
            f"/api/admin/contents/{old_contact['id']}/trash", headers=headers,
            json={"version": old_contact["version"]},
        ).json()
        client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "site_contact", "title": "Новые контакты", "data": {}},
        ).raise_for_status()
        restore_conflict = client.post(
            f"/api/admin/contents/{old_contact['id']}/restore", headers=headers,
            json={"version": old_contact["version"]},
        )
        assert restore_conflict.status_code == 409


def test_stage4_gallery_and_leaflet_server_pagination(tmp_path):
    with TestClient(create_app(settings_for(tmp_path))) as client:
        headers = {"X-CSRF-Token": login(client)}
        for index in range(25):
            publish_item(client, headers, "gallery", f"Альбом {index + 1:02d}", {
                "event_date": f"2025-01-{index % 28 + 1:02d}",
                "category": "Жизнь прихода",
                "cover": "assets/school-maslenitsa.jpg",
                "photos": [{"image": "assets/school-maslenitsa.jpg", "alt": "Фото", "order": 1}],
            })
        for index in range(21):
            publish_item(client, headers, "leaflet_issue", f"Листок {index + 1:02d}", {
                "number": index + 1,
                "period": "Проверенный выпуск",
                "publication_date": f"2024-02-{index % 28 + 1:02d}",
                "cover": "assets/school-maslenitsa.jpg",
                "pdf": "/media/leaflet.pdf",
            })

        gallery_first = client.get("/gallery")
        gallery_second = client.get("/gallery?page=2")
        assert gallery_first.status_code == gallery_second.status_code == 200
        assert gallery_first.text.count('class="album-card"') == 24
        assert gallery_second.text.count('class="album-card"') == 1
        assert client.get("/gallery?year=2025").status_code == 200
        assert client.get("/gallery?year=1999").status_code == 404
        assert client.get("/gallery?page=3").status_code == 404
        assert client.get("/gallery?page=zero").status_code == 404

        leaflet_first = client.get("/leaflet")
        leaflet_second = client.get("/leaflet?page=2")
        assert leaflet_first.status_code == leaflet_second.status_code == 200
        assert leaflet_first.text.count('class="issue-row"') == 20
        assert leaflet_second.text.count('class="issue-row"') == 1
        assert client.get("/leaflet?year=2024").status_code == 200
        assert client.get("/leaflet?year=2025").status_code == 404


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
