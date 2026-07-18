from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings
from .db import init_database, row_to_content, slugify, transaction, utc_now
from .workflow import record_audit
from .public_urls import legacy_redirect_target


MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "май": 5, "мая": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_target(old_path: str) -> str:
    explicit = {
        "/o-hrame/raspisanie-bogosluzheniy.html": "/bogosluzheniya/",
        "/o-hrame/fotogalereya.html": "/fotogalereya/",
        "/zhivoe-slovo/prihodskoy-listok.html": "/innokentievskiy-listok/",
        "/прямая-трансляция-богослужений-из-храма.html": "/translyatsiya/",
        "/требы-онлайн.html": "/treby-online/",
        "/kontakty.html": "/kontakty/",
    }
    if old_path in explicit:
        return explicit[old_path]
    if old_path.endswith(".html"):
        return old_path[:-5].rstrip("/") + "/"
    return old_path


def page_type(path: str) -> str:
    if path.endswith("duhovenstvo.html"):
        return "clergy"
    if path.startswith("/zhizn-prihoda/") or path.startswith("/voskresnaya-shkola/"):
        return "parish_section"
    return "page"


def clean_title(value: str) -> str:
    return value.split("|", 1)[0].strip() or "Материал старого сайта"


def page_record(section: dict) -> dict:
    old_path = section["path"]
    title = clean_title(section.get("title", ""))
    target = canonical_target(old_path)
    return {
        "content_type": page_type(old_path),
        "slug": slugify(target.strip("/").replace("/", "-")) or slugify(title),
        "title": title,
        "legacy_url": old_path,
        "data": {
            "summary": "",
            "body_text": section.get("text", ""),
            "headings": section.get("headings", []),
            "legacy_images": section.get("images", []),
            "legacy_documents": section.get("documents", []),
            "new_path": target,
            "migration_note": "Импортировано из read-only снимка; требуется редакторская очистка текста и проверка медиа.",
        },
    }


def document_number(text: str) -> int | None:
    explicit = re.search(r"№\s*(\d{1,3})", text)
    if explicit:
        return int(explicit.group(1))
    leading = re.match(r"^\s*(\d{1,3})(?:\s|$)", text)
    return int(leading.group(1)) if leading and int(leading.group(1)) <= 199 else None


def document_date(text: str, href: str) -> tuple[str, int]:
    source = f"{text} {href}".lower()
    years = re.findall(r"(?:19|20)\d{2}", source)
    year = int(years[-1]) if years else 2006
    month = next((number for stem, number in MONTHS.items() if stem in source), 1)
    return f"{year:04d}-{month:02d}-01", year


def leaflet_records(documents: list[dict]) -> tuple[list[dict], list[dict]]:
    grouped: dict[int, list[dict]] = defaultdict(list)
    special: list[dict] = []
    for document in documents:
        number = document_number(document.get("text", ""))
        if number is None:
            special.append(document)
        else:
            grouped[number].append(document)

    records: list[dict] = []
    for number, group in sorted(grouped.items(), reverse=True):
        primary = next((item for item in group if "прилож" not in item.get("text", "").lower()), group[0])
        attachments = [item["href"] for item in group if item is not primary]
        publication_date, year = document_date(primary.get("text", ""), primary.get("href", ""))
        period = re.sub(r"^\s*(?:№\s*)?\d+\s*", "", primary.get("text", "")).strip()
        records.append({
            "content_type": "leaflet_issue",
            "slug": f"innokentievskiy-listok-{number}",
            "title": f"Иннокентиевский листок №{number}",
            "legacy_id": str(number),
            "legacy_url": primary["href"],
            "data": {
                "number": number,
                "period": period or str(year),
                "publication_date": publication_date,
                "year": year,
                "cover": "assets/leaflet-148.jpg" if number == 148 else "",
                "pdf": primary["href"],
                "attachments": attachments,
                "featured": number == max(grouped),
                "migration_note": "PDF оставлен по исходному read-only URL; обложку и локальную копию файла нужно проверить перед публикацией.",
            },
        })
    for document in special:
        publication_date, year = document_date(document.get("text", ""), document.get("href", ""))
        records.append({
            "content_type": "page",
            "slug": "innokentievskiy-listok-" + slugify(document.get("text", "special")),
            "title": "Иннокентиевский листок — " + document.get("text", "специальный выпуск").title(),
            "legacy_url": document["href"],
            "data": {
                "body": [{"type": "file", "url": document["href"], "label": document.get("text", "PDF")}],
                "publication_date": publication_date,
                "year": year,
                "migration_note": "Специальный выпуск без номера сохранён отдельным черновиком.",
            },
        })
    return records, []


def build_records(sections: list[dict]) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    rejected: list[dict] = []
    for section in sections:
        records.append(page_record(section))
        if "prihodskoy-listok" in section.get("path", ""):
            issues, bad = leaflet_records(section.get("documents", []))
            records.extend(issues)
            rejected.extend(bad)
    return records, rejected


def media_mapping(manifest_path: Path | None) -> dict[str, str]:
    if not manifest_path or not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        url: entry["local_url"]
        for url, entry in manifest.get("entries", {}).items()
        if entry.get("status") == "mirrored" and entry.get("local_url")
    }


