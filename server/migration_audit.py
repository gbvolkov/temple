from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .config import ROOT, Settings
from .db import connect
from .full_import import is_index
from .media_mirror import collect_assets


def audit(
    inventory: dict,
    asset_source: object,
    database: Path,
    media_manifest_path: Path | None = None,
    media_root: Path | None = None,
) -> dict:
    with connect(database) as connection:
        contents = [dict(row) for row in connection.execute(
            "SELECT content_type,status,migration_review_required,legacy_url,data_json FROM contents"
        ).fetchall()]
        redirect_rows = [dict(row) for row in connection.execute("SELECT old_path,new_path FROM redirects").fetchall()]
        redirects = len(redirect_rows)
        migration_runs = connection.execute("SELECT COUNT(*) FROM migration_runs").fetchone()[0]
        remote_asset_records = connection.execute("SELECT COUNT(*) FROM contents WHERE data_json LIKE '%https://www.sv-innokenty.ru/%assets/%'").fetchone()[0]
        local_asset_records = connection.execute("SELECT COUNT(*) FROM contents WHERE data_json LIKE '%/media/legacy/%'").fetchone()[0]
    manifest = {"entries": {}, "errors": []}
    if media_manifest_path and media_manifest_path.exists():
        manifest = json.loads(media_manifest_path.read_text(encoding="utf-8"))
    assets = collect_assets(asset_source)
    mirrored = sum(entry.get("status") == "mirrored" for entry in manifest.get("entries", {}).values())
    type_counts = Counter(item["content_type"] for item in contents)
    status_counts = Counter(item["status"] for item in contents)
    review_required = sum(bool(item["migration_review_required"]) for item in contents)
    successful_pages = inventory["coverage"]["successful_pages"]
    detailed = inventory["source"].get("successful_pages_with_detailed_snapshot", inventory["source"]["detailed_snapshots"])
    local_references: set[str] = set()

    def collect_local_references(value: object) -> None:
        if isinstance(value, str) and value.startswith("/media/legacy/"):
            local_references.add(value.removeprefix("/media/legacy/").split("?", 1)[0])
        elif isinstance(value, list):
            for item in value:
                collect_local_references(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect_local_references(item)

    for content in contents:
        collect_local_references(json.loads(content["data_json"]))
    missing_local_files = []
    if media_root is not None:
        missing_local_files = sorted(
            reference for reference in local_references
            if not (media_root / "legacy" / Path(reference)).is_file()
        )
    local_files_verified = media_root is None or not missing_local_files
    expected_detail_paths = {
        page["path"] for page in inventory.get("pages", [])
        if 200 <= int(page.get("status") or 0) < 400
        and not is_index(page["path"], page.get("legacy_type", ""))
    }
    detail_redirect_paths = {
        item["old_path"] for item in redirect_rows
        if item["new_path"].startswith("/#/content/")
    }
    missing_detail_redirects = sorted(expected_detail_paths - detail_redirect_paths)
    gates = {
        "full_public_crawl": inventory["coverage"]["crawl_complete"],
        "detailed_snapshot_for_every_successful_page": detailed >= successful_pages,
        "all_referenced_media_resolved": remote_asset_records == 0 and local_asset_records > 0 and local_files_verified,
        "legacy_records_imported": len(contents) > 0 and migration_runs > 0,
        "all_imported_records_reviewed": len(contents) > 0 and review_required == 0,
        "no_unreviewed_content_published": not any(item["status"] == "published" and item["migration_review_required"] for item in contents),
        "redirects_cover_successful_pages": redirects >= successful_pages,
        "detail_pages_keep_individual_destination": not missing_detail_redirects,
        "source_media_failures_documented": all(item.get("url") and item.get("error") for item in manifest.get("errors", [])),
    }
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "ready_for_cutover": all(gates.values()),
        "gates": gates,
        "legacy": {
            "crawled": inventory["source"]["inventory_pages"],
            "remaining_queue": inventory["source"]["remaining_queue"],
            "successful_pages": successful_pages,
            "broken_pages": inventory["coverage"]["broken_pages"],
            "detailed_snapshots": detailed,
        },
        "new_cms": {
            "records": len(contents),
            "review_required": review_required,
            "redirects": redirects,
            "detail_redirects": len(detail_redirect_paths),
            "expected_detail_redirects": len(expected_detail_paths),
            "missing_detail_redirects": missing_detail_redirects,
            "migration_runs": migration_runs,
            "by_type": dict(sorted(type_counts.items())),
            "by_status": dict(sorted(status_counts.items())),
        },
        "media": {
            "known_assets": len(assets),
            "mirrored": mirrored,
            "source_missing_or_broken": len(manifest.get("errors", [])),
            "records_with_local_assets": local_asset_records,
            "records_with_remote_assets": remote_asset_records,
            "referenced_local_files": len(local_references),
            "missing_local_files": len(missing_local_files),
            "missing_local_file_paths": missing_local_files,
        },
    }


