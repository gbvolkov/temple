import hashlib
import json
from pathlib import Path

from server.importer import media_mapping, rewrite_media_urls, run_import
from server.legacy_crawl import normalize_url
from server.legacy_inventory import build_inventory, classify_path, suspicious_path
from server.media_mirror import collect_assets, download_asset
from server.media_report import build_report
from server.migration_audit import audit


ROOT = Path(__file__).resolve().parents[1]


def test_inventory_reports_partial_coverage_and_broken_links():
    crawl = json.loads((ROOT / "current-crawl.json").read_text(encoding="utf-8"))
    sections = json.loads((ROOT / "current-sections.json").read_text(encoding="utf-8"))
    inventory = build_inventory(crawl, sections)

    assert inventory["source"]["inventory_pages"] == 160
    assert inventory["source"]["remaining_queue"] == 1224
    assert inventory["coverage"]["successful_pages"] == 139
    assert inventory["coverage"]["broken_pages"] == 21
    assert inventory["coverage"]["crawl_complete"] is False
    assert inventory["coverage"]["detailed_snapshot_ratio"] == 0.0938
    assert inventory["counts_by_legacy_type"]["gallery_album"] > 50


def test_classification_and_suspicious_path_detection():
    assert classify_path("/o-hrame/raspisanie-bogosluzheniy.html", 200) == "schedule"
    assert classify_path("/o-hrame/fotogalereya/2014/album.html", 200) == "gallery_album"
    assert classify_path("/voskresnaya-shkola/zhizn.html", 200) == "school"
    assert classify_path("/anything.html", 404) == "broken"
    assert suspicious_path("/o-hrame/o-hrame/novosti-prihoda.html") is True


def test_playwright_crawler_normalizes_only_safe_internal_pages():
    host = "www.sv-innokenty.ru"
    base = "https://www.sv-innokenty.ru/o-hrame/"
    assert normalize_url("../kontakty.html?utm=1#map", base, host) == "https://www.sv-innokenty.ru/kontakty.html"
    assert normalize_url("https://example.com/page.html", base, host) is None
    assert normalize_url("/manager/index.php", base, host) is None
    assert normalize_url("/assets/file.pdf", base, host) is None
    assert normalize_url("/o-hrame/istoriya.html", base, host) == "https://www.sv-innokenty.ru/o-hrame/istoriya.html"


def test_media_collection_download_and_manifest_rewrite(tmp_path, monkeypatch):
    sections = json.loads((ROOT / "current-sections.json").read_text(encoding="utf-8"))
    assets = collect_assets(sections)
    assert len(assets) > 150
    assert len({asset["url"] for asset in assets}) == len(assets)
    assert all(asset["url"].startswith("https://www.sv-innokenty.ru/") for asset in assets)

    payload = b"read-only-mirror-test"

    class FakeHeaders(dict):
        def get_content_type(self):
            return "image/jpeg"

    class FakeResponse:
        headers = FakeHeaders({"Content-Length": str(len(payload))})

        def __enter__(self):
            self.offset = 0
            return self

        def __exit__(self, *_):
            return False

        def read(self, _):
            if self.offset:
                return b""
            self.offset = len(payload)
            return payload

    monkeypatch.setattr("server.media_mirror.urlopen", lambda *args, **kwargs: FakeResponse())
    result = download_asset({"url": "https://www.sv-innokenty.ru/assets/photo.jpg", "kind": "image"}, tmp_path, 1024)
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert (tmp_path / result["stored_path"]).read_bytes() == payload

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": {result["url"]: result}}), encoding="utf-8")
    mapping = media_mapping(manifest)
    rewritten = rewrite_media_urls({"cover": result["url"], "nested": [result["url"]]}, mapping)
    assert rewritten == {"cover": result["local_url"], "nested": [result["local_url"]]}


def test_readiness_report_refuses_partial_migration(tmp_path):
    crawl = json.loads((ROOT / "current-crawl.json").read_text(encoding="utf-8"))
    sections = json.loads((ROOT / "current-sections.json").read_text(encoding="utf-8"))
    inventory = build_inventory(crawl, sections)
    database = tmp_path / "cms.sqlite3"
    run_import(database, ROOT / "current-sections.json")

    report = audit(inventory, sections, database)

    assert report["ready_for_cutover"] is False
    assert report["gates"]["legacy_records_imported"] is True
    assert report["gates"]["no_unreviewed_content_published"] is True
    assert report["gates"]["full_public_crawl"] is False
    assert report["new_cms"]["records"] == 167
    assert report["media"]["known_assets"] > 150


def test_missing_media_report_links_material_and_mirrored_fallback():
    original = "https://www.sv-innokenty.ru/assets/images/missing.jpg"
    fallback = "https://www.sv-innokenty.ru/assets/cache/images/150x150-missing.jpg"
    plan = {"records": [{"title": "Событие", "legacy_url": "/event.html", "data": {"photos": [{"image": original, "fallback": fallback}]}}]}
    manifest = {
        "entries": {fallback: {"status": "mirrored", "local_url": "/media/legacy/fallback.jpg"}},
        "errors": [{"url": original, "error": "HTTP Error 404: Not Found"}],
    }
    rows = build_report(plan, manifest)
    assert rows[0]["references"] == 1
    assert rows[0]["fallback_mirrored"] is True
    assert rows[0]["materials"][0]["legacy_url"] == "/event.html"
