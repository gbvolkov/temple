from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings
from server.content_blocks import legacy_to_blocks, video_embed_url
from server.security import hash_password


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


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/admin/login", json={"username": "admin", "password": "test-password"})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def paragraph(block_id: str, text: str) -> dict:
    return {"id": block_id, "type": "paragraph", "data": {"runs": [{"text": text, "marks": []}]}}


def create_page(client: TestClient, headers: dict[str, str], title: str, body: list[dict], **data) -> dict:
    response = client.post(
        "/api/admin/contents",
        headers=headers,
        json={"content_type": "page", "title": title, "data": {"body": body, **data}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def publish(client: TestClient, headers: dict[str, str], item: dict) -> dict:
    reviewed = client.post(
        f"/api/admin/contents/{item['id']}/submit-review",
        headers=headers,
        json={"version": item["version"]},
    )
    assert reviewed.status_code == 200, reviewed.text
    response = client.post(
        f"/api/admin/contents/{item['id']}/publish",
        headers=headers,
        json={"version": item["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def database_counts(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(path) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("contents", "revisions", "audit_events")
        )


def test_content_schema_is_the_single_editor_source() -> None:
    schema = json.loads((ROOT / "site" / "cms-schema.json").read_text(encoding="utf-8"))
    cms_js = (ROOT / "site" / "cms.js").read_text(encoding="utf-8")

    assert schema["schema_version"] == "1.4.0"
    assert set(schema["ui"]["block_types"]) == {
        "paragraph", "heading", "list", "image", "gallery", "quote", "video", "file", "callout",
    }
    assert "typeDefinitions" not in cms_js
    assert 'fetch("/cms-schema.json")' in cms_js
    assert "admin / temple-demo" not in cms_js


def test_all_canonical_blocks_preview_and_public_rendering(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        target = create_page(client, headers, "Связанный материал", [paragraph("target-p", "Текст цели")])
        hidden = create_page(client, headers, "Скрытый материал", [paragraph("hidden-p", "Не показывать")])
        body = [
            {
                "id": "paragraph-1", "type": "paragraph",
                "data": {"runs": [
                    {"text": "Жирная ссылка", "marks": ["bold"], "href": "/about"},
                    {"text": " и курсив", "marks": ["italic"]},
                ]},
            },
            {"id": "heading-1", "type": "heading", "data": {"level": 2, "runs": [{"text": "Подзаголовок", "marks": []}]}},
            {"id": "list-1", "type": "list", "data": {"style": "numbered", "items": [{"runs": [{"text": "Первый", "marks": []}]}, {"runs": [{"text": "Второй", "marks": []}]}]}},
            {"id": "image-1", "type": "image", "data": {"image": "assets/school-maslenitsa.jpg", "alt": "Дети на празднике", "caption": "Подпись фотографии"}},
            {"id": "gallery-1", "type": "gallery", "data": {"items": [{"id": "photo-1", "image": "/assets/school-maslenitsa.jpg", "alt": "Фото галереи", "caption": "Галерея", "order": 7}]}},
            {"id": "quote-1", "type": "quote", "data": {"runs": [{"text": "Текст цитаты", "marks": []}], "author": "Автор", "source": "Источник"}},
            {"id": "video-1", "type": "video", "data": {"url": "https://www.youtube.com/watch?v=abc123", "caption": "Видео"}},
            {"id": "file-1", "type": "file", "data": {"url": "/assets/document.pdf", "label": "Документ", "description": "Описание файла"}},
            {"id": "callout-1", "type": "callout", "data": {"tone": "important", "title": "Важно", "runs": [{"text": "Текст плашки", "marks": []}]}},
        ]
        source = create_page(
            client, headers, "Страница со всеми блоками", body,
            related_content=[target["id"], hidden["id"]],
        )
        assert [block["type"] for block in source["data"]["body"]] == [block["type"] for block in body]
        assert source["data"]["body"][3]["data"]["image"] == "assets/school-maslenitsa.jpg"
        assert source["data"]["body"][4]["data"]["items"][0]["order"] == 1

        revision_count = client.get(f"/api/admin/contents/{source['id']}/revisions").json()["total"]
        unchanged = client.put(
            f"/api/admin/contents/{source['id']}", headers=headers,
            json={"title": source["title"], "slug": source["slug"], "version": source["version"], "data": source["data"]},
        )
        assert unchanged.status_code == 200
        assert unchanged.json()["version"] == source["version"]
        assert client.get(f"/api/admin/contents/{source['id']}/revisions").json()["total"] == revision_count

        counts_before = database_counts(settings.database_path)
        preview = client.post(
            "/api/admin/content-preview",
            headers=headers,
            json={
                "content_id": source["id"], "content_type": "page", "title": source["title"],
                "slug": source["slug"], "data": source["data"],
            },
        )
        assert preview.status_code == 200, preview.text
        assert database_counts(settings.database_path) == counts_before
        assert "<strong>Жирная ссылка</strong>" in preview.text
        assert "<h2" in preview.text
        assert "youtube-nocookie.com/embed/abc123" in preview.text
        assert "Описание файла" in preview.text

        options = client.get(
            "/api/admin/content-options",
            params={"types": "page", "q": "Связанный", "exclude_id": source["id"]},
        )
        assert options.status_code == 200
        assert [item["id"] for item in options.json()["items"]] == [target["id"]]

        target = publish(client, headers, target)
        source = publish(client, headers, source)
        source_page = client.get(f"/pages/{source['published_slug']}")
        assert source_page.status_code == 200
        assert "Связанный материал" in source_page.text
        assert "Скрытый материал" not in source_page.text
        assert "<ol" in source_page.text
        assert "<blockquote>" in source_page.text
        assert "content-block--callout" in source_page.text

        target_page = client.get(f"/pages/{target['published_slug']}")
        assert target_page.status_code == 200
        assert "Страница со всеми блоками" in target_page.text

        changed_data = json.loads(json.dumps(source["data"], ensure_ascii=False))
        changed_data["body"][0]["data"]["runs"][0]["text"] = "Новая рабочая версия"
        edited = client.put(
            f"/api/admin/contents/{source['id']}",
            headers=headers,
            json={"title": source["title"], "slug": source["slug"], "version": source["version"], "data": changed_data},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["version"] == source["version"] + 1
        stale = client.put(
            f"/api/admin/contents/{source['id']}", headers=headers,
            json={"title": source["title"], "slug": source["slug"], "version": source["version"], "data": source["data"]},
        )
        assert stale.status_code == 409
        assert "Новая рабочая версия" not in client.get(f"/pages/{source['published_slug']}").text
        working_preview = client.post(
            "/api/admin/content-preview",
            headers=headers,
            json={"content_id": source["id"], "content_type": "page", "title": source["title"], "slug": source["slug"], "data": changed_data},
        )
        assert "Новая рабочая версия" in working_preview.text


def test_block_relation_and_size_validation(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        headers = login(client)
        valid = paragraph("same", "Корректный текст")
        invalid_bodies = [
            [{"id": "unknown", "type": "html", "data": {"html": "<b>x</b>"}}],
            [{"id": "bad-data", "type": "paragraph", "data": "text"}],
            [{"id": "html", "type": "paragraph", "data": {"runs": [{"text": "<b>HTML</b>", "marks": []}]}}],
            [{"id": "href", "type": "paragraph", "data": {"runs": [{"text": "Опасно", "marks": [], "href": "javascript:alert(1)"}]}}],
            [{"id": "mark", "type": "paragraph", "data": {"runs": [{"text": "Подчёркивание", "marks": ["underline"]}]}}],
            [{"id": "run-extra", "type": "paragraph", "data": {"runs": [{"text": "Лишнее поле", "marks": [], "html": "нет"}]}}],
            [valid, valid],
            [{"id": "h4", "type": "heading", "data": {"level": 4, "runs": []}}],
            [{"id": "list", "type": "list", "data": {"style": "checklist", "items": []}}],
            [{"id": "image", "type": "image", "data": {"image": "https://example.org/x.jpg", "alt": "x"}}],
            [{"id": "image-traversal", "type": "image", "data": {"image": "/media/../secret.jpg", "alt": "x"}}],
            [{"id": "gallery", "type": "gallery", "data": {"items": [{"image": "/media/x.jpg", "alt": ""}]}}],
            [{"id": "gallery-duplicates", "type": "gallery", "data": {"items": [{"id": "photo", "image": "/media/a.jpg", "alt": "A"}, {"id": "photo", "image": "/media/b.jpg", "alt": "B"}]}}],
            [{"id": "video", "type": "video", "data": {"url": "http://example.org/video"}}],
            [{"id": "file", "type": "file", "data": {"url": "https://example.org/a.pdf", "label": "Файл"}}],
            [{"id": "callout", "type": "callout", "data": {"tone": "warning", "runs": []}}],
            [{"id": "legacy", "type": "legacy_text", "data": {"text": "Новый legacy"}}],
            [{"id": "extra", "type": "paragraph", "data": {"runs": [], "html": "<b>x</b>"}}],
        ]
        for index, body in enumerate(invalid_bodies):
            response = client.post(
                "/api/admin/contents",
                headers=headers,
                json={"content_type": "page", "title": f"Ошибка {index}", "data": {"body": body}},
            )
            assert response.status_code == 422, (index, response.text)

        too_many_blocks = [paragraph(f"p-{index}", str(index)) for index in range(201)]
        response = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "page", "title": "Слишком много блоков", "data": {"body": too_many_blocks}},
        )
        assert response.status_code == 422

        html_summary = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "page", "title": "HTML в анонсе", "data": {"summary": "<script>alert(1)</script>", "body": [paragraph("safe", "Текст")]}},
        )
        assert html_summary.status_code == 422

        photos = [{"id": f"photo-{index}", "image": "/media/x.jpg", "alt": str(index)} for index in range(101)]
        response = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "page", "title": "Слишком много фото", "data": {"body": [{"id": "g", "type": "gallery", "data": {"items": photos}}]}},
        )
        assert response.status_code == 422

        target = create_page(client, headers, "Цель", [paragraph("target", "Цель")])
        duplicate = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "page", "title": "Дубли", "data": {"body": [paragraph("dup", "Дубли")], "related_content": [target["id"], target["id"]]}},
        )
        assert duplicate.status_code == 422
        missing = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "page", "title": "Нет цели", "data": {"body": [paragraph("missing", "Нет")], "related_content": ["missing-id"]}},
        )
        assert missing.status_code == 422
        self_reference = client.put(
            f"/api/admin/contents/{target['id']}", headers=headers,
            json={"title": target["title"], "slug": target["slug"], "version": target["version"], "data": {**target["data"], "related_content": [target["id"]]}},
        )
        assert self_reference.status_code == 422

        too_many_relations = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "page", "title": "Слишком много связей", "data": {"body": [paragraph("relations", "Текст")], "related_content": [f"target-{index}" for index in range(21)]}},
        )
        assert too_many_relations.status_code == 422

        oversized = client.post(
            "/api/admin/contents", headers=headers,
            json={"content_type": "page", "title": "Больше мегабайта", "data": {"body": [paragraph("large", "Текст")], "migration_metadata": "x" * (1024 * 1024)}},
        )
        assert oversized.status_code == 422


