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
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(MIGRATION_TABLE_SQL)
        applied = _applied(connection)
        known_versions = {migration.version for migration in MIGRATIONS}
        unknown = sorted(set(applied) - known_versions)
        if unknown:
            raise MigrationError(f"В БД есть неизвестные версии миграций: {unknown}")
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
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
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
