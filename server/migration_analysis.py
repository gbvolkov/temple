"""Forecast editorial policy on an isolated database/media snapshot, never apply it."""
from __future__ import annotations

import argparse
import gc
import json
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

from scripts.run_cms_regression import stable_source_report
from .baseline import copy_snapshot_file, database_report, readonly_connection
from .config import ROOT, Settings
from .db import connect, init_database, utc_now
from .media_library import index_library, resolve_media_path
from .media_reconciliation import reconcile_media
from .migration_acceptance import execute_audit_run, queue_audit
from .migration_policy import POLICY_VERSION, gallery_date_proposal, news_archive_proposal, reason
from .migration_preparation import (
    EDITORIAL_CODES, PREPARATION_RULES_VERSION, _clean_crawl_text,
    _render_leaflet_cover, propose_content,
)
from .workflow import admin_content


REASON_LABELS = {
    "required_field_missing": "Не заполнены обязательные поля",
    "duplicate_title": "Совпадают заголовки материалов",
    "duplicate_content": "Вероятный дубль содержимого",
    "text_extraction_ambiguous": "Не определены границы содержательного текста",
    "external_check_missing_or_stale": "Нет свежей проверки внешней ссылки",
    "external_check_inconclusive": "Технически неопределённый результат проверки внешней ссылки",
    "external_link_unavailable": "Внешняя ссылка подтверждённо недоступна",
    "missing_legacy_media": "В исходнике есть утраченные необязательные файлы",
    "media_missing_or_invalid": "Отсутствует или повреждён медиафайл",
    "unpublished_internal_link": "Ссылка ведёт на неопубликованный материал",
    "legacy_text": "Остался непроверенный импортированный текст",
    "legacy_navigation_text": "В тексте остались элементы старого меню",
}
INTRINSIC_REASONS = {"text_extraction_ambiguous", "treby_details_require_editor", "leaflet_cover_requires_editor"}
EDITORIAL_AUDIT_CODES = EDITORIAL_CODES | INTRINSIC_REASONS | {"required_field_missing", "fallback_date_2000", "legacy_text"}


@contextmanager
def _temporary_snapshot(work_root: Path):
    with TemporaryDirectory(prefix="forecast-", dir=work_root) as temporary:
        target = Path(temporary).resolve()
        if target.parent != work_root:
            raise ValueError("Небезопасный временный каталог")
        try:
            yield target
        finally:
            # Legacy helpers use sqlite's transaction context manager, which
            # does not close connections. Release their unreachable cycles
            # before Windows removes this verified disposable directory.
            gc.collect()


def _clean_source_text(content: dict[str, Any]) -> str:
    data = content["data"]
    raw = data.get("body_text") or ""
    for field in ("body", "biography"):
        value = data.get(field)
        if isinstance(value, str):
            raw = value
        elif isinstance(value, list):
            legacy = [str(block.get("text") or "") for block in value
                      if isinstance(block, dict) and block.get("type") == "legacy_text"]
            if legacy:
                raw = "\n\n".join(legacy)
    if not raw:
        return ""
    text, confident = _clean_crawl_text(str(raw), content["title"])
    return text if confident else ""


def _audit(settings: Settings, *, check_external: bool) -> dict[str, Any]:
    run = queue_audit(settings.database_path, actor_id=None, scope={"check_external": check_external})
    return execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir,
                             settings.site_dir, run["id"], check_external=check_external)


