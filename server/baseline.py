from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def readonly_connection(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def scalar(connection: sqlite3.Connection, query: str) -> int:
    return int(connection.execute(query).fetchone()[0] or 0)


def database_report(database: Path) -> dict:
    with readonly_connection(database) as connection:
        quick_check = [row[0] for row in connection.execute("PRAGMA quick_check").fetchall()]
        foreign_key_errors = [dict(row) for row in connection.execute("PRAGMA foreign_key_check").fetchall()]
        metrics = {}
        for table in (
            "users", "sessions", "contents", "revisions", "redirects", "migration_runs",
            "media", "audit_events", "user_events", "submissions", "notification_outbox",
            "submission_events",
        ):
            metrics[table] = scalar(connection, f"SELECT COUNT(*) FROM {table}") if table_exists(connection, table) else None
        by_status = {}
        by_type = {}
        by_type_status = []
        review_required = 0
        if table_exists(connection, "contents"):
            by_status = {
                row["status"]: int(row["count"])
                for row in connection.execute("SELECT status,COUNT(*) AS count FROM contents GROUP BY status")
            }
            by_type = {
                row["content_type"]: int(row["count"])
                for row in connection.execute("SELECT content_type,COUNT(*) AS count FROM contents GROUP BY content_type")
            }
            by_type_status = [
                dict(row) for row in connection.execute(
                    "SELECT content_type,status,COUNT(*) AS count FROM contents GROUP BY content_type,status ORDER BY content_type,status"
                )
            ]
            review_required = scalar(connection, "SELECT COUNT(*) FROM contents WHERE migration_review_required=1")
        schema_version: int | str = "unversioned"
        if table_exists(connection, "schema_migrations"):
            schema_version = scalar(connection, "SELECT COALESCE(MAX(version),0) FROM schema_migrations")
    return {
        "path": str(database.resolve()),
        "size_bytes": database.stat().st_size,
        "quick_check": quick_check,
        "foreign_key_error_count": len(foreign_key_errors),
        "foreign_key_errors": foreign_key_errors,
        "schema_version": schema_version,
        "metrics": metrics,
        "review_required": review_required,
        "by_status": dict(sorted(by_status.items())),
        "by_type": dict(sorted(by_type.items())),
        "by_type_status": by_type_status,
    }


def media_files(media_dir: Path) -> list[Path]:
    return sorted((path for path in media_dir.rglob("*") if path.is_file()), key=lambda path: path.as_posix())


def media_summary(media_dir: Path) -> dict:
    files = media_files(media_dir)
    return {
        "path": str(media_dir.resolve()),
        "files": len(files),
        "size_bytes": sum(path.stat().st_size for path in files),
    }


def env_names(env_file: Path | None) -> list[str]:
    if env_file is None or not env_file.exists():
        return []
    names = []
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        if name and name.replace("_", "").isalnum():
            names.append(name)
    return sorted(set(names))


def artifact_checksums(paths: Iterable[Path]) -> list[dict]:
    records = []
    for path in paths:
        if path.exists() and path.is_file():
            records.append({
                "name": path.name,
                "path": str(path.resolve()),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return sorted(records, key=lambda item: item["name"])


def build_report(
    database: Path,
    media_dir: Path,
    *,
    git_sha: str = "",
    tag: str = "",
    image_id: str = "",
    baseline_tag_sha: str = "",
    env_file: Path | None = None,
    artifacts: Iterable[Path] = (),
) -> dict:
    return {
        "format_version": 1,
        "generated_at": utc_now(),
        "source": {
            "git_sha": git_sha,
            "tag": tag,
            "baseline_tag_sha": baseline_tag_sha,
            "docker_image_id": image_id,
        },
        "database": database_report(database),
        "media": media_summary(media_dir),
        "artifacts": artifact_checksums(artifacts),
        "environment_variable_names": env_names(env_file),
    }


def write_json(value: object, output: Path | None) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)


def write_media_manifest(media_dir: Path, output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    total = 0
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for path in media_files(media_dir):
            size = path.stat().st_size
            record = {
                "path": path.relative_to(media_dir).as_posix(),
                "size_bytes": size,
                "sha256": sha256_file(path),
            }
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
            total += size
    return {"files": count, "size_bytes": total, "manifest_sha256": sha256_file(output)}


def read_manifest(path: Path) -> dict[str, dict]:
    records = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        relative = record.get("path")
        if not relative or relative in records:
            raise ValueError(f"Некорректная запись media manifest в строке {line_number}")
        records[relative] = record
    return records


def verify_media(media_dir: Path, manifest: Path) -> dict:
    expected = read_manifest(manifest)
    actual_paths = {path.relative_to(media_dir).as_posix(): path for path in media_files(media_dir)}
    missing = sorted(set(expected) - set(actual_paths))
    extra = sorted(set(actual_paths) - set(expected))
    mismatched = []
    for relative in sorted(set(expected) & set(actual_paths)):
        path = actual_paths[relative]
        record = expected[relative]
        size = path.stat().st_size
        if size != int(record["size_bytes"]) or sha256_file(path) != record["sha256"]:
            mismatched.append(relative)
    result = {
        "ok": not missing and not extra and not mismatched,
        "files": len(actual_paths),
        "size_bytes": sum(path.stat().st_size for path in actual_paths.values()),
        "missing": missing,
        "extra": extra,
        "mismatched": mismatched,
    }
    return result


def verify_restore(database: Path, media_dir: Path, report_path: Path, manifest_path: Path) -> dict:
    expected = json.loads(report_path.read_text(encoding="utf-8"))
    database_actual = database_report(database)
    media_actual = verify_media(media_dir, manifest_path)
    database_matches = {
        "metrics": database_actual["metrics"] == expected["database"]["metrics"],
        "schema_version": database_actual["schema_version"] == expected["database"]["schema_version"],
        "review_required": database_actual["review_required"] == expected["database"]["review_required"],
        "by_status": database_actual["by_status"] == expected["database"]["by_status"],
        "by_type": database_actual["by_type"] == expected["database"]["by_type"],
        "by_type_status": database_actual["by_type_status"] == expected["database"]["by_type_status"],
    }
    artifact_matches = []
    for record in expected.get("artifacts", []):
        restored_path = database.parent / record["name"]
        artifact_matches.append({
            "name": record["name"],
            "ok": (
                restored_path.is_file()
                and restored_path.stat().st_size == record["size_bytes"]
                and sha256_file(restored_path) == record["sha256"]
            ),
        })
    expected_media = expected["media"]
    media_matches = (
        media_actual["files"] == expected_media["files"]
        and media_actual["size_bytes"] == expected_media["size_bytes"]
    )
    ok = (
        database_actual["quick_check"] == ["ok"]
        and database_actual["foreign_key_error_count"] == 0
        and all(database_matches.values())
        and all(item["ok"] for item in artifact_matches)
        and media_actual["ok"]
        and media_matches
    )
    return {
        "ok": ok,
        "database": database_actual,
        "database_matches": database_matches,
        "artifact_matches": artifact_matches,
        "media": media_actual,
        "media_matches_report": media_matches,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Создаёт и проверяет read-only baseline CMS")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--database", type=Path, required=True)
    report_parser.add_argument("--media-dir", type=Path, required=True)
    report_parser.add_argument("--git-sha", default="")
    report_parser.add_argument("--tag", default="")
    report_parser.add_argument("--image-id", default="")
    report_parser.add_argument("--baseline-tag-sha", default="")
    report_parser.add_argument("--env-file", type=Path)
    report_parser.add_argument("--artifact", type=Path, action="append", default=[])
    report_parser.add_argument("--output", type=Path)

    manifest_parser = subparsers.add_parser("media-manifest")
    manifest_parser.add_argument("--media-dir", type=Path, required=True)
    manifest_parser.add_argument("--output", type=Path, required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--database", type=Path, required=True)
    verify_parser.add_argument("--media-dir", type=Path, required=True)
    verify_parser.add_argument("--report", type=Path, required=True)
    verify_parser.add_argument("--media-manifest", type=Path, required=True)
    verify_parser.add_argument("--output", type=Path)

    args = parser.parse_args()
    if args.command == "report":
        result = build_report(
            args.database,
            args.media_dir,
            git_sha=args.git_sha,
            tag=args.tag,
            image_id=args.image_id,
            baseline_tag_sha=args.baseline_tag_sha,
            env_file=args.env_file,
            artifacts=args.artifact,
        )
        write_json(result, args.output)
    elif args.command == "media-manifest":
        write_json(write_media_manifest(args.media_dir, args.output), None)
    else:
        result = verify_restore(args.database, args.media_dir, args.report, args.media_manifest)
        write_json(result, args.output)
        if not result["ok"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
