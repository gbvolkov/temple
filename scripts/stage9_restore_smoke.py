from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request

from stage7_restore_smoke import client


def raw_request(base: str, path: str) -> tuple[int, bytes, str]:
    try:
        with urllib.request.urlopen(base + path, timeout=20) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers.get("Content-Type", "")


def main() -> None:
    port, credentials_path, database_path = sys.argv[1:]
    base = f"http://127.0.0.1:{port}"
    with open(credentials_path, encoding="utf-8") as source:
        credentials = json.load(source)

    public_request, _ = client(base)
    status, existing = public_request("/api/public/search?q=Павел")
    assert status == 200 and existing["total"] >= 1, existing

    admin_request, admin_login = client(base)
    admin_login(credentials["username"], credentials["password"])
    draft_data = {
        "publication_date": "2026-07-19",
        "category": "Проверка публикации",
        "summary": "Одноразовый материал в восстановленной копии.",
        "cover": "assets/school-maslenitsa.jpg",
        "cover_alt": "Проверка social preview",
        "body": [{
            "id": "stage9-smoke-paragraph",
            "type": "paragraph",
            "data": {"runs": [{"text": "stage9alpha уникальный поисковый маркер", "marks": []}]},
        }],
        "seo_title": "Проверка поиска и SEO",
        "seo_description": "Одноразовая проверка этапа 9 на восстановленной базе.",
    }
    status, draft = admin_request(
        "/api/admin/contents",
        method="POST",
        payload={"content_type": "news", "title": "Проверка этапа 9", "data": draft_data},
    )
    assert status == 201 and draft["status"] == "draft", draft
    status, hidden = public_request("/api/public/search?q=stage9alpha")
    assert status == 200 and hidden["total"] == 0, hidden

    status, reviewed = admin_request(
        f"/api/admin/contents/{draft['id']}/submit-review",
        method="POST",
        payload={"version": draft["version"]},
    )
    assert status == 200 and reviewed["status"] == "in_review", reviewed
    status, published = admin_request(
        f"/api/admin/contents/{draft['id']}/publish",
        method="POST",
        payload={"version": reviewed["version"]},
    )
    assert status == 200 and published["published_version"] == 1, published
    status, found = public_request("/api/public/search?q=stage9alpha")
    assert status == 200 and found["total"] == 1, found
    assert found["items"][0]["id"] == draft["id"]

    clean_path = found["items"][0]["url"]
    status, html, content_type = raw_request(base, clean_path)
    assert status == 200 and content_type.startswith("text/html"), content_type
    decoded = html.decode("utf-8")
    assert f'<link rel="canonical" href="https://temple.gbvolkoff.name:8443{clean_path}">' in decoded
    assert "Проверка поиска и SEO" in decoded and '"NewsArticle"' in decoded
    social_path = f"/social-preview/content/{draft['id']}/v1.jpg"
    status, social, content_type = raw_request(base, social_path)
    assert status == 200 and content_type.startswith("image/jpeg") and social.startswith(b"\xff\xd8")

    updated_data = dict(draft_data)
    updated_data["body"] = [{
        "id": "stage9-smoke-paragraph-v2",
        "type": "paragraph",
        "data": {"runs": [{"text": "stage9beta новый маркер рабочей версии", "marks": []}]},
    }]
    status, updated = admin_request(
        f"/api/admin/contents/{draft['id']}",
        method="PUT",
        payload={
            "version": published["version"],
            "slug": published["published_slug"],
            "title": "Проверка этапа 9 — версия 2",
            "data": updated_data,
        },
    )
    assert status == 200 and updated["version"] == 2 and updated["published_version"] == 1, updated
    status, old_result = public_request("/api/public/search?q=stage9alpha")
    assert status == 200 and old_result["total"] == 1, old_result
    status, new_result = public_request("/api/public/search?q=stage9beta")
    assert status == 200 and new_result["total"] == 0, new_result

    status, reviewed = admin_request(
        f"/api/admin/contents/{draft['id']}/submit-review",
        method="POST",
        payload={"version": updated["version"]},
    )
    assert status == 200, reviewed
    status, published = admin_request(
        f"/api/admin/contents/{draft['id']}/publish",
        method="POST",
        payload={"version": reviewed["version"]},
    )
    assert status == 200 and published["published_version"] == 2, published
    status, new_result = public_request("/api/public/search?q=stage9beta")
    assert status == 200 and new_result["total"] == 1, new_result

    for path, expected_type in (
        ("/sitemap.xml", "application/xml"),
        ("/robots.txt", "text/plain"),
        ("/rss.xml", "application/rss+xml"),
        ("/social-preview/site.jpg", "image/jpeg"),
    ):
        status, body, content_type = raw_request(base, path)
        assert status == 200 and content_type.startswith(expected_type) and body, (path, status, content_type)

    status, archived = admin_request(
        f"/api/admin/contents/{draft['id']}/archive",
        method="POST",
        payload={"version": published["version"]},
    )
    assert status == 200 and archived["status"] == "archived", archived
    status, hidden = public_request("/api/public/search?q=stage9beta")
    assert status == 200 and hidden["total"] == 0, hidden

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM content_search WHERE content_id=?", (draft["id"],)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
        ).fetchone()[0] == 8


if __name__ == "__main__":
    main()
