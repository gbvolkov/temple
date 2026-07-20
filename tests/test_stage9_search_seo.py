from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.config import Settings
from server.db import init_database
from server.search import rebuild_search_index, search_index_problems


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_BASE_URL = "https://temple.gbvolkoff.name:8443"


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
        public_base_url=PUBLIC_BASE_URL,
        submission_ip_hash_secret="stage9-test-hmac-secret-value",
        submission_worker_interval_seconds=3600,
    )


def login(client: TestClient) -> dict[str, str]:
    response = client.post("/api/admin/login", json={"username": "admin", "password": "test-password"})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def paragraph(block_id: str, text: str) -> dict:
    return {"id": block_id, "type": "paragraph", "data": {"runs": [{"text": text, "marks": []}]}}


def content_data(content_type: str, body_text: str, **extra) -> dict:
    base = {"body": [paragraph("block-1", body_text)], **extra}
    if content_type == "news":
        return {
            "publication_date": "2026-07-19",
            "category": "Новости прихода",
            "summary": "Краткое описание важного события прихода",
            "cover": "assets/school-maslenitsa.jpg",
            "cover_alt": "Приходской праздник",
            **base,
        }
    return base


def create_content(
    client: TestClient,
    headers: dict[str, str],
    content_type: str,
    title: str,
    body_text: str,
    **extra,
) -> dict:
    response = client.post(
        "/api/admin/contents",
        headers=headers,
        json={
            "content_type": content_type,
            "title": title,
            "data": content_data(content_type, body_text, **extra),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def publish(client: TestClient, headers: dict[str, str], item: dict) -> dict:
    response = client.post(
        f"/api/admin/contents/{item['id']}/submit-review",
        headers=headers,
        json={"version": item["version"]},
    )
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/admin/contents/{item['id']}/publish",
        headers=headers,
        json={"version": item["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_migration_8_backfill_verify_and_reindex_are_idempotent(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    before = settings.database_path.read_bytes()
    init_database(settings.database_path)
    assert settings.database_path.read_bytes() == before
    with sqlite3.connect(settings.database_path) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 9
        assert "fts5" in connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='content_search'"
        ).fetchone()[0].lower()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert search_index_problems(connection) == []
        assert rebuild_search_index(connection) == 0
        assert search_index_problems(connection) == []


def test_search_uses_only_published_snapshot_and_tracks_workflow(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        hidden = create_content(client, headers, "news", "Скрытый черновик", "тайный черновой маркер")
        assert client.get("/api/public/search", params={"q": "тайный"}).json()["total"] == 0

        item = publish(
            client,
            headers,
            create_content(client, headers, "news", "Пасхальная встреча прихода", "праздничный концерт воскресной школы"),
        )
        result = client.get("/api/public/search", params={"q": "ПАСХАЛЬН встреч"})
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload["total"] == 1
        assert payload["items"][0]["id"] == item["id"]
        assert payload["items"][0]["url"] == f"/news/{item['published_slug']}"
        assert payload["facets"] == {"news": 1}

        updated_data = content_data("news", "совершенно новый поисковый маркер")
        updated = client.put(
            f"/api/admin/contents/{item['id']}",
            headers=headers,
            json={
                "version": item["version"],
                "slug": item["published_slug"],
                "title": "Изменённая рабочая версия",
                "data": updated_data,
            },
        )
        assert updated.status_code == 200, updated.text
        draft = updated.json()
        assert draft["published_version"] == 1 and draft["version"] == 2
        assert client.get("/api/public/search", params={"q": "Пасхальн"}).json()["total"] == 1
        assert client.get("/api/public/search", params={"q": "совершенно"}).json()["total"] == 0

        published_v2 = publish(client, headers, draft)
        assert client.get("/api/public/search", params={"q": "совершенно новый"}).json()["total"] == 1
        assert client.get("/api/public/search", params={"q": "Пасхальн"}).json()["total"] == 0

        archived = client.post(
            f"/api/admin/contents/{item['id']}/archive",
            headers=headers,
            json={"version": published_v2["version"]},
        )
        assert archived.status_code == 200
        assert client.get("/api/public/search", params={"q": "совершенно"}).json()["total"] == 0
        assert hidden["status"] == "draft"


def test_search_validation_pagination_filters_and_html_statuses(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        for index in range(3):
            publish(
                client,
                headers,
                create_content(client, headers, "news", f"Летопись прихода {index}", "общий поисковый текст"),
            )
        publish(
            client,
            headers,
            create_content(client, headers, "page", "История летописи", "общий поисковый текст"),
        )

        response = client.get(
            "/api/public/search",
            params={"q": "общий летоп", "content_type": "news", "page": 1, "per_page": 2},
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 3
        assert response.json()["pages"] == 2
        assert len(response.json()["items"]) == 2
        assert set(response.json()["facets"]) == {"news", "page"}
        injected = client.get("/api/public/search", params={"q": 'общий OR "*" летоп'})
        assert injected.status_code == 200 and injected.json()["total"] == 0
        assert client.get("/api/public/search", params={"q": "а"}).status_code == 422
        assert client.get("/api/public/search", params={"q": "общий", "content_type": "unknown"}).status_code == 422
        assert client.get("/search").status_code == 200
        assert "noindex" in client.get("/search").text
        assert client.get("/search", params={"q": "а"}).status_code == 400
        assert client.get("/search", params={"q": "общий", "page": 99}).status_code == 404


def test_seo_social_preview_sitemap_rss_robots_and_errors(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        headers = login(client)
        item = publish(
            client,
            headers,
            create_content(
                client,
                headers,
                "news",
                "Большой приходской праздник",
                "Подробный рассказ о торжественном богослужении",
                seo_title="Праздник в Бескудникове",
                seo_description="Авторское описание новости для поисковых систем.",
            ),
        )
        url = f"/news/{item['published_slug']}"
        page = client.get(url)
        assert page.status_code == 200
        assert '<link rel="canonical" href="' + PUBLIC_BASE_URL + url + '">' in page.text
        assert '<meta property="og:title" content="Праздник в Бескудникове">' in page.text
        assert '<meta name="twitter:card" content="summary_large_image">' in page.text
        assert '"NewsArticle"' in page.text and '"Church"' in page.text and '"SearchAction"' in page.text
        assert PUBLIC_BASE_URL + f"/social-preview/content/{item['id']}/v1.jpg" in page.text

        card = client.get(f"/social-preview/content/{item['id']}/v1.jpg")
        assert card.status_code == 200
        card_path = tmp_path / "card.jpg"
        card_path.write_bytes(card.content)
        with Image.open(card_path) as image:
            assert image.size == (1200, 630)
            assert image.format == "JPEG"
        site_card = client.get("/social-preview/site.jpg")
        assert site_card.status_code == 200
        assert "immutable" in site_card.headers["cache-control"]

        sitemap = client.get("/sitemap.xml")
        assert sitemap.status_code == 200
        assert PUBLIC_BASE_URL + url in sitemap.text
        assert "/search" not in sitemap.text and "#/" not in sitemap.text
        robots = client.get("/robots.txt")
        assert robots.status_code == 200
        assert "Disallow: /api/" in robots.text
        assert f"Sitemap: {PUBLIC_BASE_URL}/sitemap.xml" in robots.text
        rss = client.get("/rss.xml")
        assert rss.status_code == 200
        assert PUBLIC_BASE_URL + url in rss.text
        assert "Большой приходской праздник" in rss.text

        missing = client.get("/missing-public-page")
        assert missing.status_code == 404 and "noindex" in missing.text
        failed = client.get("/__stage9-error-test")
        assert failed.status_code == 500 and "noindex" in failed.text
        assert "stage 9 public error test" not in failed.text
        api_missing = client.get("/api/not-found")
        assert api_missing.status_code == 404 and api_missing.headers["content-type"].startswith("application/json")


def test_special_page_redirect_and_seo_field_validation(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        too_long = create_content(
            client,
            headers,
            "page",
            "Временная страница",
            "текст",
        )
        invalid = client.put(
            f"/api/admin/contents/{too_long['id']}",
            headers=headers,
            json={
                "version": too_long["version"],
                "slug": too_long["slug"],
                "title": too_long["title"],
                "data": content_data("page", "текст", seo_title="x" * 71),
            },
        )
        assert invalid.status_code == 422

        special = publish(
            client,
            headers,
            create_content(client, headers, "page", "О воскресной школе", "описание школы", placement="school_home"),
        )
        redirect = client.get(f"/pages/{special['published_slug']}", follow_redirects=False)
        assert redirect.status_code == 301
        assert redirect.headers["location"] == "/school"
        gallery = client.get("/gallery", params={"year": "2026", "page": "1"})
        assert f'{PUBLIC_BASE_URL}/gallery?year=2026' in gallery.text
        assert "page=1" not in gallery.text
