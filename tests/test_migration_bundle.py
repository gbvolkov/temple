from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import tarfile
from pathlib import Path

import pytest

from server.full_import import execute_plan
from server.migration_bundle import (
    BundleError,
    create_bundle,
    verify_archive,
    verify_bundle,
    verify_database,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "public-source-20260730T120000Z"
    source = root / "source"
    media_file = root / "media/legacy/ab/example.jpg"
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"public-media")
    record = {
        "legacy_id": "legacy-1",
        "content_type": "page",
        "slug": "public-page",
        "title": "Public page",
        "legacy_url": "/public-page/",
        "data": {"summary": "Public summary", "body": []},
    }
    _write_json(source / "legacy-crawl-checkpoint.json", {
        "schema_version": "1.0.0",
        "base_url": "https://www.sv-innokenty.ru/",
        "queue": [],
        "remaining_queue": 0,
        "failures": [],
        "pages": [{"requested_url": "https://www.sv-innokenty.ru/public-page/", "status": 200}],
    })
    _write_json(source / "legacy-inventory.json", {"pages": [{"url": "/public-page/"}]})
    (source / "legacy-inventory.md").write_text("# Inventory\n", encoding="utf-8")
    _write_json(source / "legacy-media-manifest.json", {
        "schema_version": "1.0.0",
        "entries": {
            "https://www.sv-innokenty.ru/assets/example.jpg": {
                "status": "mirrored",
                "stored_path": "ab/example.jpg",
            }
        },
        "errors": [],
    })
    _write_json(source / "full-import-plan.json", {
        "schema_version": "1.0.0",
        "source_fingerprint": "",
        "records": [record],
        "broken": [],
        "counts_by_type": {"page": 1},
    })
    with (source / "missing-legacy-media.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["url", "error"])
        writer.writeheader()
    (source / "missing-legacy-media.md").write_text("# Missing media\n", encoding="utf-8")
    _write_json(source / "current-sections.json", {"records": []})
    return root


def test_bundle_create_verify_and_database_contract(tmp_path: Path):
    root = _bundle(tmp_path)
    manifest = create_bundle(
        root,
        source_url="https://www.sv-innokenty.ru/",
        code_sha="a" * 40,
    )
    assert manifest["scope"] == {
        "public_pages": True,
        "public_media": True,
        "private_drafts": False,
        "source_users": False,
        "source_internal_history": False,
    }
    assert verify_bundle(root)["counts"]["planned_records"] == 1

    database = tmp_path / "cms.sqlite3"
    plan = json.loads((root / "source/full-import-plan.json").read_text(encoding="utf-8"))
    execute_plan(database, root / "source/legacy-crawl-checkpoint.json", plan)
    verified = verify_database(database, root)
    assert verified["schema_version"] == 14
    assert verified["contents"] == verified["review_required"] == 1
    assert verified["source_users_imported"] == 0

    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO users(id,username,password_hash,role,is_active,created_at)
               VALUES('bootstrap','production-admin','hash','admin',1,'2026-07-30T12:00:00Z')"""
        )
    assert verify_database(database, root)["bootstrap_users"] == 1

    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO users(id,username,password_hash,role,is_active,created_at)
               VALUES('source-user','legacy-user','hash','editor',1,'2026-07-30T12:00:00Z')"""
        )
    with pytest.raises(BundleError, match="source users"):
        verify_database(database, root)


def test_bundle_verification_rejects_tampering_and_incomplete_crawl(tmp_path: Path):
    root = _bundle(tmp_path)
    create_bundle(root, source_url="https://www.sv-innokenty.ru/", code_sha="b" * 40)
    (root / "media/legacy/ab/example.jpg").write_bytes(b"tampered")
    with pytest.raises(BundleError, match="checksum mismatch"):
        verify_bundle(root)

    root = _bundle(tmp_path / "second")
    crawl_path = root / "source/legacy-crawl-checkpoint.json"
    crawl = json.loads(crawl_path.read_text(encoding="utf-8"))
    crawl["queue"] = ["https://www.sv-innokenty.ru/pending/"]
    _write_json(crawl_path, crawl)
    with pytest.raises(BundleError, match="queue is not empty"):
        create_bundle(root, source_url="https://www.sv-innokenty.ru/", code_sha="c" * 40)


def test_archive_verification_rejects_links_and_wrong_checksum(tmp_path: Path):
    root = _bundle(tmp_path)
    create_bundle(root, source_url="https://www.sv-innokenty.ru/", code_sha="d" * 40)
    archive = tmp_path / "bundle.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(root, arcname=root.name)
    checksum = tmp_path / "bundle.tar.gz.sha256"
    checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  bundle.tar.gz\n")
    assert verify_archive(archive, checksum)["root"] == root.name

    checksum.write_text(f"{'0' * 64}  bundle.tar.gz\n")
    with pytest.raises(BundleError, match="checksum mismatch"):
        verify_archive(archive, checksum)
