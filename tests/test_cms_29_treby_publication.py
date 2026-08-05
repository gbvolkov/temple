import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Settings
from server.db import connect
from server.full_import import execute_plan, record_from_page
from server.migration_acceptance import execute_audit_run, queue_audit
from server.migration_preparation import (
    execute_preparation_run,
    list_preparation_items,
    queue_preparation,
)


ROOT = Path(__file__).resolve().parents[1]


LEGACY_TREBY_URL = (
    "https://www.sv-innokenty.ru/"
    "%D1%82%D1%80%D0%B5%D0%B1%D1%8B-%D0%BE%D0%BD%D0%BB%D0%B0%D0%B9%D0%BD.html"
)


def legacy_treby_page() -> dict:
    return {
        "url": LEGACY_TREBY_URL,
        "requested_url": LEGACY_TREBY_URL,
        "status": 200,
        "title": "ТРЕБЫ ОНЛАЙН | Храм святителя Иннокентия",
        "headings": ["ТРЕБЫ ОНЛАЙН"],
        "text": (
            "ТРЕБЫ ОНЛАЙН\n"
            "Главная - ТРЕБЫ ОНЛАЙН\n"
            "Требы, рекомендованное пожертвование\n"
            "о здравии, о упокоении: 120;\n"
            "молебен о здравии: 170;\n"
            "панихида: 250;\n"
            "(c) Храм святителя Иннокентия"
        ),
        "images": [],
        "documents": [
            {"text": "QR-код", "href": "/media/legacy/treby-qr.pdf"},
        ],
        "outgoing": [],
    }


def test_cms_29_legacy_treby_page_is_routable(tmp_path) -> None:
    record = record_from_page(legacy_treby_page())
    assert record is not None
    assert record["content_type"] == "page"
    assert record["data"]["placement"] == "treby_info"

    source = tmp_path / "crawl.json"
    source.write_text("{}", encoding="utf-8")
    database = tmp_path / "cms.sqlite3"
    execute_plan(database, source, {"records": [record], "broken": []})

    with connect(database) as connection:
        imported = connection.execute(
            "SELECT data_json FROM contents WHERE legacy_url='/требы-онлайн.html'"
        ).fetchone()
        assert imported is not None
        assert json.loads(imported["data_json"])["placement"] == "treby_info"
        redirect = connection.execute(
            "SELECT new_path FROM redirects WHERE old_path='/требы-онлайн.html'"
        ).fetchone()
        assert redirect is not None
        assert redirect["new_path"] == "/treby"


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


def login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/admin/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def create_and_publish_treby(client: TestClient, headers: dict[str, str]) -> dict:
    data = {
        "placement": "treby_info",
        "summary": "Перечень треб и порядок подачи записок.",
        "body": [
            {
                "type": "heading",
                "data": {"level": 2, "runs": [{"text": "Перечень треб", "marks": []}]},
            },
            {
                "type": "list",
                "data": {
                    "style": "bulleted",
                    "items": [
                        {"runs": [{"text": "Записка о здравии", "marks": []}]},
                        {"runs": [{"text": "Панихида", "marks": []}]},
                    ],
                },
            },
            {
                "type": "file",
                "data": {
                    "url": "/assets/school-maslenitsa.jpg",
                    "label": "Памятка",
                    "description": "Приложение к перечню",
                },
            },
        ],
    }
    created = client.post(
        "/api/admin/contents",
        headers=headers,
        json={"content_type": "page", "title": "Требы и записки", "data": data},
    )
    assert created.status_code == 201, created.text
    item = created.json()
    reviewed = client.post(
        f"/api/admin/contents/{item['id']}/submit-review",
        headers=headers,
        json={"version": item["version"]},
    )
    assert reviewed.status_code == 200, reviewed.text
    published = client.post(
        f"/api/admin/contents/{item['id']}/publish",
        headers=headers,
        json={"version": item["version"]},
    )
    assert published.status_code == 200, published.text
    return published.json()


