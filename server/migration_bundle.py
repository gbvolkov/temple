from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


BUNDLE_VERSION = "1.0.0"
REQUIRED_FILES = (
    "source/legacy-crawl-checkpoint.json",
    "source/legacy-inventory.json",
    "source/legacy-inventory.md",
    "source/legacy-media-manifest.json",
    "source/full-import-plan.json",
    "source/missing-legacy-media.csv",
    "source/missing-legacy-media.md",
    "source/current-sections.json",
)


class BundleError(RuntimeError):
    pass


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BundleError(f"Invalid JSON file: {path}") from error
    if not isinstance(value, dict):
        raise BundleError(f"Expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    pure = PurePosixPath(relative)
    if not relative or pure.is_absolute() or ".." in pure.parts:
        raise BundleError(f"Unsafe bundle path: {relative}")
    return relative


def _bundle_files(root: Path) -> list[Path]:
    excluded = {"bundle-manifest.json", "SHA256SUMS"}
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BundleError(f"Symlinks are forbidden in a migration bundle: {path}")
        if path.is_file() and _safe_relative(path, root) not in excluded:
            files.append(path)
    return files


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def build_manifest(root: Path, *, source_url: str, code_sha: str) -> dict:
    root = root.resolve()
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            raise BundleError(f"Required bundle file is missing: {relative}")

    parsed_source = urlsplit(source_url)
    if parsed_source.scheme not in {"http", "https"} or not parsed_source.netloc:
        raise BundleError("source_url must be an absolute HTTP(S) URL")

    crawl = _json(root / "source/legacy-crawl-checkpoint.json")
    inventory = _json(root / "source/legacy-inventory.json")
    media = _json(root / "source/legacy-media-manifest.json")
    plan = _json(root / "source/full-import-plan.json")
    missing_rows = _csv_rows(root / "source/missing-legacy-media.csv")

    if crawl.get("queue") or int(crawl.get("remaining_queue") or 0):
        raise BundleError("The source crawl is incomplete: queue is not empty")
    if crawl.get("failures"):
        raise BundleError("The source crawl contains transport/browser failures")
    if not isinstance(crawl.get("pages"), list) or not crawl["pages"]:
        raise BundleError("The source crawl contains no pages")
    if not isinstance(plan.get("records"), list) or not plan["records"]:
        raise BundleError("The import plan contains no records")

    mirrored_entries = {
        url: item
        for url, item in media.get("entries", {}).items()
        if isinstance(item, dict) and item.get("status") == "mirrored"
    }
    error_urls = {
        str(item.get("url") or "")
        for item in media.get("errors", [])
        if isinstance(item, dict) and item.get("url")
    }
    missing_urls = {str(item.get("url") or "") for item in missing_rows if item.get("url")}
    if error_urls != missing_urls:
        raise BundleError("Media manifest errors and missing-media report do not match")

    expected_media_paths = {
        f"media/legacy/{item['stored_path']}"
        for item in mirrored_entries.values()
        if isinstance(item.get("stored_path"), str)
    }
    actual_media_paths = {
        _safe_relative(path, root)
        for path in (root / "media/legacy").rglob("*")
        if path.is_file()
    }
    if expected_media_paths != actual_media_paths:
        raise BundleError("Mirrored media files do not match the media manifest")

    files = [
        {
            "path": _safe_relative(path, root),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in _bundle_files(root)
    ]
    status_counts: dict[str, int] = {}
    for page in crawl["pages"]:
        status = str(page.get("status", 0))
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "bundle_version": BUNDLE_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_url": source_url.rstrip("/") + "/",
        "code_sha": code_sha,
        "scope": {
            "public_pages": True,
            "public_media": True,
            "private_drafts": False,
            "source_users": False,
            "source_internal_history": False,
        },
        "counts": {
            "crawled_pages": len(crawl["pages"]),
            "crawl_statuses": dict(sorted(status_counts.items())),
            "inventory_pages": len(inventory.get("pages", [])),
            "planned_records": len(plan["records"]),
            "broken_pages": len(plan.get("broken", [])),
            "mirrored_urls": len(mirrored_entries),
            "unique_media_files": len(actual_media_paths),
            "missing_media_urls": len(error_urls),
        },
        "files": files,
    }


def create_bundle(root: Path, *, source_url: str, code_sha: str) -> dict:
    manifest = build_manifest(root, source_url=source_url, code_sha=code_sha)
    manifest_path = root / "bundle-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    checksum_lines = [
        f"{item['sha256']}  {item['path']}" for item in manifest["files"]
    ]
    checksum_lines.append(f"{_sha256(manifest_path)}  bundle-manifest.json")
    (root / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    return manifest


def verify_bundle(root: Path) -> dict:
    root = root.resolve()
    manifest = _json(root / "bundle-manifest.json")
    if manifest.get("bundle_version") != BUNDLE_VERSION:
        raise BundleError("Unsupported migration bundle version")

    expected = {item["path"]: item for item in manifest.get("files", [])}
    actual = {_safe_relative(path, root): path for path in _bundle_files(root)}
    if set(expected) != set(actual):
        raise BundleError("Bundle file list differs from bundle-manifest.json")
    for relative, item in expected.items():
        path = actual[relative]
        if path.stat().st_size != item.get("size_bytes") or _sha256(path) != item.get("sha256"):
            raise BundleError(f"Bundle checksum mismatch: {relative}")

    rebuilt = build_manifest(
        root,
        source_url=str(manifest.get("source_url") or ""),
        code_sha=str(manifest.get("code_sha") or ""),
    )
    if rebuilt["counts"] != manifest.get("counts") or rebuilt["scope"] != manifest.get("scope"):
        raise BundleError("Bundle semantic counts do not match its manifest")
    return {
        "ok": True,
        "bundle_version": BUNDLE_VERSION,
        "source_url": manifest["source_url"],
        "code_sha": manifest["code_sha"],
        "counts": manifest["counts"],
    }


def verify_archive(archive: Path, checksum: Path) -> dict:
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256(archive)
    if expected != actual:
        raise BundleError("Migration bundle archive checksum mismatch")
    with tarfile.open(archive, "r:gz") as bundle:
        roots: set[str] = set()
        for member in bundle.getmembers():
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts or member.issym() or member.islnk():
                raise BundleError(f"Unsafe archive member: {member.name}")
            if pure.parts:
                roots.add(pure.parts[0])
        if len(roots) != 1:
            raise BundleError("Migration archive must contain exactly one root directory")
    return {"ok": True, "sha256": actual, "root": roots.pop()}


def verify_database(database: Path, bundle_root: Path) -> dict:
    bundle = verify_bundle(bundle_root)
    plan = _json(bundle_root / "source/full-import-plan.json")
    expected_contents = len(plan["records"])
    expected_redirects = len({str(item["legacy_url"]) for item in plan["records"]})

    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        schema_version = connection.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
        ).fetchone()[0]
        contents = connection.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
        review_required = connection.execute(
            "SELECT COUNT(*) FROM contents WHERE migration_review_required=1"
        ).fetchone()[0]
        redirects = connection.execute("SELECT COUNT(*) FROM redirects").fetchone()[0]
        bad_status = connection.execute(
            "SELECT COUNT(*) FROM contents WHERE status!='draft'"
        ).fetchone()[0]
        users = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        non_admin_users = connection.execute(
            "SELECT COUNT(*) FROM users WHERE role!='admin'"
        ).fetchone()[0]
    finally:
        connection.close()

    problems = []
    if quick_check != "ok":
        problems.append("SQLite quick_check failed")
    if foreign_keys:
        problems.append("SQLite foreign_key_check failed")
    if schema_version != 15:
        problems.append(f"schema version is {schema_version}, expected 15")
    if contents != expected_contents:
        problems.append(f"contents={contents}, expected {expected_contents}")
    if review_required != expected_contents:
        problems.append(f"review_required={review_required}, expected {expected_contents}")
    if redirects != expected_redirects:
        problems.append(f"redirects={redirects}, expected {expected_redirects}")
    if bad_status:
        problems.append(f"{bad_status} imported contents are not drafts")
    if users > 1 or non_admin_users:
        problems.append("unexpected source users were imported")
    if problems:
        raise BundleError("; ".join(problems))
    return {
        "ok": True,
        "schema_version": schema_version,
        "contents": contents,
        "review_required": review_required,
        "redirects": redirects,
        "bootstrap_users": users,
        "source_users_imported": 0,
        "bundle": bundle,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Creates and verifies immutable public migration bundles")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--source-url", required=True)
    create.add_argument("--code-sha", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", type=Path, required=True)

    archive = subparsers.add_parser("verify-archive")
    archive.add_argument("--archive", type=Path, required=True)
    archive.add_argument("--checksum", type=Path, required=True)

    database = subparsers.add_parser("verify-database")
    database.add_argument("--database", type=Path, required=True)
    database.add_argument("--bundle-root", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create_bundle(args.root, source_url=args.source_url, code_sha=args.code_sha)
        elif args.command == "verify":
            result = verify_bundle(args.root)
        elif args.command == "verify-archive":
            result = verify_archive(args.archive, args.checksum)
        else:
            result = verify_database(args.database, args.bundle_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (BundleError, OSError, sqlite3.Error) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
