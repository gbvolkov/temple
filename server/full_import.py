from __future__ import annotations

import argparse
import json
import re
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import ROOT, Settings
from .db import init_database, slugify, transaction, utc_now
from .importer import canonical_target, fingerprint, media_mapping, rewrite_media_urls, upsert_record
from .legacy_crawl import atomic_json
from .legacy_inventory import classify_path
from .media_mirror import canonical_asset_url
from .public_urls import content_path, legacy_index_target


IMAGE_RE = re.compile(r"\.(?:jpe?g|png|webp|gif)(?:\?|$)", re.I)
BOILERPLATE = ("/assets/templates/", "telegram_cvet", "/assets/images/listok/")
MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
INDEX_PATHS = {
    "/", "/o-hrame/novosti-prihoda.html", "/o-hrame/anonsy.html", "/o-hrame/duhovenstvo.html",
    "/o-hrame/nebesnyy-pokrovitel.html", "/o-hrame/svyatyni-hrama.html",
    "/o-hrame/raspisanie-bogosluzheniy.html", "/o-hrame/fotogalereya.html",
    "/zhivoe-slovo/prihodskoy-listok.html", "/voskresnaya-shkola/obshhaya-informaciya.html",
    "/voskresnaya-shkola/zhizn.html", "/voskresnaya-shkola/raspisanie-zanyatiy.html",
    "/kontakty.html", "/требы-онлайн.html", "/прямая-трансляция-богослужений-из-храма.html",
}


def clean_title(page: dict, path: str) -> str:
    title = (page.get("title") or "").split("|", 1)[0].strip()
    if not title:
        title = next((value.strip() for value in page.get("headings", []) if value.strip()), "")
    if not title:
        title = Path(path).stem.replace("-", " ").strip().title()
    return title or "Материал старого сайта"


def content_images(page: dict, *, prefer_links: bool = False) -> list[dict]:
    linked = [
        {"image": value, "alt": "", "order": index + 1}
        for index, value in enumerate(page.get("outgoing", []))
        if isinstance(value, str) and IMAGE_RE.search(value) and not any(marker in value.lower() for marker in BOILERPLATE)
    ]
    rendered = [
        {"image": image.get("src", ""), "alt": image.get("alt", ""), "order": index + 1}
        for index, image in enumerate(page.get("images", []))
        if isinstance(image, dict)
        and image.get("src", "").startswith("https://")
        and IMAGE_RE.search(image.get("src", ""))
        and not any(marker in image.get("src", "").lower() for marker in BOILERPLATE)
    ]
    if prefer_links and linked:
        for index, item in enumerate(linked):
            if "/assets/images/" in item["image"].lower() and index < len(rendered):
                item["fallback"] = rendered[index]["image"]
        source = linked
    else:
        source = rendered
    unique: list[dict] = []
    seen: set[str] = set()
    for item in source:
        if item["image"] in seen:
            continue
        seen.add(item["image"])
        item["order"] = len(unique) + 1
        unique.append(item)
    return unique


def canonicalize_media_urls(value):
    if isinstance(value, str):
        return canonical_asset_url(value, {"www.sv-innokenty.ru", "sv-innokenty.ru"}) or value
    if isinstance(value, list):
        return [canonicalize_media_urls(item) for item in value]
    if isinstance(value, dict):
        return {key: canonicalize_media_urls(item) for key, item in value.items()}
    return value


def resolve_photo_fallbacks(data: dict) -> dict:
    for key in ("photos", "legacy_images"):
        for photo in data.get(key, []) if isinstance(data.get(key), list) else []:
            if not isinstance(photo, dict):
                continue
            image = photo.get("image", "")
            fallback = photo.get("fallback", "")
            if isinstance(image, str) and image.startswith("https://") and isinstance(fallback, str) and fallback.startswith("/media/"):
                photo["image"] = fallback
    photos = data.get("photos") or data.get("legacy_images") or []
    if data.get("cover", "").startswith("https://") and photos and isinstance(photos[0], dict) and photos[0].get("image", "").startswith("/media/"):
        data["cover"] = photos[0]["image"]
    return data


