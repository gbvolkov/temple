from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.baseline import database_report, media_summary


def coverage_check_count() -> int:
    coverage = json.loads((ROOT / "tests" / "e2e" / "coverage.json").read_text(encoding="utf-8"))
    return sum(len(suite["assertions"]) for suite in coverage["suites"])


def expected_profile_counts(profile: str) -> tuple[int, int]:
    coverage = json.loads((ROOT / "tests" / "e2e" / "coverage.json").read_text(encoding="utf-8"))
    defects = json.loads((ROOT / "tests" / "e2e" / "defects.json").read_text(encoding="utf-8"))["defects"]
    if profile == "smoke":
        return len(coverage["profiles"]["smoke"]), 0
    if profile == "source-data":
        source_defects = [item for item in defects if item["kind"] == "source-data" and item["status"] == "open"]
        return len(source_defects) + int(coverage["profiles"]["source_data_integrity_tests"]), len(source_defects)
    browser_defects = [item for item in defects if item["kind"] != "source-data" and item["status"] == "open"]
    positive_tests = len({suite["nodeid"] for suite in coverage["suites"]})
    return positive_tests + len(browser_defects), len(browser_defects)


def media_inventory_digest(media: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((item for item in media.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        stat = path.stat()
        record = f"{path.relative_to(media).as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}\n"
        digest.update(record.encode("utf-8"))
    return digest.hexdigest()


def stable_source_report(database: Path, media: Path) -> dict:
    report = database_report(database)
    return {
        "database": {
            "quick_check": report["quick_check"],
            "foreign_key_error_count": report["foreign_key_error_count"],
            "metrics": report["metrics"],
            "review_required": report["review_required"],
            "by_status": report["by_status"],
            "by_type": report["by_type"],
            "by_type_status": report["by_type_status"],
        },
        "media": {**media_summary(media), "inventory_sha256": media_inventory_digest(media)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the permanent CMS Playwright regression")
    parser.add_argument("--profile", choices=("smoke", "full", "source-data"), default="smoke")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--source-db", type=Path)
    parser.add_argument("--source-media", type=Path)
    parser.add_argument("--artifacts-dir", type=Path)
    parser.add_argument("--keep-workdir", action="store_true", help="Keep isolated pytest work directory")
    args = parser.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ")
    artifacts = (args.artifacts_dir or ROOT / "output" / "playwright" / "cms-regression" / stamp).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    workdir = (ROOT / "work" / "cms-e2e" / stamp).resolve()
    workdir.mkdir(parents=True, exist_ok=False)
    command = [
        sys.executable, "-m", "pytest", "tests/e2e", "-q",
        "--junitxml", str(artifacts / "junit.xml"),
        "--cms-artifacts-dir", str(artifacts),
        "--basetemp", str(workdir),
    ]
    source_before = None
    if args.profile == "smoke":
        command += ["-m", "smoke"]
    elif args.profile == "full":
        command += ["-m", "not source_data"]
    else:
        if not args.source_db or not args.source_media:
            parser.error("source-data requires --source-db and --source-media")
        source_db = args.source_db.resolve()
        source_media = args.source_media.resolve()
        source_before = stable_source_report(source_db, source_media)
        (artifacts / "source-before.json").write_text(
            json.dumps(source_before, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        command += [
            "-m", "source_data", "--cms-source-db", str(source_db),
            "--cms-source-media", str(source_media),
        ]
    if args.headed:
        command.append("--cms-headed")

    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=ROOT, check=False)
        source_unchanged = True
        if source_before is not None:
            source_after = stable_source_report(args.source_db.resolve(), args.source_media.resolve())
            (artifacts / "source-after.json").write_text(
                json.dumps(source_after, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            source_unchanged = source_after == source_before
        counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "xfail": 0}
        junit_path = artifacts / "junit.xml"
        if junit_path.exists():
            root = ET.parse(junit_path).getroot()
            suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
            for suite in suites:
                for key in ("tests", "failures", "errors", "skipped"):
                    counts[key] += int(suite.attrib.get(key, 0))
            counts["xfail"] = sum(
                1 for skipped in root.iter("skipped") if skipped.attrib.get("type") == "pytest.xfail"
            )
        expected_tests, expected_xfail = expected_profile_counts(args.profile)
        contract_ok = counts["tests"] == expected_tests and counts["xfail"] == expected_xfail
        summary = {
        "format_version": 1,
        "profile": args.profile,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "duration_seconds": round(time.monotonic() - started, 3),
        "pytest_exit_code": result.returncode,
        "source_unchanged": source_unchanged,
        "counts": counts,
        "expected": {"tests": expected_tests, "xfail": expected_xfail},
        "contract_ok": contract_ok,
        "coverage_checks": coverage_check_count() if args.profile == "full" else None,
        "artifacts": str(artifacts),
        "workdir": str(workdir) if args.keep_workdir else None,
        }
        (artifacts / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        if result.returncode:
            return result.returncode
        if not source_unchanged:
            return 2
        return 0 if contract_ok else 3
    finally:
        if not args.keep_workdir and workdir.exists():
            shutil.rmtree(workdir)


if __name__ == "__main__":
    raise SystemExit(main())
