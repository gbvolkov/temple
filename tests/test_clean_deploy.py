from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import server.legacy_crawl as legacy_crawl
from scripts.validate_production_env import validate as validate_environment
from scripts.build_structured_source import build as build_structured_source, normalized_route_template
from scripts.validate_source_bundle import (
    BundleError,
    create_manifest,
    verify_bundle,
    verify_database,
)
from server.full_import import execute_plan
from server.importer import run_import
from server.legacy_crawl import enforce_read_only_route, inventory_seed_urls, is_read_only_method


SOURCE_URL = "https://www.sv-innokenty.ru/"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def source_bundle(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "bundle"
    media_root = root / "media" / "legacy" / "ab"
    media_root.mkdir(parents=True)
    payload = b"public source media"
    media_file = media_root / "asset.jpg"
    media_file.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    crawl = {
        "schema_version": "1.0.0",
        "base_url": SOURCE_URL,
        "queue": [],
        "remaining_queue": 0,
        "visited": [SOURCE_URL],
        "failures": [],
        "pages": [{
            "url": SOURCE_URL,
            "requested_url": SOURCE_URL,
            "status": 200,
            "images": [{"src": "https://www.sv-innokenty.ru/assets/a.jpg", "alt": ""}],
            "documents": [],
        }],
    }
    plan = {
        "schema_version": "1.0.0",
        "source_fingerprint": "f" * 64,
        "records": [{
            "content_type": "page",
            "slug": "public-source-page",
            "title": "Public source page",
            "legacy_url": "/",
            "data": {"body": [{"type": "legacy_text", "data": {"text": "Source text"}}]},
        }],
        "broken": [],
        "counts_by_type": {"page": 1},
    }
    _write_json(root / "legacy-crawl-checkpoint.json", crawl)
    _write_json(root / "full-import-plan.json", plan)
    _write_json(root / "legacy-inventory.json", {"pages": crawl["pages"], "coverage": {"reachable": 1}})
    _write_json(root / "known-public-routes.json", {"pages": [{"url": SOURCE_URL, "path": "/"}]})
    _write_json(root / "legacy-media-manifest.json", {
        "schema_version": "1.0.0",
        "entries": {"https://www.sv-innokenty.ru/assets/a.jpg": {
            "status": "mirrored",
            "stored_path": "ab/asset.jpg",
            "sha256": digest,
        }},
        "errors": [],
    })
    _write_json(root / "current-sections.json", [{"path": "/"}])
    _write_json(root / "structured-sections.json", [{
        "path": "/",
        "status": 200,
        "title": "Public source page",
        "headings": ["Public source page"],
        "text": "Source text",
        "images": [],
        "documents": [],
    }])
    _write_json(root / "structured-import-dry-run.json", {"records_found": 1, "errors": 0, "dry_run": True})
    (root / "legacy-inventory.md").write_text("# Inventory\n", encoding="utf-8")
    (root / "missing-legacy-media.md").write_text("# Missing media\n", encoding="utf-8")
    (root / "missing-legacy-media.csv").write_text(
        "url,error,directory,references,fallback_mirrored,fallback_url,materials\n",
        encoding="utf-8-sig",
    )
    create_manifest(root, SOURCE_URL, "a" * 40)
    return root, plan


def test_source_bundle_manifest_detects_tampering(tmp_path: Path):
    root, _ = source_bundle(tmp_path)
    result = verify_bundle(root)
    assert result["ok"] is True
    assert result["import"]["records"] == 1
    assert result["media"]["unique_files"] == 1

    (root / "legacy-inventory.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(BundleError, match="file (size|checksum) mismatch"):
        verify_bundle(root)


def test_source_bundle_rejects_files_outside_public_scope(tmp_path: Path):
    root, _ = source_bundle(tmp_path)
    (root / "source-bundle-manifest.json").unlink()
    _write_json(root / "source-users.json", {"users": [{"username": "legacy-admin"}]})
    with pytest.raises(BundleError, match="outside the public migration scope"):
        create_manifest(root, SOURCE_URL, "a" * 40)


def test_source_bundle_rejects_transient_media_failures(tmp_path: Path):
    root, _ = source_bundle(tmp_path)
    (root / "source-bundle-manifest.json").unlink()
    crawl = json.loads((root / "legacy-crawl-checkpoint.json").read_text(encoding="utf-8"))
    transient_url = "https://www.sv-innokenty.ru/assets/transient.pdf"
    crawl["pages"][0]["documents"].append({"href": transient_url, "text": "Transient"})
    _write_json(root / "legacy-crawl-checkpoint.json", crawl)
    media = json.loads((root / "legacy-media-manifest.json").read_text(encoding="utf-8"))
    media["errors"].append({"url": transient_url, "error": "timed out"})
    _write_json(root / "legacy-media-manifest.json", media)
    (root / "missing-legacy-media.csv").write_text(
        "url,error,directory,references,fallback_mirrored,fallback_url,materials\n"
        f"{transient_url},timed out,assets,1,false,,[]\n",
        encoding="utf-8-sig",
    )
    with pytest.raises(BundleError, match="transient, access, size, or transport failures"):
        create_manifest(root, SOURCE_URL, "a" * 40)


def test_source_bundle_rejects_non_permanent_page_failure(tmp_path: Path):
    root, _ = source_bundle(tmp_path)
    (root / "source-bundle-manifest.json").unlink()
    crawl = json.loads((root / "legacy-crawl-checkpoint.json").read_text(encoding="utf-8"))
    crawl["pages"][0]["status"] = 503
    _write_json(root / "legacy-crawl-checkpoint.json", crawl)
    with pytest.raises(BundleError, match="non-permanent HTTP failures"):
        create_manifest(root, SOURCE_URL, "a" * 40)


def test_clean_database_matches_source_bundle(tmp_path: Path):
    root, plan = source_bundle(tmp_path)
    database = tmp_path / "cms.sqlite3"
    run_import(database, root / "structured-sections.json", media_manifest=root / "legacy-media-manifest.json")
    execute_plan(database, root / "legacy-crawl-checkpoint.json", plan)

    result = verify_database(database, root)
    assert result["ok"] is True
    assert result["schema_version"] == 14
    assert result["contents"] == 1
    assert result["redirects"] == 1
    assert result["review_required"] == 1
    assert result["target_users"] == 0
    assert result["source_operational_rows"] == 0

    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO users(
                 id,username,password_hash,role,is_active,created_at,updated_at,password_changed_at
               ) VALUES('target-admin','admin','target-hash','admin',1,'2026-08-01','2026-08-01','2026-08-01')"""
        )
    with pytest.raises(BundleError, match="source_users_absent"):
        verify_database(database, root)
    post_activation = verify_database(database, root, post_activation=True)
    assert post_activation["ok"] is True
    assert post_activation["target_users"] == 1
    assert post_activation["pre_activation_scope_checked"] is False


def test_structured_sections_are_rebuilt_from_fresh_crawl():
    crawl = {"pages": [{
        "requested_url": "https://www.sv-innokenty.ru/o-hrame/istoriya.html",
        "status": 200,
        "title": "Fresh title",
        "headings": ["Fresh heading"],
        "text": "Fresh source text",
        "images": [],
        "documents": [{"text": "№ 149", "href": "https://www.sv-innokenty.ru/assets/149.pdf"}],
    }]}
    result = build_structured_source(crawl, [{"path": "/o-hrame/istoriya.html", "text": "stale"}])
    assert result[0]["text"] == "Fresh source text"
    assert result[0]["documents"][0]["text"] == "№ 149"
    assert normalized_route_template([{"path": "/o-hrame/istoriya.html", "text": "stale"}]) == [
        {"path": "/o-hrame/istoriya.html"}
    ]


def test_known_public_inventory_seeds_orphan_routes():
    seeds = inventory_seed_urls({"pages": [
        {"path": "/known-orphan.html"},
        {"url": "https://www.sv-innokenty.ru/known-orphan.html"},
        {"path": "/still-linked.html"},
    ]}, SOURCE_URL)
    assert seeds == [
        "https://www.sv-innokenty.ru/known-orphan.html",
        "https://www.sv-innokenty.ru/still-linked.html",
    ]


def test_crawl_checkpoint_syncs_remaining_after_duplicate_queue_drain():
    synchronize = getattr(legacy_crawl, "synchronize_checkpoint_state", None)
    assert synchronize is not None
    state = {
        "queue": [],
        "visited": ["https://www.sv-innokenty.ru/"],
        "remaining_queue": 24,
        "updated_at": "stale",
    }

    synchronize(state, {"https://www.sv-innokenty.ru/"})

    assert state["remaining_queue"] == 0
    assert state["visited"] == ["https://www.sv-innokenty.ru/"]
    assert state["updated_at"] != "stale"


def test_public_crawler_allows_only_read_only_http_methods():
    assert all(is_read_only_method(method) for method in ("GET", "head", "OPTIONS"))
    assert all(not is_read_only_method(method) for method in ("POST", "PUT", "PATCH", "DELETE"))

    class Request:
        def __init__(self, method: str):
            self.method = method
            self.url = "https://www.sv-innokenty.ru/analytics"

    class Route:
        def __init__(self, method: str):
            self.request = Request(method)
            self.continued = False
            self.aborted_with = None

        async def continue_(self):
            self.continued = True

        async def abort(self, reason: str):
            self.aborted_with = reason

    blocked: list[dict] = []
    get_route = Route("GET")
    asyncio.run(enforce_read_only_route(get_route, blocked))
    assert get_route.continued is True
    assert blocked == []

    post_route = Route("POST")
    asyncio.run(enforce_read_only_route(post_route, blocked))
    assert post_route.continued is False
    assert post_route.aborted_with == "blockedbyclient"
    assert blocked[0]["method"] == "POST"


def test_production_environment_is_fail_closed(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "CMS_ENV=production",
            "CMS_BOOTSTRAP_USER=admin",
            "CMS_BOOTSTRAP_PASSWORD=correct-horse-battery-staple",
            "CMS_DATABASE=/data/cms.sqlite3",
            "CMS_MEDIA_DIR=/data/media",
            "CMS_MEDIA_DERIVATIVES_DIR=/data/media-derivatives",
            "CMS_LEGACY_CRAWL=/data/legacy-crawl-checkpoint.json",
            "CMS_MEDIA_MANIFEST=/data/legacy-media-manifest.json",
            "CMS_MISSING_MEDIA_REPORT=/data/missing-legacy-media.csv",
            "PUBLIC_BASE_URL=https://temple.gbvolkoff.name:8443",
            "SUBMISSION_IP_HASH_SECRET=" + "s" * 32,
            "SUBMISSION_TRUSTED_PROXY_NETWORKS=127.0.0.0/8,172.16.0.0/12",
            "SMTP_HOST=",
            "SMTP_PORT=",
            "SMTP_USER=",
            "SMTP_PASSWORD=",
            "SMTP_FROM=",
            "SMTP_SECURITY=",
            "SUBMISSION_NOTIFY_TO=",
        ]) + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    assert validate_environment(env_file)["ok"] is True

    env_file.write_text(env_file.read_text(encoding="utf-8").replace(
        "https://temple.gbvolkoff.name:8443", "https://operator:secret@temple.gbvolkoff.name:8443"
    ), encoding="utf-8")
    result = validate_environment(env_file)
    assert any("PUBLIC_BASE_URL" in error for error in result["errors"])
    env_file.write_text(env_file.read_text(encoding="utf-8").replace(
        "https://operator:secret@temple.gbvolkoff.name:8443", "https://temple.gbvolkoff.name:8443"
    ), encoding="utf-8")

    env_file.write_text(env_file.read_text(encoding="utf-8").replace(
        "correct-horse-battery-staple", "replace-with-password"
    ), encoding="utf-8")
    result = validate_environment(env_file)
    assert result["ok"] is False
    assert any("CMS_BOOTSTRAP_PASSWORD" in error for error in result["errors"])

    env_file.write_text(env_file.read_text(encoding="utf-8").replace(
        "SMTP_HOST=", "SMTP_HOST=smtp.example.org"
    ).replace(
        "SMTP_PORT=", "SMTP_PORT=587"
    ).replace(
        "SMTP_USER=", "SMTP_USER=replace-with-user"
    ).replace(
        "SMTP_PASSWORD=", "SMTP_PASSWORD=replace-with-secret"
    ).replace(
        "SMTP_FROM=", "SMTP_FROM=site@example.org"
    ).replace(
        "SMTP_SECURITY=", "SMTP_SECURITY=starttls"
    ).replace(
        "SUBMISSION_NOTIFY_TO=", "SUBMISSION_NOTIFY_TO=recipient@example.org"
    ), encoding="utf-8")
    result = validate_environment(env_file)
    assert any("SMTP settings contain placeholder values" in error for error in result["errors"])