def rewrite_media_urls(value, mapping: dict[str, str]):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [rewrite_media_urls(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: rewrite_media_urls(item, mapping) for key, item in value.items()}
    return value


def canonicalize_legacy_assets(value):
    from .media_mirror import canonical_asset_url

    if isinstance(value, str):
        return canonical_asset_url(value, {"www.sv-innokenty.ru", "sv-innokenty.ru"}) or value
    if isinstance(value, list):
        return [canonicalize_legacy_assets(item) for item in value]
    if isinstance(value, dict):
        return {key: canonicalize_legacy_assets(item) for key, item in value.items()}
    return value


def unique_slug(connection: sqlite3.Connection, desired: str, legacy_url: str) -> str:
    existing = connection.execute("SELECT slug FROM contents WHERE legacy_url = ?", (legacy_url,)).fetchone()
    if existing:
        return existing["slug"]
    candidate = desired
    suffix = 2
    while connection.execute("SELECT 1 FROM contents WHERE slug = ?", (candidate,)).fetchone():
        candidate = f"{desired}-{suffix}"
        suffix += 1
    return candidate


def upsert_record(connection: sqlite3.Connection, record: dict, actor_id: str | None = None) -> str:
    now = utc_now()
    old = connection.execute("SELECT * FROM contents WHERE legacy_url = ?", (record["legacy_url"],)).fetchone()
    payload = json.dumps(record["data"], ensure_ascii=False, sort_keys=True)
    if old and not old["migration_review_required"]:
        return "skipped"
    if old and old["content_type"] == record["content_type"] and old["title"] == record["title"] and old["data_json"] == payload:
        return "skipped"
    if old:
        version = old["version"] + 1
        connection.execute(
            "UPDATE contents SET content_type=?, title=?, data_json=?, migration_review_required=1, version=?, updated_at=? WHERE id=?",
            (record["content_type"], record["title"], payload, version, now, old["id"]),
        )
        snapshot = dict(row_to_content(connection.execute("SELECT * FROM contents WHERE id=?", (old["id"],)).fetchone()))
        connection.execute(
            "INSERT INTO revisions(content_id, version, snapshot_json, actor_id, created_at) VALUES(?,?,?,?,?)",
            (old["id"], version, json.dumps(snapshot, ensure_ascii=False), None, now),
        )
        after = connection.execute("SELECT * FROM contents WHERE id=?", (old["id"],)).fetchone()
        record_audit(
            connection, content_id=old["id"], actor_id=actor_id, action="import_update",
            before=old, after=after, details={"source": "legacy_import"},
        )
        return "updated"

    content_id = str(uuid.uuid4())
    slug = unique_slug(connection, record["slug"], record["legacy_url"])
    connection.execute(
        """INSERT INTO contents(id,content_type,slug,title,status,data_json,legacy_id,legacy_url,
           migration_review_required,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,1,1,?,?)""",
        (content_id, record["content_type"], slug, record["title"], "draft", payload,
         record.get("legacy_id"), record["legacy_url"], now, now),
    )
    snapshot = row_to_content(connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone())
    connection.execute(
        "INSERT INTO revisions(content_id, version, snapshot_json, actor_id, created_at) VALUES(?,?,?,?,?)",
        (content_id, 1, json.dumps(snapshot, ensure_ascii=False), None, now),
    )
    after = connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
    record_audit(
        connection, content_id=content_id, actor_id=actor_id, action="import_create",
        before=None, after=after, details={"source": "legacy_import"},
    )
    return "imported"


def run_import(
    database: Path,
    source: Path,
    *,
    dry_run: bool = False,
    media_manifest: Path | None = None,
    leaflets_only: bool = False,
    actor_id: str | None = None,
) -> dict:
    sections = json.loads(source.read_text(encoding="utf-8"))
    records, rejected = build_records(sections)
    if leaflets_only:
        records = [record for record in records if record["legacy_url"].startswith("http")]
    mapping = media_mapping(media_manifest)
    for record in records:
        record["data"] = canonicalize_legacy_assets(record["data"])
        if mapping:
            record["data"] = rewrite_media_urls(record["data"], mapping)
    report = {
        "source": source.name,
        "source_fingerprint": fingerprint(source),
        "records_found": len(records),
        "imported": 0,
        "updated": 0,
        "skipped": 0,
        "errors": len(rejected),
        "rejected_documents": rejected,
        "dry_run": dry_run,
        "mirrored_urls_available": len(mapping),
    }
    if dry_run:
        report["sample"] = records[:3]
        return report

    init_database(database)
    run_id = str(uuid.uuid4())
    started_at = utc_now()
    with transaction(database) as connection:
        for record in records:
            result = upsert_record(connection, record, actor_id)
            report[result] += 1
            if record["legacy_url"].startswith("/"):
                connection.execute(
                    "INSERT INTO redirects(old_path,new_path,status_code,created_at) VALUES(?,?,301,?) "
                    "ON CONFLICT(old_path) DO UPDATE SET new_path=excluded.new_path",
                    (
                        record["legacy_url"],
                        legacy_redirect_target(record["legacy_url"], record["content_type"], record["slug"]),
                        started_at,
                    ),
                )
        connection.execute(
            """INSERT INTO migration_runs(id,source_name,source_fingerprint,status,imported,updated,skipped,errors,
               report_json,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, source.name, report["source_fingerprint"], "completed", report["imported"], report["updated"],
             report["skipped"], report["errors"], json.dumps(report, ensure_ascii=False), started_at, utc_now()),
        )
    report["run_id"] = run_id
    return report


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Безопасный импорт read-only снимка старого сайта")
    parser.add_argument("--source", type=Path, default=settings.legacy_sections_path)
    parser.add_argument("--database", type=Path, default=settings.database_path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--media-manifest", type=Path, default=settings.root / "data" / "legacy-media-manifest.json")
    parser.add_argument("--leaflets-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_import(args.database, args.source, dry_run=args.dry_run, media_manifest=args.media_manifest, leaflets_only=args.leaflets_only), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
