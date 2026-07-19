from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .config import Settings
from .db import SCHEMA, row_to_content
from .public_urls import (
    DETAIL_PREFIXES,
    LEGACY_INDEX_PATHS,
    STATIC_HASH_TARGETS,
    clean_hash_target,
    content_path,
    is_legacy_index,
    legacy_index_target,
)


MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
)
"""

REQUIRED_COLUMNS = {
    "users": {"id", "username", "password_hash", "role", "is_active", "created_at"},
    "sessions": {"token_hash", "user_id", "csrf_token", "expires_at", "created_at"},
    "contents": {
        "id", "content_type", "slug", "title", "status", "data_json", "legacy_id", "legacy_url",
        "migration_review_required", "version", "created_at", "updated_at", "published_at",
    },
    "revisions": {"id", "content_id", "version", "snapshot_json", "actor_id", "created_at"},
    "redirects": {"old_path", "new_path", "status_code", "created_at"},
    "migration_runs": {
        "id", "source_name", "source_fingerprint", "status", "imported", "updated", "skipped",
        "errors", "report_json", "started_at", "finished_at",
    },
    "media": {
        "id", "original_name", "stored_name", "mime_type", "size_bytes", "alt_text", "uploaded_by", "created_at",
    },
}

ACCIDENTAL_CONTENT_ID = "660f8f7c-1183-464d-b39c-f4df2579fd45"
ACCIDENTAL_CONTENT_SLUG = (
    "o-hrame-novosti-prihoda-arhiv-novostey-2014-god-svyashhenstvo-eto-prizvanie-"
    "pamyati-arhimandrita-ioanna-krestyankina"
)
ACCIDENTAL_CONTENT_VERSION = 7
# A NULL revision actor is the existing schema's representation of an
# automated/system change. Human revisions always carry a users.id value.
SYSTEM_ACTOR_ID: None = None


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], str]
    signature: str = ""
    foreign_keys_off: bool = False

    @property
    def checksum(self) -> str:
        payload = "\n".join((self.name, self.signature, inspect.getsource(self.apply)))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _execute_schema(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA.split(";"):
        if statement.strip():
            connection.execute(statement)


def validate_baseline_schema(connection: sqlite3.Connection, *, create_if_empty: bool = True) -> None:
    existing = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    application_tables = set(REQUIRED_COLUMNS)
    if not (existing & application_tables):
        if not create_if_empty:
            return
        _execute_schema(connection)
        existing = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    missing_tables = sorted(application_tables - existing)
    if missing_tables:
        raise MigrationError(f"В исходной схеме отсутствуют таблицы: {', '.join(missing_tables)}")
    for table, required in REQUIRED_COLUMNS.items():
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        missing = sorted(required - columns)
        if missing:
            raise MigrationError(f"Таблица {table} несовместима; отсутствуют поля: {', '.join(missing)}")


def apply_baseline_schema(connection: sqlite3.Connection) -> str:
    validate_baseline_schema(connection)
    return "baseline schema verified"


def _snapshot_if_missing(connection: sqlite3.Connection, content_id: str) -> None:
    row = connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
    if not row:
        raise MigrationError(f"Материал {content_id} исчез во время миграции")
    exists = connection.execute(
        "SELECT 1 FROM revisions WHERE content_id=? AND version=?", (content_id, row["version"])
    ).fetchone()
    if exists:
        return
    content = row_to_content(row)
    connection.execute(
        "INSERT INTO revisions(content_id,version,snapshot_json,actor_id,created_at) VALUES(?,?,?,?,?)",
        (content_id, row["version"], json.dumps(content, ensure_ascii=False), SYSTEM_ACTOR_ID, utc_now()),
    )


def revert_accidental_publication(connection: sqlite3.Connection) -> str:
    row = connection.execute("SELECT * FROM contents WHERE id=?", (ACCIDENTAL_CONTENT_ID,)).fetchone()
    if row is None:
        return "target absent; no-op"
    if row["slug"] != ACCIDENTAL_CONTENT_SLUG:
        raise MigrationError("ID случайной публикации существует, но slug не совпадает; изменение остановлено")
    if (
        row["status"] == "draft"
        and row["migration_review_required"] == 1
        and row["published_at"] is None
        and row["version"] >= ACCIDENTAL_CONTENT_VERSION + 1
    ):
        return "target already reverted; no-op"
    if row["status"] != "published" or row["version"] != ACCIDENTAL_CONTENT_VERSION:
        raise MigrationError(
            "Случайная публикация имеет неожиданное состояние "
            f"status={row['status']}, version={row['version']}; изменение остановлено"
        )
    _snapshot_if_missing(connection, ACCIDENTAL_CONTENT_ID)
    next_version = row["version"] + 1
    connection.execute(
        """UPDATE contents SET status='draft',migration_review_required=1,published_at=NULL,
           version=?,updated_at=? WHERE id=?""",
        (next_version, utc_now(), ACCIDENTAL_CONTENT_ID),
    )
    _snapshot_if_missing(connection, ACCIDENTAL_CONTENT_ID)
    return f"target reverted to draft version {next_version}"


PUBLICATION_MODEL_SQL = """
CREATE TABLE contents_v3 (
  id TEXT PRIMARY KEY,
  content_type TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  published_slug TEXT,
  title TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('draft','in_review','scheduled','published','archived','trash')),
  data_json TEXT NOT NULL,
  legacy_id TEXT,
  legacy_url TEXT UNIQUE,
  migration_review_required INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1,
  published_version INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  published_at TEXT,
  scheduled_at TEXT,
  reviewed_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(id, published_version) REFERENCES revisions(content_id, version),
  CHECK(published_version IS NULL OR (published_version >= 1 AND published_version <= version)),
  CHECK(published_version IS NULL OR published_slug IS NOT NULL),
  CHECK(status <> 'published' OR published_version IS NOT NULL),
  CHECK((status = 'scheduled' AND scheduled_at IS NOT NULL) OR (status <> 'scheduled' AND scheduled_at IS NULL)),
  CHECK((status = 'trash' AND deleted_at IS NOT NULL) OR (status <> 'trash' AND deleted_at IS NULL))
);
CREATE UNIQUE INDEX idx_contents_published_slug ON contents_v3(published_slug) WHERE published_slug IS NOT NULL;
CREATE INDEX idx_contents_public ON contents_v3(published_version, content_type, published_at);
CREATE INDEX idx_contents_status ON contents_v3(status, updated_at);
CREATE INDEX idx_contents_legacy_id ON contents_v3(legacy_id);
CREATE TABLE audit_events (
  id TEXT PRIMARY KEY,
  content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
  actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  content_version INTEGER NOT NULL,
  published_version INTEGER,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_audit_events_content ON audit_events(content_id, created_at DESC);
CREATE INDEX idx_audit_events_actor ON audit_events(actor_id, created_at DESC);
"""

PUBLICATION_REQUIRED_COLUMNS = {
    "contents": {
        "id", "content_type", "slug", "published_slug", "title", "status", "data_json",
        "legacy_id", "legacy_url", "migration_review_required", "version", "published_version",
        "created_at", "updated_at", "published_at", "scheduled_at", "reviewed_by", "reviewed_at",
        "deleted_at",
    },
    "audit_events": {
        "id", "content_id", "actor_id", "action", "from_status", "to_status", "content_version",
        "published_version", "details_json", "created_at",
    },
}


def validate_publication_schema(connection: sqlite3.Connection) -> None:
    for table, required in PUBLICATION_REQUIRED_COLUMNS.items():
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            raise MigrationError(f"После миграции модели публикации отсутствует таблица {table}")
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing = sorted(required - columns)
        if missing:
            raise MigrationError(f"Таблица {table} не завершена; отсутствуют поля: {', '.join(missing)}")
    indexes = {
        row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    required_indexes = {
        "idx_contents_published_slug", "idx_contents_public", "idx_contents_status",
        "idx_contents_legacy_id", "idx_audit_events_content", "idx_audit_events_actor",
    }
    missing_indexes = sorted(required_indexes - indexes)
    if missing_indexes:
        raise MigrationError(f"После миграции отсутствуют индексы: {', '.join(missing_indexes)}")
    broken_pointers = connection.execute(
        """SELECT COUNT(*) FROM contents c
           LEFT JOIN revisions r ON r.content_id=c.id AND r.version=c.published_version
           WHERE c.published_version IS NOT NULL AND r.id IS NULL"""
    ).fetchone()[0]
    if broken_pointers:
        raise MigrationError(f"Найдены публикации без ревизии: {broken_pointers}")


def apply_publication_model(connection: sqlite3.Connection) -> str:
    before_count = connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
    published_rows = connection.execute(
        "SELECT id FROM contents WHERE status='published' ORDER BY id"
    ).fetchall()
    for row in published_rows:
        _snapshot_if_missing(connection, row["id"])

    connection.execute(PUBLICATION_MODEL_SQL.split(";", 1)[0])
    connection.execute(
        """INSERT INTO contents_v3(
             id,content_type,slug,published_slug,title,status,data_json,legacy_id,legacy_url,
             migration_review_required,version,published_version,created_at,updated_at,published_at,
             scheduled_at,reviewed_by,reviewed_at,deleted_at
           )
           SELECT id,content_type,slug,
                  CASE WHEN status='published' THEN slug ELSE NULL END,
                  title,
                  CASE WHEN status='scheduled' THEN 'draft' ELSE status END,
                  data_json,legacy_id,legacy_url,migration_review_required,version,
                  CASE WHEN status='published' THEN version ELSE NULL END,
                  created_at,updated_at,published_at,NULL,NULL,NULL,NULL
           FROM contents"""
    )
    connection.execute("DROP TABLE contents")
    connection.execute("ALTER TABLE contents_v3 RENAME TO contents")
    statements = PUBLICATION_MODEL_SQL.split(";")[1:]
    for statement in statements:
        sql = statement.strip()
        if sql:
            connection.execute(sql.replace("contents_v3", "contents"))

    after_count = connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
    if after_count != before_count:
        raise MigrationError(f"Количество материалов изменилось: {before_count} -> {after_count}")
    validate_publication_schema(connection)
    return f"publication model created for {after_count} contents; published pointers: {len(published_rows)}"


def validate_clean_redirects(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT old_path,new_path,status_code FROM redirects ORDER BY old_path"
    ).fetchall()
    hash_targets = [row["old_path"] for row in rows if row["new_path"].startswith("/#/")]
    if hash_targets:
        raise MigrationError(f"Остались hash-редиректы: {len(hash_targets)}")
    invalid_statuses = [row["old_path"] for row in rows if row["status_code"] != 301]
    if invalid_statuses:
        raise MigrationError(f"Найдены legacy-редиректы не со статусом 301: {len(invalid_statuses)}")
    old_paths = {row["old_path"] for row in rows}
    chains = []
    for row in rows:
        target_path = row["new_path"].split("#", 1)[0].split("?", 1)[0]
        # The historical root row is never dispatched by middleware and is
        # retained solely to keep the imported row count stable.
        if row["old_path"] != "/" and target_path != "/" and target_path in old_paths:
            chains.append((row["old_path"], target_path))
    if chains:
        raise MigrationError(f"Найдены цепочки legacy-редиректов: {len(chains)}")


def apply_clean_public_urls(connection: sqlite3.Connection) -> str:
    before_count = connection.execute("SELECT COUNT(*) FROM redirects").fetchone()[0]
    rows = connection.execute("SELECT old_path,new_path FROM redirects ORDER BY old_path").fetchall()
    updates: list[tuple[str, str]] = []
    for row in rows:
        old_path = row["old_path"]
        new_path = row["new_path"]
        if new_path.startswith("/#/content/"):
            slug = new_path.removeprefix("/#/content/")
            content = connection.execute(
                "SELECT content_type,slug FROM contents WHERE slug=? OR published_slug=?",
                (slug, slug),
            ).fetchall()
            if len(content) != 1:
                raise MigrationError(
                    f"Detail-редирект {old_path} ссылается на неоднозначный или отсутствующий slug {slug}"
                )
            target = clean_hash_target(
                new_path, content_type=content[0]["content_type"], slug=content[0]["slug"]
            )
        elif new_path.startswith("/#/"):
            if not is_legacy_index(old_path):
                raise MigrationError(f"Неизвестный индексный legacy-URL {old_path} -> {new_path}")
            target = legacy_index_target(old_path)
        else:
            target = new_path
        updates.append((target, old_path))
    connection.executemany("UPDATE redirects SET new_path=? WHERE old_path=?", updates)
    after_count = connection.execute("SELECT COUNT(*) FROM redirects").fetchone()[0]
    if after_count != before_count:
        raise MigrationError(f"Количество редиректов изменилось: {before_count} -> {after_count}")
    validate_clean_redirects(connection)
    return f"converted {after_count} redirects to clean public URLs"


MEDIA_LIBRARY_SQL = """
CREATE TABLE media_usages (
  media_id TEXT NOT NULL REFERENCES media(id) ON DELETE RESTRICT,
  content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
  revision_version INTEGER NOT NULL DEFAULT 0,
  field_path TEXT NOT NULL,
  is_published INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY(media_id, content_id, revision_version, field_path)
);
CREATE INDEX idx_media_usages_content ON media_usages(content_id, revision_version);
CREATE INDEX idx_media_usages_published ON media_usages(media_id, is_published);
CREATE TABLE media_events (
  id TEXT PRIMARY KEY,
  media_id TEXT,
  actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_media_events_media ON media_events(media_id, created_at DESC);
CREATE TABLE missing_media_issues (
  id TEXT PRIMARY KEY,
  source_url TEXT NOT NULL UNIQUE,
  error TEXT NOT NULL,
  source_directory TEXT NOT NULL DEFAULT '',
  reference_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','resolved')),
  replacement_media_id TEXT REFERENCES media(id) ON DELETE SET NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE INDEX idx_missing_media_status ON missing_media_issues(status, updated_at DESC);
CREATE TABLE missing_media_issue_contents (
  issue_id TEXT NOT NULL REFERENCES missing_media_issues(id) ON DELETE CASCADE,
  content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
  PRIMARY KEY(issue_id, content_id)
);
CREATE INDEX idx_missing_media_contents ON missing_media_issue_contents(content_id);
"""

MEDIA_LIBRARY_COLUMNS = {
    "sha256": "TEXT",
    "kind": "TEXT NOT NULL DEFAULT 'document'",
    "source": "TEXT NOT NULL DEFAULT 'upload'",
    "status": "TEXT NOT NULL DEFAULT 'pending'",
    "width": "INTEGER",
    "height": "INTEGER",
    "duration_seconds": "REAL",
    "version": "INTEGER NOT NULL DEFAULT 1",
    "updated_at": "TEXT",
    "replaces_media_id": "TEXT",
    "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
}

MEDIA_LIBRARY_REQUIRED_COLUMNS = {
    "media": set(REQUIRED_COLUMNS["media"]) | set(MEDIA_LIBRARY_COLUMNS),
    "media_usages": {"media_id", "content_id", "revision_version", "field_path", "is_published", "created_at"},
    "media_events": {"id", "media_id", "actor_id", "action", "details_json", "created_at"},
    "missing_media_issues": {
        "id", "source_url", "error", "source_directory", "reference_count", "status",
        "replacement_media_id", "version", "created_at", "updated_at", "resolved_at",
    },
    "missing_media_issue_contents": {"issue_id", "content_id"},
}


def validate_media_library_schema(connection: sqlite3.Connection) -> None:
    for table, required in MEDIA_LIBRARY_REQUIRED_COLUMNS.items():
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            raise MigrationError(f"После миграции медиатеки отсутствует таблица {table}")
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing = sorted(required - columns)
        if missing:
            raise MigrationError(f"Таблица {table} не завершена; отсутствуют поля: {', '.join(missing)}")
    required_indexes = {
        "idx_media_sha256", "idx_media_library", "idx_media_usages_content",
        "idx_media_usages_published", "idx_media_events_media", "idx_missing_media_status",
        "idx_missing_media_contents",
    }
    indexes = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    missing_indexes = sorted(required_indexes - indexes)
    if missing_indexes:
        raise MigrationError(f"После миграции медиатеки отсутствуют индексы: {', '.join(missing_indexes)}")


def apply_media_library(connection: sqlite3.Connection) -> str:
    before = connection.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    existing_columns = {row["name"] for row in connection.execute("PRAGMA table_info(media)")}
    for name, definition in MEDIA_LIBRARY_COLUMNS.items():
        if name not in existing_columns:
            connection.execute(f"ALTER TABLE media ADD COLUMN {name} {definition}")
    connection.execute("UPDATE media SET updated_at=COALESCE(updated_at,created_at)")
    for statement in MEDIA_LIBRARY_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
    connection.execute("CREATE INDEX idx_media_sha256 ON media(sha256)")
    connection.execute("CREATE INDEX idx_media_library ON media(kind,status,source,created_at DESC)")
    after = connection.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    if after != before:
        raise MigrationError(f"Количество media-записей изменилось во время schema migration: {before} -> {after}")
    validate_media_library_schema(connection)
    return f"media library schema created; preserved media rows: {after}"


USER_WORKFLOW_COLUMNS = {
    "version": "INTEGER NOT NULL DEFAULT 1",
    "updated_at": "TEXT",
    "last_login_at": "TEXT",
    "password_changed_at": "TEXT",
}

USER_WORKFLOW_SQL = """
CREATE TABLE user_events (
  id TEXT PRIMARY KEY,
  actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  target_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_user_events_created ON user_events(created_at DESC);
CREATE INDEX idx_user_events_target ON user_events(target_user_id, created_at DESC);
"""


def validate_user_workflow_schema(connection: sqlite3.Connection) -> None:
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    missing = sorted(set(USER_WORKFLOW_COLUMNS) - columns)
    if missing:
        raise MigrationError(
            "Таблица users не завершена; отсутствуют поля: " + ", ".join(missing)
        )
    event_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(user_events)")
    }
    required_events = {
        "id", "actor_id", "target_user_id", "action", "details_json", "created_at",
    }
    missing_events = sorted(required_events - event_columns)
    if missing_events:
        raise MigrationError(
            "Таблица user_events не завершена; отсутствуют поля: "
            + ", ".join(missing_events)
        )
    indexes = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    required_indexes = {
        "idx_users_username_nocase", "idx_user_events_created", "idx_user_events_target",
    }
    missing_indexes = sorted(required_indexes - indexes)
    if missing_indexes:
        raise MigrationError(
            "После миграции пользователей отсутствуют индексы: "
            + ", ".join(missing_indexes)
        )


def apply_user_workflow(connection: sqlite3.Connection) -> str:
    before = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    duplicate = connection.execute(
        """SELECT lower(username) AS normalized, COUNT(*) AS amount
           FROM users GROUP BY lower(username) HAVING amount > 1 LIMIT 1"""
    ).fetchone()
    if duplicate:
        raise MigrationError(
            f"Имена пользователей отличаются только регистром: {duplicate['normalized']}"
        )
    existing_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
    for name, definition in USER_WORKFLOW_COLUMNS.items():
        if name not in existing_columns:
            connection.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
    connection.execute("UPDATE users SET updated_at=COALESCE(updated_at,created_at)")
    for statement in USER_WORKFLOW_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
    connection.execute(
        "CREATE UNIQUE INDEX idx_users_username_nocase ON users(username COLLATE NOCASE)"
    )
    after = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if after != before:
        raise MigrationError(
            f"Количество пользователей изменилось во время schema migration: {before} -> {after}"
        )
    validate_user_workflow_schema(connection)
    return f"user workflow schema created; preserved users: {after}"


VISITOR_SUBMISSIONS_SQL = """
CREATE TABLE submissions (
  id TEXT PRIMARY KEY,
  reference_code TEXT NOT NULL UNIQUE,
  submission_type TEXT NOT NULL CHECK(submission_type IN ('prayer_note','school_enrollment')),
  status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','in_progress','done','spam')),
  payload_json TEXT NOT NULL,
  ip_hash TEXT NOT NULL,
  payload_fingerprint TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  handled_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE INDEX idx_submissions_queue ON submissions(status, created_at DESC);
CREATE INDEX idx_submissions_type_status ON submissions(submission_type, status, created_at DESC);
CREATE INDEX idx_submissions_retention ON submissions(submission_type, status, closed_at);
CREATE INDEX idx_submissions_deduplicate ON submissions(ip_hash, payload_fingerprint, created_at DESC);
CREATE TABLE notification_outbox (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL UNIQUE REFERENCES submissions(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','failed')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
  next_attempt_at TEXT NOT NULL,
  locked_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sent_at TEXT
);
CREATE INDEX idx_notification_outbox_due ON notification_outbox(status, next_attempt_at);
CREATE TABLE submission_events (
  id TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_submission_events_submission ON submission_events(submission_id, created_at DESC);
"""


VISITOR_SUBMISSION_REQUIRED_COLUMNS = {
    "submissions": {
        "id", "reference_code", "submission_type", "status", "payload_json", "ip_hash",
        "payload_fingerprint", "version", "handled_by", "created_at", "updated_at", "closed_at",
    },
    "notification_outbox": {
        "id", "submission_id", "status", "attempts", "next_attempt_at", "locked_at",
        "last_error", "created_at", "updated_at", "sent_at",
    },
    "submission_events": {
        "id", "submission_id", "actor_id", "action", "from_status", "to_status",
        "details_json", "created_at",
    },
}


def validate_visitor_submissions_schema(connection: sqlite3.Connection) -> None:
    for table, required in VISITOR_SUBMISSION_REQUIRED_COLUMNS.items():
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing = sorted(required - columns)
        if missing:
            raise MigrationError(
                f"Таблица {table} не завершена; отсутствуют поля: {', '.join(missing)}"
            )
    indexes = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    required_indexes = {
        "idx_submissions_queue", "idx_submissions_type_status", "idx_submissions_retention",
        "idx_submissions_deduplicate", "idx_notification_outbox_due",
        "idx_submission_events_submission",
    }
    missing_indexes = sorted(required_indexes - indexes)
    if missing_indexes:
        raise MigrationError(
            "После миграции заявок отсутствуют индексы: " + ", ".join(missing_indexes)
        )


def apply_visitor_submissions(connection: sqlite3.Connection) -> str:
    preserved = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("contents", "users", "media", "redirects")
    }
    for statement in VISITOR_SUBMISSIONS_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
    validate_visitor_submissions_schema(connection)
    after = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in preserved
    }
    if after != preserved:
        raise MigrationError(
            f"Количество существующих записей изменилось во время миграции заявок: {preserved} -> {after}"
        )
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise MigrationError(
            f"Миграция заявок нарушила внешние ключи: {len(foreign_key_errors)}"
        )
    return "visitor submission queue and notification outbox created"


MIGRATIONS = (
    Migration(1, "baseline_schema", apply_baseline_schema, SCHEMA),
    Migration(
        2,
        "revert_accidental_2014_publication",
        revert_accidental_publication,
        "\n".join((
            ACCIDENTAL_CONTENT_ID,
            ACCIDENTAL_CONTENT_SLUG,
            str(ACCIDENTAL_CONTENT_VERSION),
            "system_actor=NULL",
            inspect.getsource(_snapshot_if_missing),
        )),
    ),
    Migration(
        3,
        "publication_model_and_audit",
        apply_publication_model,
        "\n".join((
            PUBLICATION_MODEL_SQL,
            inspect.getsource(validate_publication_schema),
            inspect.getsource(_snapshot_if_missing),
        )),
        foreign_keys_off=True,
    ),
    Migration(
        4,
        "clean_public_urls",
        apply_clean_public_urls,
        "\n".join((
            repr(sorted(DETAIL_PREFIXES.items())),
            repr(sorted(LEGACY_INDEX_PATHS)),
            repr(sorted(STATIC_HASH_TARGETS.items())),
            inspect.getsource(clean_hash_target),
            inspect.getsource(content_path),
            inspect.getsource(legacy_index_target),
            inspect.getsource(is_legacy_index),
            inspect.getsource(validate_clean_redirects),
        )),
    ),
    Migration(
        5,
        "media_library",
        apply_media_library,
        "\n".join((
            MEDIA_LIBRARY_SQL,
            repr(sorted(MEDIA_LIBRARY_COLUMNS.items())),
            inspect.getsource(validate_media_library_schema),
        )),
    ),
    Migration(
        6,
        "users_and_editorial_workflow",
        apply_user_workflow,
        "\n".join((
            USER_WORKFLOW_SQL,
            repr(sorted(USER_WORKFLOW_COLUMNS.items())),
            inspect.getsource(validate_user_workflow_schema),
        )),
    ),
    Migration(
        7,
        "visitor_submissions_and_notifications",
        apply_visitor_submissions,
        "\n".join((
            VISITOR_SUBMISSIONS_SQL,
            repr(sorted((name, sorted(columns)) for name, columns in VISITOR_SUBMISSION_REQUIRED_COLUMNS.items())),
            inspect.getsource(validate_visitor_submissions_schema),
        )),
    ),
)


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _applied(connection: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        return {}
    return {
        int(row["version"]): row
        for row in connection.execute("SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version")
    }


def migration_status(path: Path) -> list[dict]:
    if not path.exists():
        applied: dict[int, sqlite3.Row] = {}
    else:
        connection = _connect_readonly(path)
        try:
            applied = _applied(connection)
        finally:
            connection.close()
    status = []
    for migration in MIGRATIONS:
        record = applied.get(migration.version)
        state = "pending"
        if record:
            state = "applied" if record["checksum"] == migration.checksum else "checksum_mismatch"
        status.append({
            "version": migration.version,
            "name": migration.name,
            "checksum": migration.checksum,
            "state": state,
            "applied_at": record["applied_at"] if record else None,
        })
    unknown = sorted(set(applied) - {migration.version for migration in MIGRATIONS})
    if unknown:
        status.append({"version": None, "name": "unknown_applied_versions", "state": "error", "versions": unknown})
    return status


def verify_migrations(path: Path) -> list[dict]:
    status = migration_status(path)
    problems = [item for item in status if item["state"] in {"checksum_mismatch", "error"}]
    if problems:
        raise MigrationError(json.dumps(problems, ensure_ascii=False))
    if path.exists():
        connection = _connect_readonly(path)
        try:
            validate_baseline_schema(connection, create_if_empty=False)
            if any(item.get("version") == 3 and item["state"] == "applied" for item in status):
                validate_publication_schema(connection)
            if any(item.get("version") == 4 and item["state"] == "applied" for item in status):
                validate_clean_redirects(connection)
            if any(item.get("version") == 5 and item["state"] == "applied" for item in status):
                validate_media_library_schema(connection)
            if any(item.get("version") == 6 and item["state"] == "applied" for item in status):
                validate_user_workflow_schema(connection)
            if any(item.get("version") == 7 and item["state"] == "applied" for item in status):
                validate_visitor_submissions_schema(connection)
        finally:
            connection.close()
    return status


def migrate(path: Path, *, dry_run: bool = False) -> list[dict]:
    if dry_run:
        status = verify_migrations(path)
        return [item for item in status if item["state"] == "pending"]
    connection = _connect(path)
    results: list[dict] = []
    try:
        applied_before = _applied(connection)
        known_versions = {migration.version for migration in MIGRATIONS}
        unknown = sorted(set(applied_before) - known_versions)
        if unknown:
            raise MigrationError(f"В БД есть неизвестные версии миграций: {unknown}")
        for migration in MIGRATIONS:
            record = applied_before.get(migration.version)
            if record and record["checksum"] != migration.checksum:
                raise MigrationError(
                    f"Checksum применённой миграции {migration.version} {migration.name} изменён"
                )
        pending = [migration for migration in MIGRATIONS if migration.version not in applied_before]
        foreign_keys_off = any(migration.foreign_keys_off for migration in pending)
        if foreign_keys_off:
            connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(MIGRATION_TABLE_SQL)
        applied = _applied(connection)
        for migration in MIGRATIONS:
            record = applied.get(migration.version)
            if record:
                if record["checksum"] != migration.checksum:
                    raise MigrationError(
                        f"Checksum применённой миграции {migration.version} {migration.name} изменён"
                    )
                results.append({"version": migration.version, "name": migration.name, "state": "unchanged"})
                continue
            detail = migration.apply(connection)
            applied_at = utc_now()
            connection.execute(
                "INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                (migration.version, migration.name, migration.checksum, applied_at),
            )
            results.append({"version": migration.version, "name": migration.name, "state": "applied", "detail": detail})
        if foreign_keys_off:
            foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                raise MigrationError(f"Миграция нарушила внешние ключи: {len(foreign_key_errors)}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.execute("PRAGMA foreign_keys = ON")
        connection.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Управляет версионированными миграциями CMS")
    parser.add_argument("command", choices=("status", "verify", "up"))
    parser.add_argument("--database", type=Path, default=Settings.from_env().database_path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "status":
        result = migration_status(args.database)
    elif args.command == "verify":
        result = verify_migrations(args.database)
    else:
        result = migrate(args.database, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