def test_legacy_body_is_readable_and_noop_save_does_not_rewrite_it(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        item = create_page(client, headers, "Старая страница", [paragraph("initial", "Временный текст")])
        legacy = [{"type": "paragraph", "data": {"text": "Старый paragraph-объект"}}]
        with sqlite3.connect(settings.database_path) as connection:
            data = json.loads(connection.execute("SELECT data_json FROM contents WHERE id=?", (item["id"],)).fetchone()[0])
            data["body"] = legacy
            connection.execute("UPDATE contents SET data_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), item["id"]))
            snapshot = json.loads(connection.execute(
                "SELECT snapshot_json FROM revisions WHERE content_id=? AND version=1", (item["id"],),
            ).fetchone()[0])
            snapshot["data"] = data
            connection.execute(
                "UPDATE revisions SET snapshot_json=? WHERE content_id=? AND version=1",
                (json.dumps(snapshot, ensure_ascii=False), item["id"]),
            )
            connection.commit()

        current = client.get(f"/api/admin/contents/{item['id']}").json()
        revision_count = client.get(f"/api/admin/contents/{item['id']}/revisions").json()["total"]
        unchanged = client.put(
            f"/api/admin/contents/{item['id']}", headers=headers,
            json={"title": current["title"], "slug": current["slug"], "version": current["version"], "data": current["data"]},
        )
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["version"] == current["version"]
        assert unchanged.json()["data"]["body"] == legacy
        assert client.get(f"/api/admin/contents/{item['id']}/revisions").json()["total"] == revision_count

        preview = client.post(
            "/api/admin/content-preview", headers=headers,
            json={"content_id": item["id"], "content_type": "page", "title": current["title"], "slug": current["slug"], "data": current["data"]},
        )
        assert preview.status_code == 200
        assert "Старый paragraph-объект" in preview.text


def test_legacy_conversion_and_official_video_recognition() -> None:
    blocks = legacy_to_blocks("Первый абзац\n\nВторой абзац")
    assert [block["type"] for block in blocks] == ["paragraph", "paragraph"]
    assert len({block["id"] for block in blocks}) == 2
    assert blocks[0]["data"]["runs"] == [{"text": "Первый абзац", "marks": []}]

    assert video_embed_url("https://youtu.be/abc123") == "https://www.youtube-nocookie.com/embed/abc123"
    assert video_embed_url("https://rutube.ru/video/abcdef/") == "https://rutube.ru/play/embed/abcdef"
    assert video_embed_url("https://vk.com/video-123_456") == "https://vk.com/video_ext.php?oid=-123&id=456&hd=2"
    assert video_embed_url("https://example.org/video") == ""
    assert video_embed_url("http://www.youtube.com/watch?v=abc123") == ""


def test_preview_roles_csrf_and_options_pagination(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        admin_headers = login(client)
        item = create_page(client, admin_headers, "Цель предпросмотра", [paragraph("preview", "Текст")])
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "INSERT INTO users(id,username,password_hash,role,is_active,created_at) VALUES(?,?,?,?,1,?)",
                ("viewer-stage5", "viewer-stage5", hash_password("viewer-password"), "viewer", datetime.now(UTC).isoformat()),
            )
            connection.commit()

        client.cookies.clear()
        login_response = client.post(
            "/api/admin/login", json={"username": "viewer-stage5", "password": "viewer-password"},
        )
        viewer_csrf = login_response.json()["csrf_token"]
        options = client.get("/api/admin/content-options", params={"types": "page", "limit": 1, "offset": 0})
        assert options.status_code == 200
        assert options.json()["limit"] == 1
        assert options.json()["total"] >= 1

        payload = {
            "content_id": item["id"], "content_type": "page", "title": item["title"],
            "slug": item["slug"], "data": item["data"],
        }
        assert client.post("/api/admin/content-preview", json=payload).status_code == 403
        preview = client.post(
            "/api/admin/content-preview", headers={"X-CSRF-Token": viewer_csrf}, json=payload,
        )
        assert preview.status_code == 200
        assert "Цель предпросмотра" in preview.text
        forbidden = client.post(
            "/api/admin/contents", headers={"X-CSRF-Token": viewer_csrf},
            json={"content_type": "page", "title": "Нельзя создать", "data": {"body": []}},
        )
        assert forbidden.status_code == 403


def test_preview_uses_special_public_placements_and_detail_templates(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        cases = [
            ("page", {"placement": "about_history", "body": [paragraph("about", "История храма")]}, "О храме"),
            ("page", {"placement": "school_home", "body": [paragraph("school", "Текст школы")]}, "Воскресная школа"),
            ("page", {"placement": "schedule_info", "body": [paragraph("schedule", "Пояснение расписания")]}, "Расписание"),
            ("parish_section", {"body": [paragraph("parish", "Направление прихода")]}, "Жизнь прихода"),
            ("news", {"body": [paragraph("news", "Текст новости")]}, "Новости прихода"),
            ("clergy", {"biography": [paragraph("clergy", "Биография")]}, "Духовенство"),
            ("gallery", {"photos": []}, "Фотогалерея"),
        ]
        counts_before = database_counts(settings.database_path)
        for index, (content_type, data, expected) in enumerate(cases):
            response = client.post(
                "/api/admin/content-preview", headers=headers,
                json={"content_type": content_type, "title": f"Предпросмотр {index}", "slug": f"preview-{index}", "data": data},
            )
            assert response.status_code == 200, response.text
            assert f"Предпросмотр {index}" in response.text
            assert expected in response.text
            assert 'href="/styles.css"' in response.text
        assert database_counts(settings.database_path) == counts_before
