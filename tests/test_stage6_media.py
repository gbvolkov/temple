from __future__ import annotations

import csv
import io
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path

import av
from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.config import Settings
from server.db import init_database
from server.media_library import index_library
from server.security import hash_password


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


def login(client: TestClient, username: str = "admin", password: str = "test-password") -> dict[str, str]:
    response = client.post("/api/admin/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def image_bytes(color: str = "#9b633a", format_name: str = "JPEG") -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (64, 40), color).save(stream, format_name)
    return stream.getvalue()


def pdf_bytes() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (64, 40), "white").save(stream, "PDF")
    return stream.getvalue()


def video_bytes() -> bytes:
    stream = io.BytesIO()
    with av.open(stream, mode="w", format="mp4") as container:
        video = container.add_stream("mpeg4", rate=1)
        video.width = 64
        video.height = 40
        video.pix_fmt = "yuv420p"
        frame = av.VideoFrame.from_image(Image.new("RGB", (64, 40), "#334455"))
        for packet in video.encode(frame):
            container.mux(packet)
        for packet in video.encode():
            container.mux(packet)
    return stream.getvalue()


def docx_bytes(*, with_macros: bool = False) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, mode="w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        if with_macros:
            archive.writestr("word/vbaProject.bin", b"macro")
    return stream.getvalue()


def upload(client: TestClient, headers: dict[str, str], name: str, body: bytes, mime: str, alt: str = ""):
    return client.post(
        "/api/admin/media",
        headers=headers,
        files={"file": (name, body, mime)},
        data={"alt_text": alt},
    )


