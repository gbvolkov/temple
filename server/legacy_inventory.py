from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import ROOT


TYPE_TO_CMS = {
    "home": "page",
    "news": "news",
    "announcement": "news",
    "clergy": "clergy",
    "saint": "page",
    "shrine": "page",
    "schedule": "service/page",
    "gallery_index": "page",
    "gallery_album": "gallery",
    "leaflet_index": "page",
    "school": "parish_section/page/gallery",
    "parish_life": "parish_section/news/gallery",
    "stream": "video/page",
    "online_request": "page/form",
    "contacts": "site_contact",
    "page": "page",
    "broken": "redirect/review",
}


def classify_path(path: str, status: int) -> str:
    decoded = unquote(path).lower().rstrip("/") or "/"
    if status >= 400:
        return "broken"
    if decoded == "/":
        return "home"
    if "novosti-prihoda" in decoded:
        return "news"
    if "/anons" in decoded:
        return "announcement"
    if "duhovenstvo" in decoded:
        return "clergy"
    if "nebesnyy-pokrovitel" in decoded:
        return "saint"
    if "svyatyni-hrama" in decoded:
        return "shrine"
    if "raspisanie-bogosluzheniy" in decoded:
        return "schedule"
    if "fotogalereya" in decoded:
        tail = decoded.split("fotogalereya", 1)[1].strip("/")
        return "gallery_album" if tail else "gallery_index"
    if "prihodskoy-listok" in decoded:
        return "leaflet_index"
    if decoded.startswith("/voskresnaya-shkola"):
        return "school"
    if decoded.startswith("/zhizn-prihoda"):
        return "parish_life"
    if "трансляция" in decoded:
        return "stream"
    if "требы-онлайн" in decoded:
        return "online_request"
    if decoded == "/kontakty.html":
        return "contacts"
    return "page"


def suspicious_path(path: str) -> bool:
    decoded = unquote(path).lower()
    repeated = ("/o-hrame/o-hrame/", "/o-hrame/zhivoe-slovo/", "/o-hrame/voskresnaya-shkola/", "/fotogalereya/o-hrame/")
    return any(pattern in decoded for pattern in repeated)


def build_inventory(crawl: dict, detailed_sections: list[dict]) -> dict:
    detailed_by_path = {section["path"]: section for section in detailed_sections}
    pages = []
    for source in crawl.get("pages", []):
        path = unquote(urlsplit(source["url"]).path) or "/"
        status = int(source.get("status") or 0)
        page_type = classify_path(path, status)
        inline_detailed = source if any(key in source for key in ("text", "images", "documents")) else None
        detailed = inline_detailed or detailed_by_path.get(path)
        outgoing = source.get("outgoing", source.get("links", 0))
        pages.append({
            "url": source["url"],
            "path": path,
            "status": status,
            "title": source.get("title", ""),
            "h1": source.get("h1", source.get("headings", [])[:1]),
            "outgoing_links": len(outgoing) if isinstance(outgoing, list) else int(outgoing or 0),
            "legacy_type": page_type,
            "target_cms_type": TYPE_TO_CMS[page_type],
            "has_detailed_snapshot": detailed is not None,
            "image_count": len(detailed.get("images", [])) if detailed else None,
            "document_count": len(detailed.get("documents", [])) if detailed else None,
            "suspicious_path": suspicious_path(path),
        })

    type_counts = Counter(page["legacy_type"] for page in pages)
    status_counts = Counter(str(page["status"]) for page in pages)
    matched_detailed_paths = {page["path"] for page in pages if page["has_detailed_snapshot"]}
    unmatched_detailed_paths = sorted(set(detailed_by_path) - {page["path"] for page in pages})
    remaining_queue = int(crawl.get("remaining", crawl.get("remaining_queue", len(crawl.get("queue", [])))) or 0)
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": {
            "declared_crawled": int(crawl.get("count") or len(pages)),
            "inventory_pages": len(pages),
            "remaining_queue": remaining_queue,
            "detailed_snapshots": sum(page["has_detailed_snapshot"] for page in pages),
            "detailed_snapshots_matched_to_crawl": len(matched_detailed_paths),
            "successful_pages_with_detailed_snapshot": sum(page["has_detailed_snapshot"] and 200 <= page["status"] < 400 for page in pages),
            "unmatched_detailed_paths": unmatched_detailed_paths,
        },
        "coverage": {
            "crawl_complete": remaining_queue == 0,
            "detailed_snapshot_ratio": round(sum(page["has_detailed_snapshot"] for page in pages) / max(len(pages), 1), 4),
            "successful_pages": sum(200 <= page["status"] < 400 for page in pages),
            "broken_pages": sum(page["status"] >= 400 for page in pages),
            "suspicious_paths": sum(page["suspicious_path"] for page in pages),
        },
        "counts_by_status": dict(sorted(status_counts.items())),
        "counts_by_legacy_type": dict(sorted(type_counts.items())),
        "pages": pages,
    }


