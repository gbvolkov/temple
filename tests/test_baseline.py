from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import pytest

from server.baseline import build_report, verify_restore, write_media_manifest
from server.migrations import migrate
from scripts.run_cms_regression import stable_source_report


def test_cms_61_media_snapshot_falls_back_when_hardlinks_are_denied(tmp_path, monkeypatch):
    import os
    from server.baseline import copy_snapshot_file

    source = tmp_path / "source.jpg"
    target = tmp_path / "copy.jpg"
    source.write_bytes(b"immutable source")
    before = source.stat()
    def deny_link(*args, **kwargs):
        raise PermissionError("Hardlinks are unavailable in this Windows sandbox")
    monkeypatch.setattr(os, "link", deny_link)
    assert Path(copy_snapshot_file(str(source), str(target))) == target
    assert target.read_bytes() == source.read_bytes() == b"immutable source"
    assert target.stat().st_mtime_ns == before.st_mtime_ns
    target.write_bytes(b"isolated edit")
    assert source.read_bytes() == b"immutable source"


def test_cms_57_source_report_detects_row_changes_without_count_changes(tmp_path):
    database = tmp_path / "cms.sqlite3"
    media = tmp_path / "media"
    media.mkdir()
    migrate(database)
    with sqlite3.connect(database) as connection:
        connection.execute("""INSERT INTO contents(id,content_type,slug,title,status,data_json,migration_review_required,version,created_at,updated_at)
            VALUES('record','page','record','Исходный заголовок','draft','{}',1,1,'now','now')""")
    before = stable_source_report(database, media)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE contents SET title='Изменённый заголовок' WHERE id='record'")
    assert stable_source_report(database, media) != before


def test_cms_58_source_manifest_is_resolved_from_source_database(tmp_path):
    from scripts.run_cms_regression import source_manifest_path

    source = tmp_path / "external-source"
    source.mkdir()
    database = source / "cms.sqlite3"
    manifest = source / "legacy-media-manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    assert source_manifest_path(database) == manifest.resolve()
    migrate(database)
    media = source / "media"
    media.mkdir()
    before = stable_source_report(database, media, manifest)
    manifest.write_text('{"changed": true}', encoding="utf-8")
    assert stable_source_report(database, media, manifest) != before
    manifest.unlink()
    import pytest
    with pytest.raises(FileNotFoundError):
        source_manifest_path(database)


def test_baseline_report_counts_public_materials_by_published_pointer(tmp_path):
    database = tmp_path / "cms.sqlite3"
    media = tmp_path / "media"
    media.mkdir()
    migrate(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO contents(
                 id,content_type,slug,title,status,data_json,migration_review_required,
                 version,created_at,updated_at,published_version,published_slug
               ) VALUES('published-draft','page','draft','Draft','draft','{}',1,2,
                        'created','updated',1,'public')"""
        )

    report = build_report(database, media)

    assert report["database"]["metrics"]["published"] == 1
    assert report["database"]["metrics"]["missing_media_issues"] == 0


def test_baseline_report_never_contains_environment_values(tmp_path):
    database = tmp_path / "cms.sqlite3"
    media = tmp_path / "media"
    media.mkdir()
    (media / "one.jpg").write_bytes(b"image-one")
    (media / "two.pdf").write_bytes(b"document-two")
    artifact = tmp_path / "crawl.json"
    artifact.write_text('{"ok":true}', encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("CMS_ENV=production\nCMS_BOOTSTRAP_PASSWORD=top-secret\n", encoding="utf-8")
    migrate(database)

    report = build_report(
        database,
        media,
        git_sha="abc123",
        tag="baseline-test",
        image_id="sha256:image",
        baseline_tag_sha="tag-sha",
        env_file=env_file,
        artifacts=[artifact],
    )

    encoded = json.dumps(report, ensure_ascii=False)
    assert "top-secret" not in encoded
    assert report["environment_variable_names"] == ["CMS_BOOTSTRAP_PASSWORD", "CMS_ENV"]
    assert report["database"]["quick_check"] == ["ok"]
    assert report["database"]["foreign_key_error_count"] == 0
    assert report["source"]["baseline_tag_sha"] == "tag-sha"
    assert report["media"] == {"path": str(media.resolve()), "files": 2, "size_bytes": 21}
    assert report["artifacts"][0]["name"] == "crawl.json"


def test_restore_verification_detects_media_change(tmp_path):
    database = tmp_path / "cms.sqlite3"
    media = tmp_path / "media"
    media.mkdir()
    file = media / "photo.jpg"
    file.write_bytes(b"original")
    migrate(database)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(build_report(database, media)), encoding="utf-8")
    manifest_path = tmp_path / "media.jsonl"
    write_media_manifest(media, manifest_path)

    assert verify_restore(database, media, report_path, manifest_path)["ok"] is True
    file.write_bytes(b"changed")
    result = verify_restore(database, media, report_path, manifest_path)
    assert result["ok"] is False
    assert result["media"]["mismatched"] == ["photo.jpg"]


@pytest.mark.parametrize("artifact_name", ["full-import-plan.json", "legacy-media-manifest.json", "legacy-crawl-checkpoint.json", "missing-legacy-media.csv", "missing-legacy-media.md"])
def test_restore_verification_checks_migration_artifacts(tmp_path, artifact_name):
    database = tmp_path / "data" / "cms.sqlite3"
    media = tmp_path / "data" / "media"
    media.mkdir(parents=True)
    artifact = tmp_path / "data" / artifact_name
    artifact.write_text('{"baseline":true}', encoding="utf-8")
    migrate(database)
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(build_report(database, media, artifacts=[artifact])), encoding="utf-8"
    )
    manifest_path = tmp_path / "media.jsonl"
    write_media_manifest(media, manifest_path)

    assert verify_restore(database, media, report_path, manifest_path)["ok"] is True
    artifact.write_text('{"baseline":false}', encoding="utf-8")
    result = verify_restore(database, media, report_path, manifest_path)
    assert result["ok"] is False
    assert result["artifact_matches"] == [{"name": artifact_name, "ok": False}]


def test_public_home_has_honest_empty_news_state():
    source = (Path(__file__).parents[1] / "site" / "templates" / "home.html").read_text(encoding="utf-8")
    assert "Новости готовятся к публикации" in source
    assert "Фотовыставка памяти Святейшего Патриарха Тихона" not in source
    assert "Новая встреча молодёжного движения прихода" not in source
    assert "Занятия воскресной школы перед Пасхой" not in source
