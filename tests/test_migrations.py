from __future__ import annotations

import sqlite3

import pytest

from server.db import SCHEMA
from server.migrations import (
    ACCIDENTAL_CONTENT_ID,
    ACCIDENTAL_CONTENT_SLUG,
    MigrationError,
    migrate,
    migration_status,
    verify_migrations,
)


def legacy_database(path):
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()


def test_fresh_database_and_repeat_are_idempotent(tmp_path):
    database = tmp_path / "fresh.sqlite3"
    first = migrate(database)
    assert [item["state"] for item in first] == ["applied", "applied"]
    second = migrate(database)
    assert [item["state"] for item in second] == ["unchanged", "unchanged"]
    assert all(item["state"] == "applied" for item in migration_status(database))
    verify_migrations(database)


def test_existing_legacy_schema_is_stamped_without_losing_data(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    legacy_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users(id,username,password_hash,role,is_active,created_at) VALUES('u','admin','hash','admin',1,'now')"
        )
    migrate(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2


def test_dry_run_does_not_create_or_change_database(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    legacy_database(database)
    before = database.read_bytes()
    pending = migrate(database, dry_run=True)
    assert [item["version"] for item in pending] == [1, 2]
    assert database.read_bytes() == before
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()[0] == 0


def test_checksum_mismatch_stops_verification_and_migration(tmp_path):
    database = tmp_path / "checksum.sqlite3"
    migrate(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE schema_migrations SET checksum='tampered' WHERE version=1")
    with pytest.raises(MigrationError, match="checksum_mismatch"):
        verify_migrations(database)
    with pytest.raises(MigrationError, match="Checksum"):
        migrate(database)


def test_incompatible_legacy_schema_is_rejected(tmp_path):
    database = tmp_path / "broken.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE contents(id TEXT PRIMARY KEY)")
    with pytest.raises(MigrationError, match="отсутствуют таблицы"):
        migrate(database)


def test_accidental_publication_is_reverted_and_other_content_is_untouched(tmp_path):
    database = tmp_path / "content.sqlite3"
    legacy_database(database)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """INSERT INTO contents(id,content_type,slug,title,status,data_json,legacy_id,legacy_url,
               migration_review_required,version,created_at,updated_at,published_at)
               VALUES(?,?,?,?,?,'{}',NULL,NULL,0,7,'created','updated','published')""",
            (ACCIDENTAL_CONTENT_ID, "news", ACCIDENTAL_CONTENT_SLUG, "Случайная публикация", "published"),
        )
        connection.execute(
            """INSERT INTO contents(id,content_type,slug,title,status,data_json,legacy_id,legacy_url,
               migration_review_required,version,created_at,updated_at,published_at)
               VALUES('other','news','other','Другая публикация','published','{}',NULL,NULL,0,3,
               'created','updated','published')"""
        )
    migrate(database)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        target = connection.execute("SELECT * FROM contents WHERE id=?", (ACCIDENTAL_CONTENT_ID,)).fetchone()
        other = connection.execute("SELECT * FROM contents WHERE id='other'").fetchone()
        assert target["status"] == "draft"
        assert target["migration_review_required"] == 1
        assert target["published_at"] is None
        assert target["version"] == 8
        assert connection.execute(
            "SELECT GROUP_CONCAT(version, ',') FROM revisions WHERE content_id=? ORDER BY version",
            (ACCIDENTAL_CONTENT_ID,),
        ).fetchone()[0] == "7,8"
        assert connection.execute(
            "SELECT actor_id FROM revisions WHERE content_id=? AND version=8",
            (ACCIDENTAL_CONTENT_ID,),
        ).fetchone()[0] is None
        assert other["status"] == "published"
        assert other["version"] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM contents WHERE status='published'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM contents WHERE migration_review_required=1"
        ).fetchone()[0] == 1
    migrate(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM revisions WHERE content_id=?", (ACCIDENTAL_CONTENT_ID,)
        ).fetchone()[0] == 2
