import json

from server.db import connect
from server.full_import import build_full_plan, execute_plan, record_from_page


def page(url, *, status=200, title="Материал", images=None, outgoing=None):
    return {
        "url": url,
        "requested_url": url,
        "status": status,
        "title": title + " | Храм святителя Иннокентия",
        "headings": [title],
        "text": "Снимок текста старого сайта",
        "images": images or [],
        "documents": [],
        "outgoing": outgoing or [],
    }


def test_full_plan_maps_indexes_items_and_high_quality_gallery_images():
    gallery_url = "https://www.sv-innokenty.ru/o-hrame/fotogalereya/2025/album.html"
    gallery = page(
        gallery_url,
        title="Праздник",
        images=[{"src": "https://www.sv-innokenty.ru/assets/cache/150x150-photo.jpg", "alt": ""}],
        outgoing=["https://www.sv-innokenty.ru/assets/cache/800x600-photo.jpg"],
    )
    plan = build_full_plan({"pages": [
        page("https://www.sv-innokenty.ru/o-hrame/novosti-prihoda.html", title="Новости прихода"),
        page("https://www.sv-innokenty.ru/o-hrame/novosti-prihoda/event.html", title="Событие"),
        gallery,
        page("https://www.sv-innokenty.ru/zhizn-prihoda/molodezhnoe-dvizhenie.html", title="Молодёжное движение", images=[{"src": "https://www.sv-innokenty.ru/assets/one.jpg", "alt": ""}] * 4),
        page("https://www.sv-innokenty.ru/kontakty.html", title="Контакты"),
        page("https://www.sv-innokenty.ru/broken.html", status=404),
    ]})

    assert plan["counts_by_type"] == {"gallery": 1, "news": 1, "page": 1, "parish_section": 1, "site_contact": 1}
    assert len(plan["broken"]) == 1
    gallery_record = next(record for record in plan["records"] if record["content_type"] == "gallery")
    assert gallery_record["data"]["photos"][0]["image"].endswith("800x600-photo.jpg")
    assert record_from_page(page("https://www.sv-innokenty.ru/broken.html", status=404)) is None


def test_missing_original_uses_mirrored_thumbnail_fallback():
    original = "https://www.sv-innokenty.ru/assets/images/album/photo.jpg"
    thumbnail = "https://www.sv-innokenty.ru/assets/cache/images/album/150x150-photo.jpg"
    gallery = page(
        "https://www.sv-innokenty.ru/o-hrame/fotogalereya/2016/album.html",
        images=[{"src": thumbnail, "alt": "Фото"}],
        outgoing=[original],
    )
    plan = build_full_plan({"pages": [gallery]}, {thumbnail: "/media/legacy/photo.jpg"}, {original})
    record = plan["records"][0]
    assert record["data"]["photos"][0]["image"] == "/media/legacy/photo.jpg"
    assert record["data"]["cover"] == "/media/legacy/photo.jpg"


def test_full_plan_execution_is_draft_only_and_idempotent(tmp_path):
    source = tmp_path / "crawl.json"
    source.write_text("{}", encoding="utf-8")
    database = tmp_path / "cms.sqlite3"
    plan = build_full_plan({"pages": [
        page("https://www.sv-innokenty.ru/o-hrame/novosti-prihoda/event.html", title="Событие"),
        page("https://www.sv-innokenty.ru/kontakty.html", title="Контакты"),
    ]})

    first = execute_plan(database, source, plan)
    second = execute_plan(database, source, plan)

    assert first["imported"] == 2
    assert second["skipped"] == 2
    with connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM contents WHERE status='draft' AND migration_review_required=1").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM redirects").fetchone()[0] == 2
        assert connection.execute(
            "SELECT new_path FROM redirects WHERE old_path='/o-hrame/novosti-prihoda/event.html'"
        ).fetchone()[0] == "/news/o-hrame-novosti-prihoda-event"
        assert connection.execute(
            "SELECT new_path FROM redirects WHERE old_path='/kontakty.html'"
        ).fetchone()[0] == "/about#contacts"


def test_full_import_can_refine_type_of_unreviewed_legacy_record(tmp_path):
    source = tmp_path / "crawl.json"
    source.write_text("{}", encoding="utf-8")
    database = tmp_path / "cms.sqlite3"
    early_plan = {"records": [{
        "content_type": "page", "slug": "kontakty", "title": "Контакты", "legacy_url": "/kontakty.html",
        "data": {"body": [{"type": "legacy_text", "text": "old"}]},
    }], "broken": []}
    refined_plan = build_full_plan({"pages": [page("https://www.sv-innokenty.ru/kontakty.html", title="Контакты")]})
    execute_plan(database, source, early_plan)
    execute_plan(database, source, refined_plan)
    with connect(database) as connection:
        assert connection.execute("SELECT content_type FROM contents WHERE legacy_url='/kontakty.html'").fetchone()[0] == "site_contact"