def test_cms_29_public_route_preview_visibility_and_singleton(tmp_path: Path) -> None:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        headers = login(client)
        empty = client.get("/treby")
        assert empty.status_code == 200
        assert "Информация готовится к публикации" in empty.text

        preview = client.post(
            "/api/admin/content-preview",
            headers=headers,
            json={
                "content_type": "page",
                "title": "Предпросмотр треб",
                "data": {
                    "placement": "treby_info",
                    "body": [{"type": "paragraph", "data": {"text": "Текст предпросмотра"}}],
                },
            },
        )
        assert preview.status_code == 200, preview.text
        assert "Текст предпросмотра" in preview.text
        assert "Требы и записки" in preview.text

        item = create_and_publish_treby(client, headers)
        public = client.get("/treby")
        assert public.status_code == 200
        assert "Записка о здравии" in public.text
        assert "Панихида" in public.text
        assert "Памятка" in public.text
        detail = client.get(f"/pages/{item['slug']}", follow_redirects=False)
        assert detail.status_code == 301
        assert detail.headers["location"] == "/treby"

        duplicate = client.post(
            "/api/admin/contents",
            headers=headers,
            json={
                "content_type": "page",
                "title": "Второй перечень",
                "data": {
                    "placement": "treby_info",
                    "body": [{"type": "paragraph", "data": {"text": "Дубликат"}}],
                },
            },
        ).json()
        reviewed = client.post(
            f"/api/admin/contents/{duplicate['id']}/submit-review",
            headers=headers,
            json={"version": duplicate["version"]},
        )
        assert reviewed.status_code == 200
        conflict = client.post(
            f"/api/admin/contents/{duplicate['id']}/publish",
            headers=headers,
            json={"version": duplicate["version"]},
        )
        assert conflict.status_code == 409


def test_cms_29_preparation_places_existing_draft_and_preserves_documents(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        created = client.post(
            "/api/admin/contents",
            headers=headers,
            json={
                "content_type": "page",
                "title": "ТРЕБЫ ОНЛАЙН",
                "data": {
                    "body": [{"type": "paragraph", "data": {"text": "Временный текст"}}],
                },
            },
        ).json()
        legacy = {
            "summary": "",
            "body": [{"type": "legacy_text", "text": legacy_treby_page()["text"]}],
            "body_text": legacy_treby_page()["text"],
            "legacy_documents": [
                {"text": "QR-код", "href": "/assets/school-maslenitsa.jpg"},
            ],
            "migration_note": "Импортировано из crawl",
        }
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "UPDATE contents SET data_json=?,legacy_url=?,migration_review_required=1 WHERE id=?",
                (
                    json.dumps(legacy, ensure_ascii=False),
                    "/требы-онлайн.html",
                    created["id"],
                ),
            )

        audit = queue_audit(
            settings.database_path,
            actor_id=None,
            scope={"check_external": False},
        )
        execute_audit_run(
            settings.database_path,
            settings.schema_path,
            settings.media_dir,
            settings.site_dir,
            audit["id"],
            check_external=False,
        )
        queued = queue_preparation(settings.database_path, actor_id=None)
        preview = execute_preparation_run(settings, queued["id"])
        planned = list_preparation_items(settings.database_path, preview["id"])["items"][0]
        assert planned["classification"] == "editorial_review"
        assert planned["proposed"]["data"]["placement"] == "treby_info"
        assert {block["type"] for block in planned["proposed"]["data"]["body"]} >= {
            "paragraph",
            "file",
        }
        assert {reason["code"] for reason in planned["reasons"]} >= {
            "treby_details_require_editor",
        }

        apply_run = queue_preparation(
            settings.database_path,
            actor_id=None,
            mode="apply",
            preview_run_id=preview["id"],
        )
        execute_preparation_run(settings, apply_run["id"])
        with sqlite3.connect(settings.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT data_json,migration_review_required FROM contents WHERE id=?",
                (created["id"],),
            ).fetchone()
            data = json.loads(row["data_json"])
            assert data["placement"] == "treby_info"
            assert any(block["type"] == "file" for block in data["body"])
            assert row["migration_review_required"] == 1