def render_markdown(inventory: dict) -> str:
    source = inventory["source"]
    coverage = inventory["coverage"]
    lines = [
        "# Реестр старого сайта и полнота миграции",
        "",
        f"Сформировано: {inventory['generated_at']}",
        "",
        "## Состояние обследования",
        "",
        f"- обследовано URL: **{source['inventory_pages']}**;",
        f"- осталось в очереди предыдущего прохода: **{source['remaining_queue']}**;",
        f"- успешных ответов: **{coverage['successful_pages']}**;",
        f"- ответов 4xx/5xx: **{coverage['broken_pages']}**;",
        f"- подробных снимков с текстом и медиа: **{source['detailed_snapshots']}** (совпали с crawl: **{source['detailed_snapshots_matched_to_crawl']}**);",
        f"- полный crawl: **{'да' if coverage['crawl_complete'] else 'нет'}**.",
        "",
        "## Типы материалов",
        "",
        "| Тип старого материала | Найдено | Новая сущность |",
        "|---|---:|---|",
    ]
    for legacy_type, count in inventory["counts_by_legacy_type"].items():
        lines.append(f"| `{legacy_type}` | {count} | `{TYPE_TO_CMS[legacy_type]}` |")
    broken = [page for page in inventory["pages"] if page["status"] >= 400]
    lines.extend(["", "## Нерабочие или подозрительные URL", ""])
    if not broken:
        lines.append("Не обнаружены.")
    else:
        lines.extend(["| HTTP | Путь | Причина проверки |", "|---:|---|---|"])
        for page in broken:
            reason = "повтор сегмента пути" if page["suspicious_path"] else "ответ старого сайта"
            lines.append(f"| {page['status']} | `{page['path']}` | {reason} |")
    if source["unmatched_detailed_paths"]:
        lines.extend(["", "Подробные снимки вне списка crawl:"])
        lines.extend(f"- `{path}`" for path in source["unmatched_detailed_paths"])
    lines.extend([
        "",
        "## Вывод",
        "",
        "Текущий реестр доказывает структуру ключевых разделов, но не является полным экспортом архива. До переключения домена нужно завершить очередь, получить подробный снимок каждой успешной страницы, зеркалировать медиа и сверить 301-редиректы.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    full_checkpoint = ROOT / "data" / "legacy-crawl-checkpoint.json"
    parser = argparse.ArgumentParser(description="Строит проверяемый реестр уже обследованных страниц старого сайта")
    parser.add_argument("--crawl", type=Path, default=full_checkpoint if full_checkpoint.exists() else ROOT / "current-crawl.json")
    parser.add_argument("--sections", type=Path, default=ROOT / "current-sections.json")
    parser.add_argument("--json", type=Path, default=ROOT / "outputs" / "legacy-inventory.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "outputs" / "legacy-inventory.md")
    args = parser.parse_args()
    inventory = build_inventory(
        json.loads(args.crawl.read_text(encoding="utf-8")),
        json.loads(args.sections.read_text(encoding="utf-8")),
    )
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(render_markdown(inventory), encoding="utf-8")
    print(json.dumps({"pages": len(inventory["pages"]), **inventory["coverage"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
