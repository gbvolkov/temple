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
    assert [item["state"] for item in first] == ["applied"] * 5
    second = migrate(database)
    assert [item["state"] for item in second] == ["unchanged"] * 5
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
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 5


def test_dry_run_does_not_create_or_change_database(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    legacy_database(database)
    before = database.read_bytes()
    pending = migrate(database, dry_run=True)
    assert [item["version"] for item in pending] == [1, 2, 3, 4, 5]
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


def test_publication_model_backfills_existing_publication_and_preserves_foreign_keys(tmp_path):
    database = tmp_path / "published.sqlite3"
    legacy_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO contents(id,content_type,slug,title,status,data_json,legacy_id,legacy_url,
               migration_review_required,version,created_at,updated_at,published_at)
               VALUES('clergy','clergy','pavel','Павел Николаев','published','{}',NULL,NULL,0,8,
               'created','updated','published')"""
        )
    migrate(database)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM contents WHERE id='clergy'").fetchone()
        assert row["status"] == "published"
        assert row["published_version"] == 8
        assert row["published_slug"] == "pavel"
        assert connection.execute(
            "SELECT COUNT(*) FROM revisions WHERE content_id='clergy' AND version=8"
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='audit_events'"
        ).fetchone()[0] == 1
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE contents SET published_version=7 WHERE id='clergy'")
        connection.execute(
            "UPDATE contents SET status='in_review' WHERE id='clergy'"
        )


def test_clean_url_migration_converts_static_detail_and_year_redirects(tmp_path):
    database = tmp_path / "redirects.sqlite3"
    legacy_database(database)
    with sqlite3.connect(database) as connection:
        for index, (content_type, slug) in enumerate((
            ("news", "news-one"),
            ("gallery", "gallery-one"),
            ("parish_section", "parish-one"),
            ("clergy", "clergy-one"),
            ("page", "page-one"),
        )):
            connection.execute(
                """INSERT INTO contents(id,content_type,slug,title,status,data_json,legacy_id,legacy_url,
                   migration_review_required,version,created_at,updated_at,published_at)
                   VALUES(?,?,?,?,?,'{}',NULL,NULL,1,1,'created','updated',NULL)""",
                (f"content-{index}", content_type, slug, slug, "draft"),
            )
            connection.execute(
                "INSERT INTO redirects(old_path,new_path,status_code,created_at) VALUES(?,?,301,'now')",
                (f"/legacy/{content_type}-{index}.html", f"/#/content/{slug}"),
            )
        for old_path, old_target in (
            ("/o-hrame/novosti-prihoda.html", "/#/about"),
            ("/kontakty.html", "/#/about"),
            ("/o-hrame/duhovenstvo.html", "/#/about"),
            ("/o-hrame/raspisanie-bogosluzheniy.html", "/#/schedule"),
            ("/o-hrame/fotogalereya/20241.html", "/#/gallery"),
        ):
            connection.execute(
                "INSERT INTO redirects(old_path,new_path,status_code,created_at) VALUES(?,?,301,'now')",
                (old_path, old_target),
            )

    migrate(database)
    with sqlite3.connect(database) as connection:
        destinations = dict(connection.execute("SELECT old_path,new_path FROM redirects"))
        assert destinations["/legacy/news-0.html"] == "/news/news-one"
        assert destinations["/legacy/gallery-1.html"] == "/gallery/gallery-one"
        assert destinations["/legacy/parish_section-2.html"] == "/parish/parish-one"
        assert destinations["/legacy/clergy-3.html"] == "/about/clergy/clergy-one"
        assert destinations["/legacy/page-4.html"] == "/pages/page-one"
        assert destinations["/o-hrame/novosti-prihoda.html"] == "/news"
        assert destinations["/kontakty.html"] == "/about#contacts"
        assert destinations["/o-hrame/duhovenstvo.html"] == "/about#clergy"
        assert destinations["/o-hrame/raspisanie-bogosluzheniy.html"] == "/schedule"
        assert destinations["/o-hrame/fotogalereya/20241.html"] == "/gallery?year=2024"
        assert connection.execute("SELECT COUNT(*) FROM redirects WHERE new_path LIKE '/#/%'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0] == 5
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_clean_url_migration_rejects_unknown_hash_index(tmp_path):
    database = tmp_path / "unknown-redirect.sqlite3"
    legacy_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO redirects(old_path,new_path,status_code,created_at) VALUES('/unexpected.html','/#/about',301,'now')"
        )
    with pytest.raises(MigrationError, match="legacy-URL"):
        migrate(database)