def prune_failed_media(value, failed_urls: set[str]):
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict) and item.get("image") in failed_urls:
                continue
            result.append(prune_failed_media(item, failed_urls))
        return result
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if isinstance(item, str) and item in failed_urls and key in {"image", "cover", "photo", "pdf", "url", "src", "href", "fallback"}:
                result[key] = ""
            else:
                result[key] = prune_failed_media(item, failed_urls)
        return result
    return "" if isinstance(value, str) and value in failed_urls else value


def infer_date(path: str, title: str) -> str:
    decoded = unquote(f"{path} {title}").lower()
    numeric = re.search(r"(?<!\d)(\d{1,2})[._-](\d{1,2})[._-](20\d{2})(?!\d)", decoded)
    if numeric:
        day, month, year = map(int, numeric.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    year_match = re.search(r"(?:19|20)\d{2}", decoded)
    year = int(year_match.group()) if year_match else 2000
    month = next((number for stem, number in MONTHS.items() if stem in decoded), 1)
    day_match = re.search(r"(?<!\d)([0-3]?\d)\s*(?:январ|феврал|март|апрел|мая|май|июн|июл|август|сентябр|октябр|ноябр|декабр)", decoded)
    day = int(day_match.group(1)) if day_match and 1 <= int(day_match.group(1)) <= 31 else 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def is_index(path: str, legacy_type: str) -> bool:
    if path in INDEX_PATHS:
        return True
    if legacy_type == "gallery_album":
        tail = path.split("fotogalereya/", 1)[-1]
        return bool(re.fullmatch(r"\d{4,5}(?:\.html)?/?", tail))
    return False


def target_type(path: str, legacy_type: str, images: list[dict]) -> str:
    if legacy_type == "contacts":
        return "site_contact"
    if legacy_type == "parish_life" and len([part for part in path.strip("/").split("/") if part]) == 2:
        return "parish_section"
    if is_index(path, legacy_type):
        return "page"
    if legacy_type in {"news", "announcement"}:
        return "news"
    if legacy_type == "gallery_album":
        return "gallery"
    if legacy_type == "clergy":
        return "clergy"
    if legacy_type in {"school", "parish_life"}:
        return "gallery" if len(images) >= 3 else "news"
    return "page"


def record_from_page(page: dict) -> dict | None:
    status = int(page.get("status") or 0)
    path = unquote(urlsplit(page.get("requested_url") or page["url"]).path) or "/"
    if status < 200 or status >= 400:
        return None
    legacy_type = classify_path(path, status)
    gallery_images = content_images(page, prefer_links=legacy_type in {"gallery_album", "school", "parish_life"})
    content_type = target_type(path, legacy_type, gallery_images)
    title = clean_title(page, path)
    raw_text = page.get("text", "")
    documents = page.get("documents", [])
    common = {
        "body_text": raw_text,
        "headings": page.get("headings", []),
        "legacy_images": gallery_images,
        "legacy_documents": documents,
        "new_path": canonical_target(path),
        "migration_note": "Импортировано полным read-only crawl; требуется редакторская очистка текста и проверка медиа.",
    }
    date = infer_date(path, title)
    if content_type == "news":
        data = {**common, "publication_date": date, "category": "Новости прихода", "summary": "", "body": [{"type": "legacy_text", "text": raw_text}], "cover": gallery_images[0]["image"] if gallery_images else "", "cover_alt": title}
    elif content_type == "gallery":
        data = {**common, "event_date": date, "category": "Воскресная школа" if "voskresnaya-shkola" in path else "Жизнь прихода" if "zhizn-prihoda" in path else "Богослужения", "summary": "", "cover": gallery_images[0]["image"] if gallery_images else "", "photos": gallery_images}
    elif content_type == "clergy":
        data = {**common, "full_name": title, "rank": "", "position": "", "photo": gallery_images[0]["image"] if gallery_images else "", "biography": [{"type": "legacy_text", "text": raw_text}]}
    elif content_type == "site_contact":
        data = {**common, "address": "Москва, Бескудниковский бульвар, 1", "phone": "+7 (499) 480-09-89", "email": "", "opening_hours": "", "map_coordinates": "", "legal_details": "", "social_links": []}
    elif content_type == "parish_section":
        data = {**common, "summary": "", "cover": gallery_images[0]["image"] if gallery_images else ""}
    else:
        data = {**common, "summary": "", "body": [{"type": "legacy_text", "text": raw_text}], "cover": gallery_images[0]["image"] if gallery_images else ""}
    return {
        "content_type": content_type,
        "slug": slugify(path.strip("/").replace("/", "-").removesuffix(".html") or "glavnaya"),
        "title": title,
        "legacy_url": path,
        "data": data,
    }


def build_full_plan(crawl: dict, mapping: dict[str, str] | None = None, failed_urls: set[str] | None = None) -> dict:
    mapping = mapping or {}
    failed_urls = failed_urls or set()
    records = []
    broken = []
    for page in crawl.get("pages", []):
        record = record_from_page(page)
        if record:
            record["data"] = canonicalize_media_urls(record["data"])
            if mapping:
                record["data"] = rewrite_media_urls(record["data"], mapping)
                record["data"] = resolve_photo_fallbacks(record["data"])
            if failed_urls:
                record["data"] = prune_failed_media(record["data"], failed_urls)
            records.append(record)
        else:
            broken.append({"url": page.get("requested_url") or page.get("url"), "status": page.get("status", 0)})
    return {
        "schema_version": "1.0.0",
        "source_fingerprint": "",
        "records": records,
        "broken": broken,
        "counts_by_type": dict(sorted(Counter(record["content_type"] for record in records).items())),
    }


def execute_plan(database: Path, source: Path, plan: dict, *, actor_id: str | None = None) -> dict:
    init_database(database)
    counters = Counter()
    started = utc_now()
    run_id = str(uuid.uuid4())
    with transaction(database) as connection:
        for record in plan["records"]:
            counters[upsert_record(connection, record, actor_id)] += 1
            legacy_type = classify_path(record["legacy_url"], 200)
            redirect_target = (
                legacy_index_target(record["legacy_url"])
                if is_index(record["legacy_url"], legacy_type)
                else content_path(record["content_type"], record["slug"])
            )
            connection.execute(
                "INSERT INTO redirects(old_path,new_path,status_code,created_at) VALUES(?,?,301,?) "
                "ON CONFLICT(old_path) DO UPDATE SET new_path=excluded.new_path",
                (record["legacy_url"], redirect_target, started),
            )
        report = {
            "source": source.name,
            "source_fingerprint": fingerprint(source),
            "records_found": len(plan["records"]),
            "broken": len(plan["broken"]),
            "imported": counters["imported"],
            "updated": counters["updated"],
            "skipped": counters["skipped"],
            "errors": 0,
            "run_id": run_id,
        }
        connection.execute(
            """INSERT INTO migration_runs(id,source_name,source_fingerprint,status,imported,updated,skipped,errors,
               report_json,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, source.name, report["source_fingerprint"], "completed", report["imported"], report["updated"], report["skipped"], 0, json.dumps(report, ensure_ascii=False), started, utc_now()),
        )
    return report


def main() -> None:
    settings = Settings.from_env()
    parser = argparse.ArgumentParser(description="Полный импорт Playwright-checkpoint старого сайта в черновики новой CMS")
    parser.add_argument("--source", type=Path, default=ROOT / "data" / "legacy-crawl-checkpoint.json")
    parser.add_argument("--database", type=Path, default=settings.database_path)
    parser.add_argument("--media-manifest", type=Path, default=ROOT / "data" / "legacy-media-manifest.json")
    parser.add_argument("--plan", type=Path, default=ROOT / "data" / "full-import-plan.json")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    crawl = json.loads(args.source.read_text(encoding="utf-8"))
    plan = build_full_plan(crawl)
    plan["source_fingerprint"] = fingerprint(args.source)
    atomic_json(args.plan, plan)
    manifest = json.loads(args.media_manifest.read_text(encoding="utf-8")) if args.media_manifest.exists() else {"errors": []}
    failed_urls = {item["url"] for item in manifest.get("errors", [])}
    localized_plan = build_full_plan(crawl, media_mapping(args.media_manifest), failed_urls) if args.execute else plan
    result = execute_plan(args.database, args.source, localized_plan) if args.execute else {
        "execute": False,
        "records_found": len(plan["records"]),
        "broken": len(plan["broken"]),
        "counts_by_type": plan["counts_by_type"],
        "plan": str(args.plan),
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
