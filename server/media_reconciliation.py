from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from .config import Settings
from .media_library import index_library, iter_media_references, stored_name_from_url


class MediaReconciliationError(RuntimeError):
    pass


def _safe_manifest_path(value: Any) -> str | None:
    raw = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or "." in path.parts:
        return None
    return path.as_posix()


def _physical_files(root: Path) -> set[str]:
    if not root.is_dir():
        return set()
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"entries": {}, "errors": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MediaReconciliationError("Manifest медиа повреждён или недоступен") from error
    if not isinstance(value, dict):
        raise MediaReconciliationError("Manifest медиа должен быть JSON-объектом")
    entries = value.get("entries", {})
    errors = value.get("errors", [])
    if not isinstance(entries, dict) or not isinstance(errors, list):
        raise MediaReconciliationError("Manifest медиа содержит неверную структуру")
    return value


def _load_missing_report(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            return [dict(row) for row in csv.DictReader(source)]
    except (OSError, csv.Error) as error:
        raise MediaReconciliationError("Отчёт отсутствующих медиа повреждён или недоступен") from error


def _sample(values: set[str] | list[str], limit: int = 10) -> list[str]:
    return sorted(values)[:limit]


def _content_reference_state(
    connection: sqlite3.Connection,
) -> tuple[
    set[tuple[str, str, int, str]],
    set[tuple[str, str, int, str]],
    set[str],
]:
    expected: set[tuple[str, str, int, str]] = set()
    invalid_payloads: set[str] = set()
    payloads = connection.execute(
        """SELECT id AS content_id,0 AS revision_version,data_json AS payload
           FROM contents
           UNION ALL
           SELECT content_id,version AS revision_version,snapshot_json AS payload
           FROM revisions"""
    )
    for row in payloads:
        marker = f"{row['content_id']}@{int(row['revision_version'])}"
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            invalid_payloads.add(marker)
            continue
        for url, field_path in iter_media_references(payload):
            stored_name = stored_name_from_url(url)
            if stored_name:
                expected.add(
                    (
                        stored_name,
                        str(row["content_id"]),
                        int(row["revision_version"]),
                        field_path,
                    )
                )

    actual = {
        (
            str(row["stored_name"]),
            str(row["content_id"]),
            int(row["revision_version"]),
            str(row["field_path"]),
        )
        for row in connection.execute(
            """SELECT media.stored_name,usage.content_id,usage.revision_version,usage.field_path
               FROM media_usages usage
               JOIN media ON media.id=usage.media_id"""
        )
    }
    return expected, actual, invalid_payloads


def _usage_sample(values: set[tuple[str, str, int, str]], limit: int = 10) -> list[str]:
    return [
        f"{content_id}@{revision_version}:{field_path} -> {stored_name}"
        for stored_name, content_id, revision_version, field_path in sorted(values)[:limit]
    ]


def reconcile_media(
    database_path: Path,
    media_dir: Path,
    manifest_path: Path,
    *,
    missing_report: Path | None = None,
    derivatives_dir: Path | None = None,
) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    report = _load_missing_report(missing_report)
    physical = _physical_files(media_dir)
    derivatives = _physical_files(derivatives_dir) if derivatives_dir else set()

    manifest_paths: list[str] = []
    unsafe_urls: list[str] = []
    mirrored_urls = 0
    for source_url, item in manifest.get("entries", {}).items():
        if not isinstance(item, dict) or item.get("status") != "mirrored":
            continue
        mirrored_urls += 1
        stored_path = _safe_manifest_path(item.get("stored_path"))
        if stored_path is None:
            unsafe_urls.append(str(source_url))
            continue
        manifest_paths.append(f"legacy/{stored_path}")

    path_counts = Counter(manifest_paths)
    unique_manifest_paths = set(path_counts)
    duplicate_groups = {path for path, amount in path_counts.items() if amount > 1}
    duplicate_aliases = sum(amount - 1 for amount in path_counts.values() if amount > 1)
    missing_manifest_files = unique_manifest_paths - physical
    unmanaged_files = physical - unique_manifest_paths

    manifest_error_rows = [
        str(item.get("url") or "").strip()
        for item in manifest.get("errors", [])
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]
    report_url_rows = [
        str(item.get("url") or "").strip()
        for item in report
        if str(item.get("url") or "").strip()
    ]
    manifest_error_urls = set(manifest_error_rows)
    report_urls = set(report_url_rows)
    report_manifest_mismatch = manifest_error_urls ^ report_urls

    connection = sqlite3.connect(
        database_path.resolve().as_uri() + "?mode=ro",
        uri=True,
        timeout=15,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        registered_rows = connection.execute("SELECT id,stored_name FROM media").fetchall()
        registered = {str(row["stored_name"]) for row in registered_rows}
        registered_ids = {str(row["id"]) for row in registered_rows}
        content_revisions = {
            (str(row["content_id"]), int(row["version"]))
            for row in connection.execute("SELECT content_id,version FROM revisions")
        }
        missing_issue_urls = {
            str(row["source_url"])
            for row in connection.execute("SELECT source_url FROM missing_media_issues")
        }
        invalid_files = int(
            connection.execute("SELECT COUNT(*) FROM media WHERE status!='ready'").fetchone()[0]
        )
        ready_missing_originals = {
            row["stored_name"]
            for row in connection.execute("SELECT stored_name FROM media WHERE status='ready'")
            if row["stored_name"] not in physical
        }
        pending_missing_issues = int(
            connection.execute(
                "SELECT COUNT(*) FROM missing_media_issues WHERE status='pending'"
            ).fetchone()[0]
        )
        linked_missing_issues = int(
            connection.execute(
                """SELECT COUNT(*) FROM missing_media_issues issue
                   WHERE EXISTS(
                     SELECT 1 FROM missing_media_issue_contents link
                     WHERE link.issue_id=issue.id
                   )"""
            ).fetchone()[0]
        )
        total_missing_issues = int(
            connection.execute("SELECT COUNT(*) FROM missing_media_issues").fetchone()[0]
        )
        expected_usages, actual_usages, invalid_content_payloads = _content_reference_state(
            connection
        )
    finally:
        connection.close()

    unregistered_files = physical - registered
    registered_without_file = registered - physical
    issue_report_mismatch = missing_issue_urls ^ report_urls
    unlinked_missing_issues = total_missing_issues - linked_missing_issues
    content_reference_files = {item[0] for item in expected_usages}
    content_unregistered_files = content_reference_files - registered
    content_missing_files = content_reference_files - physical
    expected_registered_usages = {
        item for item in expected_usages if item[0] in registered
    }
    missing_usage_rows = expected_registered_usages - actual_usages
    stale_usage_rows = actual_usages - expected_usages
    content_reference_issues = (
        len(content_unregistered_files)
        + len(content_missing_files)
        + len(missing_usage_rows)
        + len(stale_usage_rows)
        + len(invalid_content_payloads)
    )

    orphan_derivatives: set[str] = set()
    invalid_derivatives: set[str] = set()
    social_derivatives: set[str] = set()
    for stored_name in derivatives:
        parts = PurePosixPath(stored_name).parts
        if parts == ("social", "site-v1.jpg"):
            social_derivatives.add(stored_name)
            continue
        if len(parts) == 3 and parts[0] == "social" and re.fullmatch(r"[A-Za-z0-9-]{1,80}", parts[1]):
            version = re.fullmatch(r"v([1-9][0-9]*)\.jpg", parts[2])
            if version:
                if (parts[1], int(version.group(1))) in content_revisions:
                    social_derivatives.add(stored_name)
                else:
                    orphan_derivatives.add(stored_name)
                continue
        if len(parts) != 2 or parts[1] not in {"thumb.webp", "web.webp"}:
            invalid_derivatives.add(stored_name)
        elif parts[0] not in registered_ids:
            orphan_derivatives.add(stored_name)

    unexplained_count = (
        len(unsafe_urls)
        + len(missing_manifest_files)
        + len(unregistered_files)
        + len(ready_missing_originals)
        + len(report_manifest_mismatch)
        + len(issue_report_mismatch)
        + content_reference_issues
        + len(orphan_derivatives)
        + len(invalid_derivatives)
        + invalid_files
    )

    return {
        "status": "ok" if unexplained_count == 0 else "warning",
        "manifest_urls": mirrored_urls,
        "manifest_unique_files": len(unique_manifest_paths),
        "duplicate_aliases": duplicate_aliases,
        "duplicate_groups": len(duplicate_groups),
        "physical_files": len(physical),
        "derivative_files": len(derivatives),
        "social_derivative_files": len(social_derivatives),
        "orphan_derivative_files": len(orphan_derivatives),
        "invalid_derivative_files": len(invalid_derivatives),
        "missing_manifest_files": len(missing_manifest_files),
        "unmanaged_files": len(unmanaged_files),
        "registered_files": len(registered),
        "unregistered_files": len(unregistered_files),
        "registered_without_file": len(registered_without_file),
        "invalid_files": invalid_files,
        "manifest_errors": len(manifest_error_urls),
        "duplicate_manifest_error_rows": len(manifest_error_rows) - len(manifest_error_urls),
        "missing_report_rows": len(report_url_rows),
        "missing_report_unique_urls": len(report_urls),
        "duplicate_missing_report_rows": len(report_url_rows) - len(report_urls),
        "report_manifest_mismatch": len(report_manifest_mismatch),
        "missing_issues": total_missing_issues,
        "pending_missing_issues": pending_missing_issues,
        "linked_missing_issues": linked_missing_issues,
        "unlinked_missing_issues": unlinked_missing_issues,
        "issue_report_mismatch": len(issue_report_mismatch),
        "content_references": len(expected_usages),
        "content_reference_files": len(content_reference_files),
        "content_unregistered_files": len(content_unregistered_files),
        "content_missing_files": len(content_missing_files),
        "usage_rows": len(actual_usages),
        "missing_usage_rows": len(missing_usage_rows),
        "stale_usage_rows": len(stale_usage_rows),
        "invalid_content_payloads": len(invalid_content_payloads),
        "content_reference_issues": content_reference_issues,
        "unsafe_manifest_paths": len(unsafe_urls),
        "unexplained_count": unexplained_count,
        "samples": {
            "duplicate_files": _sample(duplicate_groups),
            "missing_manifest_files": _sample(missing_manifest_files),
            "unmanaged_files": _sample(unmanaged_files),
            "unregistered_files": _sample(unregistered_files),
            "registered_without_file": _sample(registered_without_file),
            "report_manifest_mismatch": _sample(report_manifest_mismatch),
            "issue_report_mismatch": _sample(issue_report_mismatch),
            "content_unregistered_files": _sample(content_unregistered_files),
            "content_missing_files": _sample(content_missing_files),
            "missing_usage_rows": _usage_sample(missing_usage_rows),
            "stale_usage_rows": _usage_sample(stale_usage_rows),
            "invalid_content_payloads": _sample(invalid_content_payloads),
            "orphan_derivative_files": _sample(orphan_derivatives),
            "invalid_derivative_files": _sample(invalid_derivatives),
            "unsafe_manifest_urls": _sample(unsafe_urls),
        },
    }


def run_reconciliation(settings: Settings, *, apply: bool = False) -> dict[str, Any]:
    report_path = settings.missing_report_path
    index_result = None
    if apply:
        index_result = index_library(
            settings.database_path,
            settings.media_dir,
            missing_report=report_path,
            dry_run=False,
        )
    result = reconcile_media(
        settings.database_path,
        settings.media_dir,
        settings.media_manifest_path,
        missing_report=report_path,
        derivatives_dir=settings.derivatives_dir,
    )
    return {"applied": apply, "index": index_result, "reconciliation": result}


def main() -> None:
    defaults = Settings.from_env()
    parser = argparse.ArgumentParser(
        description="Сверяет manifest, физическую медиатеку, реестр CMS и missing-media отчёт"
    )
    parser.add_argument("--database", type=Path, default=defaults.database_path)
    parser.add_argument("--media", type=Path, default=defaults.media_dir)
    parser.add_argument("--manifest", type=Path, default=defaults.media_manifest_path)
    parser.add_argument(
        "--missing-report",
        type=Path,
        default=defaults.missing_report_path,
    )
    parser.add_argument("--derivatives", type=Path, default=defaults.derivatives_dir)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Идемпотентно индексирует физические файлы и импортирует missing-media issues",
    )
    args = parser.parse_args()
    settings = Settings(
        **{
            **defaults.__dict__,
            "database_path": args.database,
            "media_dir": args.media,
            "media_manifest_path": args.manifest,
            "media_derivatives_dir": args.derivatives,
            "missing_media_report_path": args.missing_report,
        }
    )
    result = run_reconciliation(settings, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
