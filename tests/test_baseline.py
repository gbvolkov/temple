from __future__ import annotations

import json
from pathlib import Path

from server.baseline import build_report, verify_restore, write_media_manifest
from server.migrations import migrate


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


def test_restore_verification_checks_migration_artifacts(tmp_path):
    database = tmp_path / "data" / "cms.sqlite3"
    media = tmp_path / "data" / "media"
    media.mkdir(parents=True)
    artifact = tmp_path / "data" / "full-import-plan.json"
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
    assert result["artifact_matches"] == [{"name": "full-import-plan.json", "ok": False}]


def test_public_home_has_honest_empty_news_state():
    source = (Path(__file__).parents[1] / "site" / "app.js").read_text(encoding="utf-8")
    assert "Новости готовятся к публикации" in source
    assert "Фотовыставка памяти Святейшего Патриарха Тихона" not in source
    assert "Новая встреча молодёжного движения прихода" not in source
    assert "Занятия воскресной школы перед Пасхой" not in source
