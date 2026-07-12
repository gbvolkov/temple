from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import ROOT
from .media_mirror import canonical_asset_url


def references(value, target: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        canonical = canonical_asset_url(value, {"www.sv-innokenty.ru", "sv-innokenty.ru"})
        if canonical == target:
            found.append("")
    elif isinstance(value, list):
        for item in value:
            found.extend(references(item, target))
    elif isinstance(value, dict):
        image = value.get("image")
        if isinstance(image, str) and canonical_asset_url(image, {"www.sv-innokenty.ru", "sv-innokenty.ru"}) == target:
            found.append(value.get("fallback", ""))
        else:
            for item in value.values():
                found.extend(references(item, target))
    return found


def build_report(plan: dict, manifest: dict) -> list[dict]:
    mirrored = manifest.get("entries", {})
    errors_by_url = {item["url"]: item for item in manifest.get("errors", [])}
    references_by_url: dict[str, list[dict]] = {url: [] for url in errors_by_url}

    def scan(value, record):
        if isinstance(value, list):
            for item in value:
                scan(item, record)
        elif isinstance(value, dict):
            image = value.get("image")
            canonical = canonical_asset_url(image, {"www.sv-innokenty.ru", "sv-innokenty.ru"}) if isinstance(image, str) else None
            if canonical in errors_by_url:
                references_by_url[canonical].append({
                    "title": record["title"],
                    "legacy_url": record["legacy_url"],
                    "fallback": value.get("fallback", ""),
                })
            for item in value.values():
                scan(item, record)

    for record in plan.get("records", []):
        scan(record.get("data", {}), record)

    rows = []
    for url, error in errors_by_url.items():
        used_by = references_by_url[url]
        fallbacks = [item["fallback"] for item in used_by if item.get("fallback")]
        canonical_fallbacks = [canonical_asset_url(value, {"www.sv-innokenty.ru", "sv-innokenty.ru"}) for value in fallbacks]
        mirrored_fallbacks = [value for value in canonical_fallbacks if value and mirrored.get(value, {}).get("status") == "mirrored"]
        path = unquote(urlsplit(url).path)
        rows.append({
            "url": url,
            "error": error["error"],
            "directory": str(Path(path).parent).replace("\\", "/"),
            "references": len(used_by),
            "fallback_mirrored": bool(mirrored_fallbacks),
            "fallback_url": mirrored_fallbacks[0] if mirrored_fallbacks else "",
            "materials": used_by,
        })
    return rows


def render_markdown(rows: list[dict]) -> str:
    directories = Counter(row["directory"] for row in rows)
    fallback_count = sum(row["fallback_mirrored"] for row in rows)
    lines = [
        "# Файлы, отсутствующие на старом сервере",
        "",
        f"Всего: **{len(rows)}**. Для **{fallback_count}** найдено и зеркалировано уменьшенное изображение. Остальные ссылки удалены из импортированных данных, чтобы новая CMS не показывала битые изображения.",
        "",
        "## Основные каталоги",
        "",
        "| Каталог старого сайта | Файлов |",
        "|---|---:|",
    ]
    for directory, count in directories.most_common(20):
        lines.append(f"| `{directory}` | {count} |")
    lines.extend(["", "Полный построчный список и связанные материалы находятся в `missing-legacy-media.csv`.", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Формирует редакторский отчёт по отсутствующим исходным медиа")
    parser.add_argument("--plan", type=Path, default=ROOT / "data" / "full-import-plan.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / "data" / "legacy-media-manifest.json")
    parser.add_argument("--csv", type=Path, default=ROOT / "outputs" / "missing-legacy-media.csv")
    parser.add_argument("--markdown", type=Path, default=ROOT / "outputs" / "missing-legacy-media.md")
    args = parser.parse_args()
    rows = build_report(
        json.loads(args.plan.read_text(encoding="utf-8")),
        json.loads(args.manifest.read_text(encoding="utf-8")),
    )
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["url", "error", "directory", "references", "fallback_mirrored", "fallback_url", "materials"])
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "materials": "; ".join(f"{item['title']} [{item['legacy_url']}]" for item in row["materials"])})
    args.markdown.write_text(render_markdown(rows), encoding="utf-8")
    print(json.dumps({"missing": len(rows), "with_fallback": sum(row["fallback_mirrored"] for row in rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