def _unique_reasons(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {}
    for value in values:
        item = {**value, "field_path": value.get("field_path", "")}
        item.setdefault("category", "editorial" if item["code"] in EDITORIAL_AUDIT_CODES else "technical")
        unique.setdefault((item["code"], item["field_path"], item["message"]), item)
    return sorted(unique.values(), key=lambda item: (item["code"], item["field_path"], item["message"]))


def _summarize(items: list[dict[str, Any]], *, total: int, source_queue: int, normalized_queue: int) -> dict[str, Any]:
    outcomes = Counter(item["outcome"] for item in items)
    pending = [item for item in items if item["outcome"] == "remaining_queue"]
    reasons: dict[str, Any] = {}
    combinations: dict[tuple[str, ...], list[str]] = defaultdict(list)
    by_type: dict[str, Counter] = defaultdict(Counter)
    for item in items:
        by_type[item["content_type"]]["scope"] += 1
        by_type[item["content_type"]][item["outcome"]] += 1
        if item["corrected"]:
            by_type[item["content_type"]]["corrected"] += 1
    for item in pending:
        combinations[tuple(sorted({r["code"] for r in item["reasons"]}))].append(item["id"])
        for r in item["reasons"]:
            aggregate = reasons.setdefault(r["code"], {
                "code": r["code"], "label": REASON_LABELS.get(r["code"], r["message"]),
                "material_ids": set(), "occurrences": 0, "fields": Counter(),
                "by_type": defaultdict(set), "categories": set(),
            })
            aggregate["material_ids"].add(item["id"])
            aggregate["occurrences"] += 1
            aggregate["fields"][r["field_path"] or "material"] += 1
            aggregate["by_type"][item["content_type"]].add(item["id"])
            aggregate["categories"].add(r["category"])
    reason_rows = []
    for value in reasons.values():
        reason_rows.append({**value, "materials": len(value["material_ids"]),
                            "material_ids": sorted(value["material_ids"]), "fields": dict(sorted(value["fields"].items())),
                            "by_type": {key: len(ids) for key, ids in sorted(value["by_type"].items())},
                            "categories": sorted(value["categories"])})
    summary = {
        "total_contents": total, "source_queue": source_queue, "queue_after_schema": normalized_queue,
        "scope": len(items), "outside_scope": total - len(items),
        "archive": outcomes["archive"], "ready_for_publication": outcomes["ready_for_publication"],
        "corrected_and_ready": sum(item["corrected"] and item["outcome"] == "ready_for_publication" for item in items),
        "already_ready": sum(not item["corrected"] and item["outcome"] == "ready_for_publication" for item in items),
        "remaining_queue": outcomes["remaining_queue"],
        "corrected_total": sum(item["corrected"] for item in items if item["outcome"] != "archive"),
        "gallery_dates_changed": sum(bool(item.get("date_proposal", {}).get("changed")) for item in items),
        "gallery_years_changed": sum(bool(item.get("date_proposal", {}).get("changed")) and item["date_before"][:4] != item["date_after"][:4] for item in items),
        "gallery_year_only": sum(item.get("date_proposal", {}).get("precision") == "year" for item in items),
        "gallery_photos_before": sum(item.get("photo_count_before", 0) for item in items),
        "gallery_photos_after": sum(item.get("photo_count_after", 0) for item in items),
        "gallery_all_photos_preserved": all(item.get("photo_sequence_preserved", True) for item in items),
        "newly_queued": sum(not item["was_queued"] for item in items),
        "technical_queue": sum(any(r["category"] == "technical" for r in item["reasons"]) for item in pending),
        "editorial_only_queue": sum(all(r["category"] != "technical" for r in item["reasons"]) for item in pending),
    }
    assert summary["scope"] == sum(summary[key] for key in ("archive", "ready_for_publication", "remaining_queue"))
    assert all(item["reasons"] for item in pending)
    return {
        "summary": summary, "by_type": {key: dict(value) for key, value in sorted(by_type.items())},
        "remaining_reasons": sorted(reason_rows, key=lambda value: (-value["materials"], value["code"])),
        "reason_combinations": [{"codes": list(codes), "materials": len(ids), "material_ids": sorted(ids)}
                                for codes, ids in sorted(combinations.items(), key=lambda pair: (-len(pair[1]), pair[0]))],
    }


def analyze_migration(settings: Settings, *, check_external: bool = False, work_root: Path | None = None,
                      progress: Callable[[str], None] | None = None,
                      projection_dir: Path | None = None) -> dict[str, Any]:
    """Migrate, index, propose, project and re-audit only disposable copies."""
    source_db, source_media = settings.database_path.resolve(), settings.media_dir.resolve()
    if not source_db.is_file():
        raise FileNotFoundError(source_db)
    if not source_media.is_dir():
        raise FileNotFoundError(source_media)
    if projection_dir is not None:
        projection_dir = projection_dir.resolve()
        if (projection_dir.exists() or projection_dir == source_db.parent
                or projection_dir == source_media or source_media in projection_dir.parents
                or projection_dir in source_media.parents):
            raise ValueError("Проекция должна находиться в новом каталоге вне источника")
    work_root = (work_root or ROOT / "work" / "migration-analysis").resolve()
    if work_root == source_media or source_media in work_root.parents or work_root == source_db:
        raise ValueError("Рабочий каталог анализатора не может находиться в исходной медиатеке")
    work_root.mkdir(parents=True, exist_ok=True)
    emit = progress or (lambda message: None)
    manifest = settings.media_manifest_path.resolve()
    if not manifest.is_file():
        raise FileNotFoundError(manifest)
    emit("Фиксация контрольных сумм источника")
    before = stable_source_report(source_db, source_media, manifest)
    source_metadata = database_report(source_db)
    items: list[dict[str, Any]] = []
    with _temporary_snapshot(work_root) as target:
        emit("Создание изолированного SQLite-снимка и копии медиатеки")
        database = target / "cms.sqlite3"
        with closing(readonly_connection(source_db)) as source, closing(sqlite3.connect(database)) as destination:
            source.backup(destination)
        shutil.copytree(source_media, target / "media", copy_function=copy_snapshot_file)
        simulated = replace(settings, database_path=database, media_dir=target / "media",
                            media_derivatives_dir=target / "derivatives")
        init_database(database)
        normalized = database_report(database)
        emit("Сверка реестра медиа на копии")
        reconciliation = None
        if manifest:
            media_report = reconcile_media(database, simulated.media_dir, manifest,
                                           missing_report=simulated.missing_report_path,
                                           derivatives_dir=simulated.derivatives_dir)
            indexed = None
            if media_report["status"] != "ok":
                def index_progress(state: dict[str, Any]) -> None:
                    processed, total = state["processed_files"], state["total_files"]
                    if processed % 1000 == 0 or processed == total:
                        emit(f"Медиа: {processed}/{total}, этап {state['phase']}")
                indexed = index_library(database, simulated.media_dir,
                                        missing_report=simulated.missing_report_path, progress=index_progress)
                media_report = reconcile_media(database, simulated.media_dir, manifest,
                                               missing_report=simulated.missing_report_path,
                                               derivatives_dir=simulated.derivatives_dir)
            reconciliation = {"index": indexed, "reconciliation": media_report}
        schema = json.loads(settings.schema_path.read_text(encoding="utf-8"))
        with closing(connect(database)) as c:
            rows = c.execute("SELECT * FROM contents ORDER BY id").fetchall()
            # Earlier snapshots retain date evidence removed by preparation.
            historical_text = {}
            previous_dates = {}
            manually_dated = {}
            for revision in c.execute("SELECT content_id,snapshot_json,actor_id FROM revisions ORDER BY version"):
                snapshot = json.loads(revision["snapshot_json"])
                event_date = snapshot.get("data", {}).get("event_date")
                content_id = revision["content_id"]
                if content_id in previous_dates and event_date != previous_dates[content_id]:
                    manually_dated[content_id] = event_date if revision["actor_id"] else None
                previous_dates[content_id] = event_date
                if revision["content_id"] not in historical_text:
                    if "data" in snapshot and "title" in snapshot:
                        text = _clean_source_text(snapshot)
                        if text:
                            historical_text[revision["content_id"]] = text
            selected = []
            for row in rows:
                content = admin_content(row)
                policy = None
                if (row["content_type"] == "gallery" and row["status"] not in {"archived", "trash"}
                        and (row["migration_review_required"] or row["legacy_url"] or row["legacy_id"])):
                    if manually_dated.get(row["id"]) and manually_dated[row["id"]] == content["data"].get("event_date"):
                        content["data"]["event_date_precision"] = "day"
                    policy = gallery_date_proposal(content, clean_text=_clean_source_text(content) or historical_text.get(row["id"], ""))
                if row["migration_review_required"] or (policy and (policy["changed"] or policy["reasons"])):
                    selected.append((row, policy))
                    c.execute("UPDATE contents SET migration_review_required=1 WHERE id=?", (row["id"],))
            c.commit()
        emit(f"Исходный аудит {len(selected)} материалов на копии")
        pre_audit = _audit(simulated, check_external=check_external)
        emit("Расчёт исправлений, архивирования и неполных дат")
        with closing(connect(database)) as c:
            for original, policy in selected:
                row = c.execute("SELECT * FROM contents WHERE id=?", (original["id"],)).fetchone()
                content = admin_content(row)
                archive = news_archive_proposal(content) if row["content_type"] == "news" else None
                if archive and archive["archive"]:
                    snapshot = {key: content[key] for key in ("content_type", "slug", "title", "data")}
                    preparation = {"before": snapshot, "proposed": snapshot, "transformations": [], "reasons": []}
                else:
                    preparation = propose_content(c, row, schema, simulated.media_dir)
                proposed = preparation["proposed"]
                transforms = preparation["transformations"]
                reasons = [r for r in preparation["reasons"] if r["code"] in INTRINSIC_REASONS]
                if policy:
                    proposed["data"]["event_date"] = policy["after"]
                    if policy["precision"] in {"year", "day"}:
                        proposed["data"]["event_date_precision"] = policy["precision"]
                        proposed["data"]["event_date_policy_version"] = POLICY_VERSION
                    reasons.extend(policy["reasons"])
                    if policy["changed"]:
                        transforms.append({"rule": "gallery_date_policy", "before": policy["before"], "after": policy["after"]})
                archived = bool(archive and archive["archive"])
                corrected = proposed != preparation["before"]
                if not archived:
                    for transform in transforms:
                        if transform["rule"] == "leaflet_pdf_cover":
                            _render_leaflet_cover(simulated, c, transform)
                c.execute("""UPDATE contents SET content_type=?,slug=?,title=?,data_json=?,
                    version=version+?,status=?,migration_review_required=?,published_version=? WHERE id=?""",
                    (proposed["content_type"], proposed["slug"], proposed["title"],
                     json.dumps(proposed["data"], ensure_ascii=False), int(corrected),
                     "archived" if archived else row["status"], 0 if archived else 1,
                     None if archived else row["published_version"], row["id"]))
                if not archived:
                    if reconciliation and reconciliation["reconciliation"]["status"] != "ok":
                        reasons.append(reason("media_inventory_unreconciled", "Медиа-реестр копии содержит необъяснённые расхождения", category="technical"))
                    if row["status"] != "draft" or row["published_version"] is not None:
                        reasons.append(reason("workflow_state_requires_action", "Материал не является непубличным черновиком", category="technical"))
                    active = c.execute("""SELECT 1 FROM migration_review_batch_items bi JOIN migration_review_batches b ON b.id=bi.batch_id
                        WHERE bi.content_id=? AND b.status IN ('draft','in_review') LIMIT 1""", (row["id"],)).fetchone()
                    if active:
                        reasons.append(reason("active_review_batch", "Материал входит в незавершённую редакционную партию", category="technical"))
                date_field = "event_date" if row["content_type"] == "gallery" else "publication_date"
                item = {
                    "id": row["id"], "title": proposed["title"], "content_type": row["content_type"],
                    "legacy_url": row["legacy_url"], "version": original["version"], "status": row["status"],
                    "was_queued": bool(original["migration_review_required"]), "corrected": corrected,
                    "changed_fields": sorted(key for key in set(content["data"]) | set(proposed["data"])
                                             if content["data"].get(key) != proposed["data"].get(key)),
                    "date_before": str(content["data"].get(date_field) or ""),
                    "date_after": str(proposed["data"].get(date_field) or ""),
                    "transformations": transforms, "reasons": [] if archived else reasons,
                    "outcome": "archive" if archived else "pending_audit",
                }
                if policy:
                    item["date_proposal"] = policy
                if row["content_type"] == "gallery":
                    old_photos = content["data"].get("photos") or []
                    new_photos = proposed["data"].get("photos") or []
                    images = lambda photos: [p.get("image") if isinstance(p, dict) else p for p in photos]
                    item.update(photo_count_before=len(old_photos), photo_count_after=len(new_photos),
                                photo_sequence_preserved=images(old_photos) == images(new_photos))
                    if not item["photo_sequence_preserved"]:
                        raise ValueError(f"Подготовка изменила список фотографий альбома {row['id']}")
                if archive:
                    item["archive_proposal"] = archive
                items.append(item)
            c.commit()
        emit("Повторный аудит предлагаемого состояния")
        post_audit = _audit(simulated, check_external=check_external)
        with closing(connect(database)) as c:
            for item in items:
                if item["outcome"] == "archive":
                    continue
                issues = c.execute("""SELECT i.code,i.message,i.field_path,i.severity FROM migration_review_issues i
                    JOIN contents c ON c.id=i.content_id AND c.version=i.content_version
                    WHERE i.audit_run_id=? AND i.content_id=? AND i.status IN ('open','acknowledged')
                    AND i.severity IN ('blocker','warning') ORDER BY i.code,i.field_path""", (post_audit["id"], item["id"])).fetchall()
                item["reasons"] = _unique_reasons([*item["reasons"], *[dict(issue) for issue in issues]])
                item["outcome"] = "remaining_queue" if item["reasons"] else "ready_for_publication"
        result = _summarize(items, total=normalized["metrics"]["contents"],
                            source_queue=source_metadata["review_required"], normalized_queue=normalized["review_required"])
        if projection_dir is not None:
            projection_dir.mkdir(parents=True, exist_ok=False)
            with closing(readonly_connection(database)) as source, closing(sqlite3.connect(projection_dir / "projected.sqlite3")) as destination:
                source.backup(destination)
            with closing(readonly_connection(database)) as c:
                for row in c.execute("SELECT stored_name FROM media WHERE source='migration_preparation'"):
                    stored_name = row["stored_name"]
                    if not resolve_media_path(source_media, stored_name).exists():
                        exported = resolve_media_path(projection_dir / "media", stored_name)
                        exported.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(resolve_media_path(simulated.media_dir, stored_name), exported)
    emit("Проверка неизменности исходной БД, медиа и manifest")
    after = stable_source_report(source_db, source_media, manifest)
    return {
        "format_version": 1, "generated_at": utc_now(), "policy_version": POLICY_VERSION,
        "preparation_rules_version": PREPARATION_RULES_VERSION, "archive_scope": "all_queued_news",
        "mode": "forecast_only", "source_database": str(source_db), "source_schema": source_metadata["schema_version"],
        "analysis_schema": normalized["schema_version"], "external_checks": "online" if check_external else "fresh_cache_only",
        "source_unchanged": before == after, "source_before": before, "source_after": after,
        "audit_counts": {"before": pre_audit["counts"], "after": post_audit["counts"]},
        "media_reconciliation": reconciliation["reconciliation"] if reconciliation else None,
        **result, "items": items,
        "notes": [
            "Прогноз построен на изолированной копии; исходные материалы не изменены и не опубликованы.",
            "Готовность означает возможность миграционной приёмки и штатного workflow публикации после применения и проверки исправлений.",
            "Причины пересекаются: один материал может учитываться в нескольких строках; комбинации причин образуют непересекающиеся группы.",
            "Все новости и анонсы из очереди, включая недатированные, архивируются без публикации и изменения содержимого.",
            "01.01 при известном годе из URL принято пользователем как допустимая дата и не является причиной очереди.",
            "Все фотографии альбомов, включая повторные ссылки и их порядок, сохраняются.",
            "Схема и медиа-реестр обновлены только на копии. Состав очереди после schema может отличаться от исходного.",
            "Без --check-external используются только свежие подтверждения ссылок; их отсутствие остаётся технической причиной очереди.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = ["# Прогноз миграционной очереди", "", f"Дата: {report['generated_at']}", "",
             f"Источник: `{report['source_database']}`; schema источника: `{report['source_schema']}`, копии: `{report['analysis_schema']}`.", "",
             "Архивируются **все новости и анонсы из миграционной очереди**, включая недатированные, без публикации.", "",
             "| Показатель | Материалов |", "|---|---:|",
             f"| Всего в источнике | {s['total_contents']} |",
             f"| Очередь в исходной БД / после обновления схемы копии | {s['source_queue']} / {s['queue_after_schema']} |",
             f"| В области анализа | {s['scope']} |",
             f"| Будут архивированы | {s['archive']} |",
             f"| Готовы к публикации после применения и приёмки | {s['ready_for_publication']} |",
             f"| В том числе исправлены и готовы | {s['corrected_and_ready']} |",
             f"| В том числе уже готовы | {s['already_ready']} |",
             f"| Останутся в миграционной очереди | {s['remaining_queue']} |",
             f"| Из них с техническими препятствиями | {s['technical_queue']} |",
             f"| Из них только с содержательными причинами | {s['editorial_only_queue']} |",
             f"| Будут добавлены/возвращены в очередь | {s['newly_queued']} |",
             f"| Исправлены данные (без архивируемых) | {s['corrected_total']} |",
             f"| Изменена дата / год фотоальбома | {s['gallery_dates_changed']} / {s['gallery_years_changed']} |",
             f"| Фотоальбомы с подтверждённым только годом | {s['gallery_year_only']} |", "",
             f"Фотографии альбомов: **{s['gallery_photos_before']} → {s['gallery_photos_after']}**; список и порядок сохранены: **{s['gallery_all_photos_preserved']}**.", "",
             "## Распределение по типам", "", "| Тип | Всего | Архив | Готовы | Очередь |", "|---|---:|---:|---:|---:|"]
    for kind, counts in report["by_type"].items():
        lines.append(f"| {kind} | {counts['scope']} | {counts.get('archive', 0)} | {counts.get('ready_for_publication', 0)} | {counts.get('remaining_queue', 0)} |")
    lines += ["", "## Причины остатка очереди — все причины", "",
              "Количество материалов по причинам пересекается. «Замечаний» учитывает отдельные поля одного материала.", "",
              "| Код | Причина | Материалов | Замечаний | Поля |", "|---|---|---:|---:|---|"]
    for r in report["remaining_reasons"]:
        fields = ", ".join(f"{key}: {count}" for key, count in r["fields"].items())
        label = r["label"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{r['code']}` | {label} | {r['materials']} | {r['occurrences']} | {fields} |")
    lines += ["", "## Комбинации причин — непересекающиеся группы", "", "| Коды | Материалов |", "|---|---:|"]
    for combination in report["reason_combinations"]:
        lines.append(f"| {', '.join(combination['codes'])} | {combination['materials']} |")
    lines += ["", "## Условия и целостность", ""]
    lines += [f"- {note}" for note in report["notes"]]
    lines += [f"- Исходные данные неизменны: **{report['source_unchanged']}**.",
              f"- Режим проверки внешних ссылок: `{report['external_checks']}`.",
              "- Полный список материалов, предлагаемых дат и замечаний с ID содержится в соседнем JSON-отчёте.", ""]
    return "\n".join(lines)


def main() -> int:
    defaults = Settings.from_env()
    parser = argparse.ArgumentParser(description="Read-only прогноз архива, готовности и всех причин миграционной очереди")
    parser.add_argument("--database", type=Path, default=defaults.database_path)
    parser.add_argument("--media", type=Path, default=defaults.media_dir)
    parser.add_argument("--manifest", type=Path, default=defaults.media_manifest_path)
    parser.add_argument("--missing-report", type=Path, default=defaults.missing_report_path)
    parser.add_argument("--check-external", action="store_true", help="Выполнить сетевые проверки ссылок на копии")
    parser.add_argument("--work-root", type=Path, default=ROOT / "work/migration-analysis")
    parser.add_argument("--json", type=Path, default=ROOT / "outputs/migration-policy-analysis.json")
    parser.add_argument("--markdown", type=Path, default=ROOT / "outputs/migration-policy-analysis.md")
    args = parser.parse_args()
    protected = {path.resolve() for path in (args.database, Path(str(args.database) + "-wal"),
                  Path(str(args.database) + "-shm"), args.manifest, args.missing_report, defaults.schema_path)}
    outputs = [args.json.resolve(), args.markdown.resolve()]
    media = args.media.resolve()
    if len(set(outputs)) != 2 or any(path in protected or path == media or media in path.parents for path in outputs):
        parser.error("Отчёты должны быть отдельными файлами вне исходной БД, manifest, отчёта и медиатеки")
    settings = replace(defaults, database_path=args.database, media_dir=args.media,
                       media_manifest_path=args.manifest, missing_media_report_path=args.missing_report)
    report = analyze_migration(settings, check_external=args.check_external,
                               work_root=args.work_root, progress=lambda message: print(message, file=sys.stderr, flush=True))
    for path, value in ((outputs[0], json.dumps(report, ensure_ascii=False, indent=2) + "\n"),
                        (outputs[1], render_markdown(report))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    print(json.dumps({"summary": report["summary"], "source_unchanged": report["source_unchanged"],
                      "json": str(outputs[0]), "markdown": str(outputs[1])}, ensure_ascii=False))
    return 0 if report["source_unchanged"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