def render_markdown(report: dict) -> str:
    labels = {
        "full_public_crawl": "Полный обход публичного сайта",
        "detailed_snapshot_for_every_successful_page": "Подробный снимок каждой рабочей страницы",
        "all_referenced_media_resolved": "Все ссылки в импортированных материалах локальны или безопасно удалены",
        "legacy_records_imported": "Материалы импортированы в новую CMS",
        "all_imported_records_reviewed": "Все импортированные материалы проверены редактором",
        "no_unreviewed_content_published": "Непроверенные материалы не опубликованы",
        "redirects_cover_successful_pages": "301-редиректы покрывают рабочие URL",
        "detail_pages_keep_individual_destination": "Детальные страницы ведут на собственные материалы, а не только в раздел",
        "source_media_failures_documented": "Отсутствующие на старом сервере файлы задокументированы",
    }
    lines = [
        "# Готовность миграции к переключению домена",
        "",
        f"Итог: **{'ГОТОВО' if report['ready_for_cutover'] else 'НЕ ГОТОВО'}**",
        "",
        "| Контрольный барьер | Состояние |",
        "|---|---|",
    ]
    for key, passed in report["gates"].items():
        lines.append(f"| {labels[key]} | {'✅' if passed else '❌'} |")
    legacy, cms, media = report["legacy"], report["new_cms"], report["media"]
    lines.extend([
        "",
        "## Числа",
        "",
        f"- старых URL обследовано: **{legacy['crawled']}**, в очереди: **{legacy['remaining_queue']}**;",
        f"- подробных снимков: **{legacy['detailed_snapshots']}** из **{legacy['successful_pages']}** рабочих страниц;",
        f"- в новой CMS: **{cms['records']}** записей, требуют проверки: **{cms['review_required']}**;",
        f"- 301-редиректов: **{cms['redirects']}**;",
        f"- индивидуальных редиректов материалов: **{cms['detail_redirects']}** из **{cms['expected_detail_redirects']}**;",
        f"- известных медиа: **{media['known_assets']}**, зеркалировано: **{media['mirrored']}**, отсутствует на старом сервере: **{media['source_missing_or_broken']}**;",
        f"- записей с локальными медиа: **{media['records_with_local_assets']}**, с внешними asset-ссылками: **{media['records_with_remote_assets']}**.",
        f"- уникальных локальных файлов в материалах: **{media['referenced_local_files']}**, отсутствует на диске: **{media['missing_local_files']}**.",
        "",
        "Переключать домен можно только после прохождения всех барьеров. Отдельный зелёный тест интерфейса не заменяет полноту миграции контента.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    settings = Settings.from_env()
    full_plan = ROOT / "data" / "full-import-plan.json"
    parser = argparse.ArgumentParser(description="Проверяет готовность миграции к production cutover")
    parser.add_argument("--inventory", type=Path, default=ROOT / "outputs" / "legacy-inventory.json")
    parser.add_argument("--asset-source", type=Path, default=full_plan if full_plan.exists() else ROOT / "current-sections.json")
    parser.add_argument("--database", type=Path, default=settings.database_path)
    parser.add_argument("--media-manifest", type=Path, default=ROOT / "data" / "legacy-media-manifest.json")
    parser.add_argument("--media-root", type=Path, default=settings.media_dir)
    parser.add_argument("--json", type=Path, default=ROOT / "outputs" / "migration-readiness.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "outputs" / "migration-readiness.md")
    args = parser.parse_args()
    report = audit(
        json.loads(args.inventory.read_text(encoding="utf-8")),
        json.loads(args.asset_source.read_text(encoding="utf-8")),
        args.database,
        args.media_manifest,
        args.media_root,
    )
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"ready_for_cutover": report["ready_for_cutover"], "gates": report["gates"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
