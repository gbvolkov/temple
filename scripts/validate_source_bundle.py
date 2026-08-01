from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from server.importer import build_records
from server.media_mirror import collect_assets


MANIFEST_NAME = "source-bundle-manifest.json"
REQUIRED_FILES = {
    "legacy-crawl-checkpoint.json",
    "legacy-inventory.json",
    "legacy-inventory.md",
    "legacy-media-manifest.json",
    "full-import-plan.json",
    "missing-legacy-media.csv",
    "missing-legacy-media.md",
    "current-sections.json",
    "structured-sections.json",
    "structured-import-dry-run.json",
    "known-public-routes.json",
}


class BundleError(RuntimeError):
    pass


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BundleError(f"Invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise BundleError(f"Expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BundleError(f"Symlinks are forbidden in a source bundle: {path}")
        if path.is_file() and path.name != MANIFEST_NAME:
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise BundleError(f"Unsafe bundle path: {value!r}")
    return path.as_posix()


def _source_summary(root: Path, source_url: str, git_sha: str) -> dict:
    missing = sorted(name for name in REQUIRED_FILES if not (root / name).is_file())
    if missing:
        raise BundleError("Required bundle files are missing: " + ", ".join(missing))
    unexpected_scope_files = [
        path.relative_to(root).as_posix()
        for path in _relative_files(root)
        if path.relative_to(root).as_posix() not in REQUIRED_FILES
        and not path.relative_to(root).as_posix().startswith("media/legacy/")
    ]
    if unexpected_scope_files:
        raise BundleError(
            "Source bundle contains files outside the public migration scope: "
            + ", ".join(unexpected_scope_files)
        )

    crawl = _json(root / "legacy-crawl-checkpoint.json")
    plan = _json(root / "full-import-plan.json")
    media = _json(root / "legacy-media-manifest.json")
    inventory = _json(root / "legacy-inventory.json")
    known_routes = _json(root / "known-public-routes.json")
    try:
        structured_sections = json.loads((root / "structured-sections.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BundleError("Invalid structured-sections.json") from error
    if not isinstance(structured_sections, list) or not structured_sections:
        raise BundleError("Structured source contains no sections")
    structured_records, rejected_records = build_records(structured_sections)
    if rejected_records:
        raise BundleError(f"Structured source has {len(rejected_records)} rejected records")
    try:
        route_template = json.loads((root / "current-sections.json").read_text(encoding="utf-8"))
        structured_dry_run = json.loads((root / "structured-import-dry-run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BundleError("Invalid structured source evidence") from error
    if not isinstance(route_template, list) or not isinstance(structured_dry_run, dict):
        raise BundleError("Structured source evidence has an invalid shape")
    route_paths = {str(item.get("path", "")) for item in route_template if isinstance(item, dict)}
    structured_paths = {str(item.get("path", "")) for item in structured_sections if isinstance(item, dict)}
    if not route_paths or route_paths != structured_paths:
        raise BundleError("Fresh structured routes do not match the required route template")
    if (
        structured_dry_run.get("dry_run") is not True
        or int(structured_dry_run.get("errors", -1)) != 0
        or int(structured_dry_run.get("records_found", -1)) != len(structured_records)
    ):
        raise BundleError("Structured import dry-run does not match the fresh structured source")

    pages = crawl.get("pages")
    failures = crawl.get("failures")
    queue = crawl.get("queue")
    blocked_write_requests = crawl.get("blocked_write_requests", [])
    records = plan.get("records")
    broken = plan.get("broken")
    entries = media.get("entries")
    media_errors = media.get("errors")
    if not isinstance(pages, list) or not pages:
        raise BundleError("Crawl contains no pages")
    if not isinstance(failures, list) or failures:
        raise BundleError(f"Crawl has {len(failures) if isinstance(failures, list) else 'invalid'} failures")
    if not isinstance(queue, list) or queue or int(crawl.get("remaining_queue", 0)) != 0:
        raise BundleError("Crawl queue is not fully drained")
    if not isinstance(blocked_write_requests, list):
        raise BundleError("Crawl blocked_write_requests has an invalid shape")
    if any(
        not isinstance(item, dict) or str(item.get("method", "")).upper() in {"GET", "HEAD", "OPTIONS"}
        for item in blocked_write_requests
    ):
        raise BundleError("Crawl blocked_write_requests contains an invalid record")
    if not isinstance(records, list) or not records:
        raise BundleError("Import plan contains no records")
    if not isinstance(broken, list):
        raise BundleError("Import plan has an invalid broken list")
    if not isinstance(entries, dict) or not isinstance(media_errors, list):
        raise BundleError("Media manifest has an invalid shape")
    non_permanent_page_failures = [
        str(page.get("requested_url") or page.get("url") or "")
        for page in pages
        if isinstance(page, dict)
        and not (
            200 <= int(page.get("status") or 0) < 400
            or int(page.get("status") or 0) in {404, 410}
        )
    ]
    if non_permanent_page_failures:
        raise BundleError(
            "Crawl contains non-permanent HTTP failures: "
            + ", ".join(non_permanent_page_failures[:20])
        )

    discovered_asset_urls = {item["url"] for item in collect_assets(crawl)}
    media_error_urls = {
        str(item.get("url", ""))
        for item in media_errors
        if isinstance(item, dict) and item.get("url")
    }
    if len(media_error_urls) != len(media_errors):
        raise BundleError("Media manifest errors contain missing or duplicate URLs")
    non_permanent_media_errors = [
        str(item.get("url", ""))
        for item in media_errors
        if not re.search(r"\bHTTP Error (?:404|410)\b", str(item.get("error", "")))
    ]
    if non_permanent_media_errors:
        raise BundleError(
            "Media mirror contains transient, access, size, or transport failures: "
            + ", ".join(non_permanent_media_errors[:20])
        )
    if discovered_asset_urls != set(entries) | media_error_urls:
        raise BundleError("Media manifest does not cover every asset discovered by the fresh crawl")

    known_pages = known_routes.get("pages")
    if not isinstance(known_pages, list) or not known_pages:
        raise BundleError("Known public route inventory is empty")
    known_paths = {
        unquote(urlsplit(str(item.get("url") or item.get("path") or "")).path or "/")
        for item in known_pages
        if isinstance(item, dict)
    }
    visited_paths = {
        unquote(urlsplit(str(page.get("requested_url") or page.get("url") or "")).path or "/")
        for page in pages
        if isinstance(page, dict)
    }
    if not known_paths.issubset(visited_paths):
        missing_known = sorted(known_paths - visited_paths)
        raise BundleError(f"Fresh crawl omitted {len(missing_known)} known public routes")

    expected_source = source_url.rstrip("/") + "/"
    actual_source = str(crawl.get("base_url", "")).rstrip("/") + "/"
    if actual_source != expected_source:
        raise BundleError(f"Crawl source mismatch: expected {expected_source}, received {actual_source}")
    parsed = urlsplit(actual_source)
    if parsed.scheme != "https" or not parsed.hostname:
        raise BundleError("Source URL must be an absolute HTTPS URL")

    mirrored_paths: set[str] = set()
    for url, entry in entries.items():
        if not isinstance(entry, dict) or entry.get("status") != "mirrored":
            raise BundleError(f"Media entry is not mirrored: {url}")
        stored = _safe_relative(str(entry.get("stored_path", "")))
        mirrored_paths.add(stored)
        target = root / "media" / "legacy" / Path(stored)
        if not target.is_file():
            raise BundleError(f"Mirrored file is missing: {stored}")
        expected_hash = str(entry.get("sha256", "")).lower()
        if len(expected_hash) != 64 or _sha256(target) != expected_hash:
            raise BundleError(f"Mirrored file checksum mismatch: {stored}")

    physical_paths = {
        path.relative_to(root / "media" / "legacy").as_posix()
        for path in (root / "media" / "legacy").rglob("*")
        if path.is_file()
    }
    if physical_paths != mirrored_paths:
        raise BundleError("Physical media and manifest paths do not match")

    with (root / "missing-legacy-media.csv").open(encoding="utf-8-sig", newline="") as source:
        missing_rows = sum(1 for _ in csv.DictReader(source))
    if missing_rows != len(media_error_urls):
        raise BundleError("Missing-media report does not match media source errors")

    statuses = Counter(str(page.get("status", 0)) for page in pages if isinstance(page, dict))
    crawl_legacy_urls = {
        str(record.get("legacy_url"))
        for record in records
        if isinstance(record, dict) and record.get("legacy_url")
    }
    structured_legacy_urls = {
        str(record.get("legacy_url"))
        for record in structured_records
        if isinstance(record, dict) and record.get("legacy_url")
    }
    all_legacy_urls = crawl_legacy_urls | structured_legacy_urls
    if len(crawl_legacy_urls) != len(records) or len(structured_legacy_urls) != len(structured_records):
        raise BundleError("Import sources contain missing or duplicate legacy URLs")
    successful_crawl_paths = {
        unquote(urlsplit(str(page.get("requested_url") or page.get("url") or "")).path) or "/"
        for page in pages
        if isinstance(page, dict) and 200 <= int(page.get("status") or 0) < 400
    }
    broken_crawl_urls = {
        str(page.get("requested_url") or page.get("url") or "")
        for page in pages
        if isinstance(page, dict) and not 200 <= int(page.get("status") or 0) < 400
    }
    plan_broken_urls = {
        str(item.get("url", ""))
        for item in broken
        if isinstance(item, dict) and item.get("url")
    }
    if successful_crawl_paths != crawl_legacy_urls:
        raise BundleError("Full import plan does not cover every successful crawl page")
    if broken_crawl_urls != plan_broken_urls:
        raise BundleError("Full import plan broken list does not match unsuccessful crawl pages")
    if not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("source_fingerprint", ""))):
        raise BundleError("Full import plan source fingerprint is invalid")
    redirect_urls = crawl_legacy_urls | {url for url in structured_legacy_urls if url.startswith("/")}
    return {
        "format_version": 1,
        "scope": "public-pages-and-discovered-media",
        "excluded": ["private-drafts", "source-users", "source-internal-history"],
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_url": actual_source,
        "source_host": parsed.hostname.lower(),
        "git_sha": git_sha,
        "crawl": {
            "pages": len(pages),
            "known_seed_routes": len(known_paths),
            "failures": 0,
            "remaining_queue": 0,
            "blocked_write_requests": len(blocked_write_requests),
            "statuses": dict(sorted(statuses.items())),
        },
        "import": {
            "records": len(all_legacy_urls),
            "crawl_records": len(records),
            "structured_records": len(structured_records),
            "broken": len(broken),
            "unique_legacy_urls": len(all_legacy_urls),
            "redirect_urls": len(redirect_urls),
            "counts_by_type": plan.get("counts_by_type", {}),
            "source_fingerprint": plan.get("source_fingerprint", ""),
        },
        "media": {
            "manifest_urls": len(entries),
            "unique_files": len(mirrored_paths),
            "source_errors": len(media_errors),
            "missing_report_rows": missing_rows,
        },
        "inventory": {
            "pages": len(inventory.get("pages", [])),
            "coverage": inventory.get("coverage", {}),
        },
    }


def create_manifest(root: Path, source_url: str, git_sha: str) -> dict:
    root = root.resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", git_sha):
        raise BundleError("git_sha must be a full lowercase Git commit SHA")
    report = _source_summary(root, source_url, git_sha)
    report["files"] = {
        path.relative_to(root).as_posix(): {
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for path in _relative_files(root)
    }
    output = root / MANIFEST_NAME
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def verify_bundle(root: Path) -> dict:
    root = root.resolve()
    manifest = _json(root / MANIFEST_NAME)
    expected_files = manifest.get("files")
    if not isinstance(expected_files, dict):
        raise BundleError("Bundle manifest has no file inventory")
    actual_files = {path.relative_to(root).as_posix(): path for path in _relative_files(root)}
    if set(expected_files) != set(actual_files):
        missing = sorted(set(expected_files) - set(actual_files))
        unexpected = sorted(set(actual_files) - set(expected_files))
        raise BundleError(f"Bundle file inventory mismatch; missing={missing}, unexpected={unexpected}")
    for relative, expected in expected_files.items():
        _safe_relative(relative)
        path = actual_files[relative]
        if not isinstance(expected, dict):
            raise BundleError(f"Invalid file entry: {relative}")
        if path.stat().st_size != int(expected.get("size_bytes", -1)):
            raise BundleError(f"Bundle file size mismatch: {relative}")
        if _sha256(path) != expected.get("sha256"):
            raise BundleError(f"Bundle file checksum mismatch: {relative}")
    fresh = _source_summary(root, str(manifest.get("source_url", "")), str(manifest.get("git_sha", "")))
    for section in ("scope", "excluded", "source_url", "source_host", "git_sha", "crawl", "import", "media", "inventory"):
        if fresh.get(section) != manifest.get(section):
            raise BundleError(f"Bundle semantic summary mismatch: {section}")
    return {"ok": True, **{key: manifest[key] for key in ("source_url", "git_sha", "crawl", "import", "media")}}


def verify_database(database: Path, root: Path, *, post_activation: bool = False) -> dict:
    bundle = verify_bundle(root)
    manifest = _json(root / MANIFEST_NAME)
    uri = database.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        schema = connection.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
        contents = connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
        redirects = connection.execute("SELECT COUNT(*) FROM redirects").fetchone()[0]
        review = connection.execute("SELECT COUNT(*) FROM contents WHERE migration_review_required=1").fetchone()[0]
        non_drafts = connection.execute("SELECT COUNT(*) FROM contents WHERE status!='draft'").fetchone()[0]
        revisions = connection.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
        users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        non_admin_users = connection.execute("SELECT COUNT(*) FROM users WHERE role!='admin'").fetchone()[0]
        source_operational_rows = sum(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("sessions", "user_events", "submissions", "submission_events", "notification_outbox")
        )
        completed_imports = connection.execute("SELECT COUNT(*) FROM migration_runs WHERE status='completed' AND errors=0").fetchone()[0]
        missing_issues = connection.execute("SELECT COUNT(*) FROM missing_media_issues").fetchone()[0]
    expected = manifest["import"]
    expected_missing = manifest["media"]["missing_report_rows"]
    checks = {
        "quick_check": quick == "ok",
        "foreign_key_check": not foreign_keys,
        "schema_13": schema == 13,
        "contents": contents == expected["records"],
        "redirects": redirects == expected["redirect_urls"],
        "review_flags": review == contents,
        "draft_only": non_drafts == 0,
        "revisions": revisions >= contents,
        "completed_import": completed_imports >= 1,
        "missing_media_issues": missing_issues == expected_missing,
    }
    if not post_activation:
        checks.update({
            "source_users_absent": users == 0 and non_admin_users == 0,
            "source_operational_history_absent": source_operational_rows == 0,
        })
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise BundleError("Database verification failed: " + ", ".join(failed))
    return {
        "ok": True,
        "checks": checks,
        "schema_version": schema,
        "contents": contents,
        "redirects": redirects,
        "review_required": review,
        "revisions": revisions,
        "target_users": users,
        "source_operational_rows": source_operational_rows,
        "pre_activation_scope_checked": not post_activation,
        "missing_media_issues": missing_issues,
        "bundle": bundle,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and verify immutable public-source migration bundles")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("directory", type=Path)
    create.add_argument("--source-url", required=True)
    create.add_argument("--git-sha", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("directory", type=Path)
    database = subparsers.add_parser("verify-database")
    database.add_argument("directory", type=Path)
    database.add_argument("--database", type=Path, required=True)
    database.add_argument("--post-activation", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create_manifest(args.directory, args.source_url, args.git_sha)
        elif args.command == "verify":
            result = verify_bundle(args.directory)
        else:
            result = verify_database(args.database, args.directory, post_activation=args.post_activation)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except BundleError as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