def test_media_migration_is_idempotent_and_preserves_existing_rows(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(media)")}
        assert {"sha256", "kind", "source", "status", "width", "height", "version"} <= columns
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'media_%'"
            )
        }
        assert {"media_usages", "media_events"} <= tables
        assert "missing_media_issues" in {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 9
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    init_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 9


def test_upload_validates_bytes_deduplicates_and_builds_derivatives(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        original = image_bytes()
        first = upload(client, headers, "photo.jpg", original, "application/octet-stream", "Алтарь храма")
        assert first.status_code == 201, first.text
        item = first.json()
        assert item["kind"] == "image"
        assert item["mime_type"] == "image/jpeg"
        assert item["width"] == 64 and item["height"] == 40
        assert item["deduplicated"] is False
        assert client.get(item["url"]).content == original
        derivative = client.get(item["thumbnail_url"])
        assert derivative.status_code == 200
        assert derivative.headers["content-type"].startswith("image/webp")
        assert derivative.headers["cache-control"].endswith("immutable")

        duplicate = upload(client, headers, "copy.jpeg", original, "image/jpeg")
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == item["id"]
        assert duplicate.json()["deduplicated"] is True

        listed = client.get("/api/admin/media", params={"q": "photo", "kind": "image"})
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["id"] == item["id"]

        invalid = upload(client, headers, "fake.jpg", b"not an image", "image/jpeg")
        assert invalid.status_code == 415
        disguised = upload(client, headers, "image.pdf", original, "application/pdf")
        assert disguised.status_code == 415
        assert client.get("/media/../cms.sqlite3").status_code in {400, 404}


def test_pdf_preview_metadata_stale_version_and_roles(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        response = upload(client, headers, "leaflet.pdf", pdf_bytes(), "application/pdf")
        assert response.status_code == 201, response.text
        item = response.json()
        assert item["kind"] == "document"
        assert item["metadata"]["page_count"] == 1
        assert client.get(item["preview_url"]).headers["content-type"].startswith("image/webp")

        changed = client.patch(
            f"/api/admin/media/{item['id']}",
            headers=headers,
            json={"version": item["version"], "alt_text": "Новый alt"},
        )
        assert changed.status_code == 200
        assert changed.json()["version"] == item["version"] + 1
        stale = client.patch(
            f"/api/admin/media/{item['id']}",
            headers=headers,
            json={"version": item["version"], "alt_text": "Устаревшая правка"},
        )
        assert stale.status_code == 409

        with sqlite3.connect(settings.database_path) as connection:
            connection.execute(
                "INSERT INTO users(id,username,password_hash,role,is_active,created_at) VALUES(?,?,?,?,1,'now')",
                ("viewer", "viewer", hash_password("viewer-password"), "viewer"),
            )
        viewer_headers = login(client, "viewer", "viewer-password")
        assert client.get("/api/admin/media").status_code == 200
        forbidden = upload(client, viewer_headers, "blocked.jpg", image_bytes("red"), "image/jpeg")
        assert forbidden.status_code == 403


def test_mp4_is_parsed_from_its_contents(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        response = upload(client, headers, "service.mp4", video_bytes(), "application/octet-stream")
        assert response.status_code == 201, response.text
        item = response.json()
        assert item["kind"] == "video"
        assert item["mime_type"] == "video/mp4"
        assert item["width"] == 64 and item["height"] == 40


def test_office_text_and_configurable_limits(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings = replace(settings, max_image_bytes=100)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        too_large = upload(client, headers, "large.jpg", image_bytes(), "image/jpeg")
        assert too_large.status_code == 413

        document = upload(client, headers, "document.docx", docx_bytes(), "application/zip")
        assert document.status_code == 201, document.text
        assert document.json()["mime_type"].endswith("wordprocessingml.document")
        macro = upload(client, headers, "macro.docx", docx_bytes(with_macros=True), "application/zip")
        assert macro.status_code == 415
        text = upload(client, headers, "notes.txt", "Текст".encode(), "text/plain")
        assert text.status_code == 201, text.text
        binary_text = upload(client, headers, "binary.txt", b"text\x00binary", "text/plain")
        assert binary_text.status_code == 415


def test_usage_protects_original_and_replacement_gets_new_url(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        original = upload(client, headers, "original.jpg", image_bytes(), "image/jpeg", "Фото").json()
        created = client.post(
            "/api/admin/contents",
            headers=headers,
            json={
                "content_type": "page",
                "title": "Страница с фотографией",
                "data": {
                    "body": [
                        {
                            "id": "image-block",
                            "type": "image",
                            "data": {"image": original["url"], "alt": "Фото", "caption": ""},
                        }
                    ]
                },
            },
        )
        assert created.status_code == 201, created.text
        item = created.json()
        usages = client.get(f"/api/admin/media/{original['id']}/usages").json()
        assert usages["total"] >= 1
        refused = client.delete(
            f"/api/admin/media/{original['id']}?version={original['version']}", headers=headers
        )
        assert refused.status_code == 409

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
        page = client.get(f"/pages/{published.json()['published_slug']}")
        assert page.status_code == 200
        assert original["url"] in page.text
        assert f"/media-derivatives/{original['id']}/web.webp" in page.text

        replacement = client.post(
            f"/api/admin/media/{original['id']}/replacement",
            headers=headers,
            files={"file": ("replacement.jpg", image_bytes(), "image/jpeg")},
        )
        assert replacement.status_code == 201, replacement.text
        new_item = replacement.json()
        assert new_item["id"] != original["id"]
        assert new_item["url"] != original["url"]
        assert new_item["replaces_media_id"] == original["id"]
        assert client.get(original["url"]).status_code == 200
        assert original["url"] in client.get(f"/pages/{published.json()['published_slug']}").text

        unused = upload(client, headers, "unused.jpg", image_bytes("#778899"), "image/jpeg").json()
        removed = client.delete(
            f"/api/admin/media/{unused['id']}?version={unused['version']}", headers=headers
        )
        assert removed.status_code == 204
        assert client.get(unused["url"]).status_code == 404


def test_unregistered_or_missing_media_blocks_review_and_schedule(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        created = client.post(
            "/api/admin/contents",
            headers=headers,
            json={
                "content_type": "page",
                "title": "Сломанная ссылка",
                "data": {
                    "body": [
                        {
                            "id": "missing-image",
                            "type": "image",
                            "data": {"image": "/media/missing.jpg", "alt": "Нет файла", "caption": ""},
                        }
                    ]
                },
            },
        ).json()
        refused = client.post(
            f"/api/admin/contents/{created['id']}/submit-review",
            headers=headers,
            json={"version": created["version"]},
        )
        assert refused.status_code == 422
        assert refused.json()["detail"]["media"][0]["url"] == "/media/missing.jpg"


def test_indexer_is_repeatable_and_imports_missing_queue(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.media_dir.mkdir(parents=True)
    (settings.media_dir / "legacy-photo.jpg").write_bytes(image_bytes())
    report_path = tmp_path / "missing.csv"
    with report_path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=["url", "error", "directory", "references", "fallback_mirrored", "fallback_url", "materials"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "url": "https://legacy.example/missing.jpg",
                "error": "404",
                "directory": "/images",
                "references": "1",
                "fallback_mirrored": "False",
                "fallback_url": "",
                "materials": "",
            }
        )
    init_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """INSERT INTO media(
                 id,original_name,stored_name,mime_type,size_bytes,alt_text,uploaded_by,created_at
               ) VALUES('legacy-row','Human readable name.jpg','legacy-photo.jpg','image/jpeg',0,'',NULL,'now')"""
        )
    first = index_library(settings.database_path, settings.media_dir, missing_report=report_path)
    second = index_library(settings.database_path, settings.media_dir, missing_report=report_path)
    assert first["files"] == first["ready"] == 1
    assert first["updated"] == 1
    assert second["files"] == second["ready"] == 1
    assert second["updated"] == 1
    with sqlite3.connect(settings.database_path) as connection:
        row = connection.execute("SELECT source,status,sha256,original_name FROM media").fetchone()
        assert row[0:2] == ("upload", "ready")
        assert len(row[2]) == 64
        assert row[3] == "Human readable name.jpg"
        assert connection.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM missing_media_issues").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
