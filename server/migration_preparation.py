from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
import time
import unicodedata
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from .config import Settings
from .db import connect, row_to_content, transaction, utc_now
from .media_library import (
    inspect_file,
    media_url,
    record_media_event,
    refresh_content_usages,
    resolve_media_path,
    stored_name_from_url,
)
from .migration_acceptance import (
    RULES_VERSION,
    AcceptanceError,
    execute_audit_run,
    queue_audit,
)
from .search import sync_content_search
from .workflow import admin_content, record_audit


PREPARATION_RULES_VERSION = "1.1.0"
CONTRACT_VERSION = "1.1"
PREPARATION_NAMESPACE = uuid.UUID("57f5b2b9-c1c2-4f5e-9f7e-2a33e35d2782")
RUN_STATUSES = ("queued", "running", "completed", "failed", "rolled_back")
RUN_MODES = ("preview", "apply")
CLASSIFICATIONS = ("auto_ready", "editorial_review", "technical_blocker")
SERVICE_METADATA_FIELDS = {
    "body_text", "headings", "legacy_images", "legacy_documents", "migration_note", "new_path",
}
EDITORIAL_CODES = {
    "duplicate_content",
    "duplicate_title",
    "external_link_unavailable",
    "missing_legacy_media",
    "unpublished_internal_link",
}
TECHNICAL_CODES = {
    "control_character",
    "encoding_suspect",
    "html_in_content",
    "image_alt_missing",
    "internal_link_cycle",
    "internal_link_missing",
    "invalid_date",
    "legacy_navigation_text",
    "media_missing_or_invalid",
    "static_media_missing",
    "unsafe_link",
    "external_check_inconclusive",
    "external_check_missing_or_stale",
}


PREPARATION_SCHEMA_SQL = """
CREATE TABLE migration_preparation_runs (
  id TEXT PRIMARY KEY,
  rules_version TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('preview','apply')),
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','rolled_back')),
  preview_run_id TEXT REFERENCES migration_preparation_runs(id),
  audit_run_id TEXT REFERENCES migration_audit_runs(id),
  counts_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  started_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  rolled_back_at TEXT
);
CREATE INDEX idx_migration_preparation_runs_status
  ON migration_preparation_runs(status, created_at DESC);
CREATE TABLE migration_preparation_items (
  run_id TEXT NOT NULL REFERENCES migration_preparation_runs(id) ON DELETE CASCADE,
  content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
  content_version INTEGER NOT NULL,
  result_version INTEGER,
  classification TEXT NOT NULL CHECK(classification IN ('auto_ready','editorial_review','technical_blocker')),
  state TEXT NOT NULL CHECK(state IN ('planned','applied','unchanged','rolled_back','failed')),
  before_json TEXT NOT NULL,
  proposed_json TEXT NOT NULL,
  before_hash TEXT NOT NULL,
  proposed_hash TEXT NOT NULL,
  transformations_json TEXT NOT NULL DEFAULT '[]',
  reasons_json TEXT NOT NULL DEFAULT '[]',
  recommendation TEXT NOT NULL DEFAULT '',
  options_json TEXT NOT NULL DEFAULT '[]',
  technical_blocker_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY(run_id, content_id)
);
CREATE INDEX idx_migration_preparation_items_queue
  ON migration_preparation_items(run_id, classification, content_id);
CREATE INDEX idx_migration_preparation_items_content
  ON migration_preparation_items(content_id, run_id);
"""

PREPARATION_REQUIRED_COLUMNS = {
    "migration_preparation_runs": {
        "id", "rules_version", "mode", "status", "preview_run_id", "audit_run_id",
        "counts_json", "error", "started_by", "created_at", "started_at",
        "finished_at", "rolled_back_at",
    },
    "migration_preparation_items": {
        "run_id", "content_id", "content_version", "result_version", "classification",
        "state", "before_json", "proposed_json", "before_hash", "proposed_hash",
        "transformations_json", "reasons_json", "recommendation", "options_json",
        "technical_blocker_count", "created_at",
    },
}
PREPARATION_REQUIRED_INDEXES = {
    "idx_migration_preparation_runs_status",
    "idx_migration_preparation_items_queue",
    "idx_migration_preparation_items_content",
}


class PreparationError(ValueError):
    def __init__(
        self,
        message: str,
        status_code: int = 422,
        code: str = "PREPARATION_ERROR",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details or {}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def validate_preparation_schema(connection: sqlite3.Connection) -> None:
    for table, required in PREPARATION_REQUIRED_COLUMNS.items():
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing = sorted(required - columns)
        if missing:
            raise RuntimeError(f"Preparation table {table} is incomplete: {', '.join(missing)}")
    indexes = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    missing_indexes = sorted(PREPARATION_REQUIRED_INDEXES - indexes)
    if missing_indexes:
        raise RuntimeError("Preparation indexes are missing: " + ", ".join(missing_indexes))


def apply_preparation_schema(connection: sqlite3.Connection) -> str:
    before = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("contents", "revisions", "media", "migration_review_issues")
    }
    for statement in PREPARATION_SCHEMA_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
    validate_preparation_schema(connection)
    after = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    if before != after:
        raise RuntimeError(f"Preparation schema changed existing rows: {before} -> {after}")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Preparation schema broke foreign keys")
    return "migration automatic-preparation journal created"


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return "".join(
        character
        for character in normalized
        if not (unicodedata.category(character) == "Cc" and character not in "\n\r\t")
    )


def _normalize_tree(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_text(value)
    if isinstance(value, list):
        return [_normalize_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_tree(item) for key, item in value.items()}
    return value


def _legacy_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    data = block.get("data") if isinstance(block.get("data"), dict) else {}
    return str(block.get("text") or data.get("text") or "")


def _clean_crawl_text(raw: str, title: str) -> tuple[str, bool]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in _normalize_text(raw).splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return "", False
    footer = next(
        (
            index for index, line in enumerate(lines)
            if line.startswith("(c) Храм святителя Иннокентия")
            or line.startswith("Мы не гарантируем корректную работу сайта")
        ),
        len(lines),
    )
    breadcrumbs = [
        index for index, line in enumerate(lines[:footer])
        if line.startswith("Главная -") and title.casefold() in line.casefold()
    ]
    if breadcrumbs:
        content = lines[breadcrumbs[-1] + 1:footer]
        while content and content[0].casefold() == title.casefold():
            content.pop(0)
        result = "\n\n".join(content).strip()
        return result, bool(result)
    chrome_markers = ("Новости прихода", "Приходской листок", "ТРЕБЫ ОНЛАЙН")
    if any(marker.casefold() in raw.casefold() for marker in chrome_markers):
        return "", False
    return "\n\n".join(lines), True


def _paragraph_blocks(content_id: str, text: str) -> list[dict[str, Any]]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return [
        {
            "id": str(uuid.uuid5(PREPARATION_NAMESPACE, f"{content_id}:paragraph:{index}:{part}")),
            "type": "paragraph",
            "data": {"runs": [{"text": part, "marks": []}]},
        }
        for index, part in enumerate(paragraphs)
    ]


def _summary(text: str, maximum: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= maximum:
        return compact
    shortened = compact[:maximum + 1].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return shortened + "…"


def _date_from_evidence(content: dict[str, Any], clean_text: str) -> str | None:
    evidence = " ".join((
        str(content.get("legacy_url") or ""),
        str(content.get("title") or ""),
        clean_text[:4000],
    ))
    numeric = {
        f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        for day, month, year in re.findall(
            r"(?<!\d)([0-3]?\d)[._/-]([01]?\d)[._/-]((?:19|20)\d{2})(?!\d)",
            evidence,
        )
        if 1 <= int(day) <= 31 and 1 <= int(month) <= 12 and int(year) != 2000
    }
    short_url = re.search(
        r"(?<!\d)([0-3]\d)[._-]([01]\d)[._-](\d{2})(?!\d)",
        str(content.get("legacy_url") or ""),
    )
    if short_url:
        day, month, year = map(int, short_url.groups())
        if 1 <= day <= 31 and 1 <= month <= 12:
            numeric.add(f"{2000 + year:04d}-{month:02d}-{day:02d}")
    return next(iter(numeric)) if len(numeric) == 1 else None


def _deduplicate_photos(data: dict[str, Any], title: str) -> bool:
    photos = data.get("photos")
    if not isinstance(photos, list):
        return False
    changed = False
    unique: list[Any] = []
    seen: set[str] = set()
    for photo in photos:
        if not isinstance(photo, dict):
            unique.append(photo)
            continue
        image = str(photo.get("image") or "")
        if image and image in seen:
            changed = True
            continue
        if image:
            seen.add(image)
        item = dict(photo)
        if image and not str(item.get("alt") or "").strip():
            item["alt"] = title
            changed = True
        unique.append(item)
    if changed:
        for index, photo in enumerate(unique, start=1):
            if isinstance(photo, dict):
                photo["order"] = index
        data["photos"] = unique
    return changed


def _current_issues(connection: sqlite3.Connection, content_id: str, version: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """SELECT code,severity,field_path,message,details_json
               FROM migration_review_issues
               WHERE content_id=? AND content_version=? AND status='open'
               ORDER BY severity,code,field_path""",
            (content_id, version),
        )
    ]


def _leaflet_cover_proposal(
    connection: sqlite3.Connection,
    data: dict[str, Any],
    media_dir: Path,
) -> dict[str, str] | None:
    pdf_url = str(data.get("pdf") or "")
    stored_name = stored_name_from_url(pdf_url)
    if not stored_name:
        return None
    row = connection.execute(
        "SELECT id,sha256,mime_type,status FROM media WHERE stored_name=?", (stored_name,)
    ).fetchone()
    source = resolve_media_path(media_dir, stored_name)
    if (
        not row or row["status"] != "ready" or row["mime_type"] != "application/pdf"
        or not source.is_file()
    ):
        return None
    digest = row["sha256"] or hashlib.sha256(source.read_bytes()).hexdigest()
    generated_name = f"generated/leaflet-covers/{digest}.webp"
    return {
        "pdf_media_id": row["id"],
        "pdf_stored_name": stored_name,
        "stored_name": generated_name,
        "media_id": str(uuid.uuid5(PREPARATION_NAMESPACE, "leaflet-cover:" + digest)),
        "url": media_url(generated_name),
    }


def _options(reasons: list[dict[str, Any]]) -> list[dict[str, str]]:
    codes = {reason["code"] for reason in reasons}
    result = [
        {
            "value": "accept_proposal",
            "label": "Принять предложенную версию",
            "consequence": "Материал останется черновиком и сможет пройти финальную миграционную приёмку.",
        }
    ]
    if codes & {"duplicate_content", "duplicate_title"}:
        result.append({
            "value": "merge_or_archive",
            "label": "Объединить или архивировать дубль",
            "consequence": "Потребуется выбрать канонический материал; публичные URL сохраняются через redirect.",
        })
    if "missing_legacy_media" in codes:
        result.append({
            "value": "choose_media",
            "label": "Выбрать замену медиа",
            "consequence": "Редактор подтверждает содержательно подходящий файл из медиатеки.",
        })
    result.append({
        "value": "archive",
        "label": "Оставить только в архиве",
        "consequence": "Материал будет сохранён в CMS, но не попадёт в публичную публикацию.",
    })
    return result


def propose_content(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    schema: dict[str, Any],
    media_dir: Path,
) -> dict[str, Any]:
    content = admin_content(row)
    before = {
        "content_type": content["content_type"],
        "slug": content["slug"],
        "title": content["title"],
        "data": content["data"],
    }
    proposed = _normalize_tree(before)
    data = proposed["data"]
    transformations: list[dict[str, Any]] = []
    technical: list[dict[str, Any]] = []
    editorial: list[dict[str, Any]] = []
    issues = _current_issues(connection, content["id"], content["version"])

    if proposed != before:
        transformations.append({"rule": "unicode_nfkc", "label": "Unicode и управляющие символы нормализованы"})

    definition = schema.get("content_types", {}).get(content["content_type"], {})
    fields = definition.get("fields", {})
    body_field = "biography" if content["content_type"] == "clergy" else "body"
    source_value = data.get(body_field)
    raw = str(data.get("body_text") or "")
    if isinstance(source_value, str):
        raw = source_value
    elif isinstance(source_value, list):
        legacy = [_legacy_text(block) for block in source_value if isinstance(block, dict) and block.get("type") == "legacy_text"]
        if legacy:
            raw = "\n\n".join(legacy)
    clean_text, confident = _clean_crawl_text(raw, proposed["title"]) if raw else ("", False)
    if raw and confident:
        blocks = _paragraph_blocks(content["id"], clean_text)
        if blocks and data.get(body_field) != blocks:
            data[body_field] = blocks
            transformations.append({"rule": "legacy_text_to_blocks", "label": "Служебный chrome удалён, текст преобразован в блоки"})
    elif raw:
        editorial.append({
            "code": "text_extraction_ambiguous",
            "message": "Границы содержательного текста нельзя определить формальным правилом.",
        })

    if "summary" in fields and not str(data.get("summary") or "").strip() and clean_text:
        maximum = int(fields["summary"].get("max_length") or 420)
        data["summary"] = _summary(clean_text, maximum)
        transformations.append({"rule": "summary_from_body", "label": "Краткое описание сформировано из первого содержательного текста"})

    for date_field in ("publication_date", "event_date"):
        if str(data.get(date_field) or "").startswith("2000-"):
            inferred = _date_from_evidence(content, clean_text)
            if inferred:
                data[date_field] = inferred
                transformations.append({"rule": "date_from_evidence", "field": date_field, "value": inferred})
            else:
                editorial.append({
                    "code": "date_requires_editor",
                    "field_path": date_field,
                    "message": "Год события отсутствует или источники даты противоречат друг другу.",
                })

    if data.get("cover") and "cover_alt" in fields and not str(data.get("cover_alt") or "").strip():
        data["cover_alt"] = proposed["title"]
        transformations.append({"rule": "cover_alt_from_title", "label": "Alt обложки восстановлен из заголовка"})

    if _deduplicate_photos(data, proposed["title"]):
        transformations.append({"rule": "photo_collection_normalization", "label": "Повторы внутри фотоколлекции удалены, alt восстановлен"})

    if content["content_type"] == "leaflet_issue" and not data.get("cover"):
        cover = _leaflet_cover_proposal(connection, data, media_dir)
        if cover:
            data["cover"] = cover["url"]
            transformations.append({"rule": "leaflet_pdf_cover", "label": "Обложка будет создана из первой страницы PDF", **cover})
        else:
            editorial.append({
                "code": "leaflet_cover_requires_editor",
                "field_path": "cover",
                "message": "PDF выпуска недоступен для однозначного создания обложки.",
            })

    for name in sorted(SERVICE_METADATA_FIELDS & set(data)):
        data.pop(name, None)
    if SERVICE_METADATA_FIELDS & set(before["data"]):
        transformations.append({
            "rule": "provenance_to_journal",
            "label": "Служебные crawl-поля перенесены в журнал",
            "field": "data",
            "evidence": {"removed_fields": sorted(SERVICE_METADATA_FIELDS & set(before["data"]))},
        })

    # Generated summaries, inferred dates, alt text and media paths must pass
    # the same final canonicalization as imported values.  This second pass is
    # deliberately after every rule so an auto-ready proposal cannot retain a
    # warning introduced by a generated value.
    final_proposed = _normalize_tree(proposed)
    if final_proposed != proposed:
        transformations.append({
            "rule": "final_unicode_nfkc",
            "label": "Сгенерированные значения нормализованы после всех преобразований",
            "field": "content",
            "evidence": {"normalization": "NFKC", "control_characters_removed": True},
        })
    proposed = final_proposed
    data = proposed["data"]

    resolved_codes = {
        "duplicate_media_reference", "migration_provenance", "unicode_not_nfkc",
    }
    if any(item["rule"] == "legacy_text_to_blocks" for item in transformations):
        resolved_codes.update({"legacy_text", "legacy_navigation_text"})
    if any(item["code"] == "text_extraction_ambiguous" for item in editorial):
        resolved_codes.add("legacy_text")
    if any(item["rule"] == "date_from_evidence" for item in transformations):
        resolved_codes.add("fallback_date_2000")
    if any(item["code"] == "date_requires_editor" for item in editorial):
        resolved_codes.add("fallback_date_2000")

    required_missing = []
    for name, field in fields.items():
        if not field.get("required"):
            continue
        value = proposed["title"] if name == "title" else data.get(name)
        if value in (None, "", [], {}):
            required_missing.append(name)
    for field_name in required_missing:
        editorial.append({
            "code": "required_field_requires_editor",
            "field_path": field_name,
            "message": f"Обязательное поле «{field_name}» нельзя достоверно восстановить автоматически.",
        })
    # The audit-level structural code is either resolved by a present value or
    # translated to a field-specific editorial reason above.
    resolved_codes.add("required_field_missing")

    for issue in issues:
        if issue["code"] in resolved_codes:
            continue
        reason = {
            "code": issue["code"],
            "field_path": issue["field_path"],
            "message": issue["message"],
        }
        if issue["code"] in TECHNICAL_CODES:
            technical.append(reason)
        elif issue["code"] in EDITORIAL_CODES:
            editorial.append(reason)
        else:
            technical.append({
                **reason,
                "code": "unmapped_audit_issue",
                "evidence": {"source_code": issue["code"], "severity": issue["severity"]},
            })

    editorial = list({(item["code"], item.get("field_path", ""), item["message"]): item for item in editorial}.values())
    technical = list({(item["code"], item.get("field_path", ""), item["message"]): item for item in technical}.values())
    if technical:
        classification = "technical_blocker"
    elif editorial:
        classification = "editorial_review"
    else:
        classification = "auto_ready"
    recommendation = (
        "Автоматические изменения безопасны; редакционное решение не требуется."
        if classification == "auto_ready"
        else "Проверьте только перечисленные содержательные неоднозначности; технические изменения уже подготовлены."
        if classification == "editorial_review"
        else "Сначала устраните программный или структурный блокер; передавать его редактору нельзя."
    )
    return {
        "content_id": content["id"],
        "content_version": content["version"],
        "classification": classification,
        "before": before,
        "proposed": proposed,
        "before_hash": _hash(before),
        "proposed_hash": _hash(proposed),
        "transformations": transformations,
        "reasons": [*technical, *editorial],
        "recommendation": recommendation,
        "options": _options(editorial) if editorial else [],
        "technical_blocker_count": len(technical),
    }


def queue_preparation(
    database_path: Path,
    *,
    actor_id: str | None,
    mode: str = "preview",
    preview_run_id: str | None = None,
) -> dict[str, Any]:
    if mode not in RUN_MODES:
        raise PreparationError(
            "Неизвестный режим автоматической подготовки", 422, "INVALID_PREPARATION_MODE"
        )
    if mode == "apply" and not preview_run_id:
        raise PreparationError(
            "Для применения укажите завершённый preview", 409, "PREVIEW_REQUIRED"
        )
    run_id = str(uuid.uuid4())
    with transaction(database_path) as connection:
        audit_run_id: str | None = None
        if preview_run_id:
            preview = connection.execute(
                "SELECT * FROM migration_preparation_runs WHERE id=?", (preview_run_id,)
            ).fetchone()
            if not preview:
                raise PreparationError(
                    "Preview автоматической подготовки не найден", 404, "PREVIEW_NOT_FOUND"
                )
            if preview["mode"] != "preview" or preview["status"] != "completed":
                raise PreparationError(
                    "Применить можно только завершённый preview", 409, "PREVIEW_NOT_COMPLETED"
                )
            if int(_loads(preview["counts_json"], {}).get("technical_blocker", 0)):
                raise PreparationError(
                    "Preview содержит технические блокеры", 409, "PREVIEW_NOT_SAFE",
                    {"preview_run_id": preview_run_id},
                )
            existing = connection.execute(
                """SELECT * FROM migration_preparation_runs
                   WHERE mode='apply' AND preview_run_id=? AND status IN ('queued','running','completed')
                   ORDER BY created_at DESC LIMIT 1""",
                (preview_run_id,),
            ).fetchone()
            if existing:
                result = _serialize_run(existing)
                result["deduplicated"] = True
                return result
            audit_run_id = preview["audit_run_id"]
            identity_hash = _hash({
                "kind": "apply",
                "rules_version": PREPARATION_RULES_VERSION,
                "preview_run_id": preview_run_id,
            })
        else:
            audit = _latest_full_audit(connection, require_fresh=True)
            audit_run_id = audit["id"]
            content_identity = [
                (row["id"], row["version"])
                for row in connection.execute(
                    """SELECT id,version FROM contents
                       WHERE migration_review_required=1 ORDER BY id"""
                )
            ]
            identity_hash = _hash({
                "kind": "preview",
                "rules_version": PREPARATION_RULES_VERSION,
                "audit_run_id": audit_run_id,
                "contents": content_identity,
            })
        active = connection.execute(
            """SELECT * FROM migration_preparation_runs
               WHERE status IN ('queued','running') ORDER BY created_at,id"""
        ).fetchone()
        if active:
            if active["identity_hash"] == identity_hash:
                result = _serialize_run(active)
                result["deduplicated"] = True
                return result
            raise PreparationError(
                "Уже выполняется другая операция автоматической подготовки",
                409,
                "ACTIVE_RUN_CONFLICT",
                {"active_run_id": active["id"]},
            )
        retry = connection.execute(
            """SELECT id FROM migration_preparation_runs
               WHERE identity_hash=? AND status='failed'
               ORDER BY finished_at DESC,id DESC LIMIT 1""",
            (identity_hash,),
        ).fetchone()
        connection.execute(
            """INSERT INTO migration_preparation_runs(
                 id,rules_version,mode,status,preview_run_id,audit_run_id,counts_json,
                 started_by,created_at,identity_hash,retry_of_run_id,recovered,error_json
               ) VALUES(?,?,?,'queued',?,?,'{}',?,?,?,?,0,NULL)""",
            (
                run_id, PREPARATION_RULES_VERSION, mode, preview_run_id, audit_run_id,
                actor_id, utc_now(), identity_hash, retry["id"] if retry else None,
            ),
        )
    result = get_preparation_run(database_path, run_id)
    result["deduplicated"] = False
    return result


def _serialize_run(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["counts"] = _loads(item.pop("counts_json"), {})
    item["error_detail"] = _loads(item.pop("error_json", None), None)
    item["recovered"] = bool(item.get("recovered"))
    item["contract_version"] = CONTRACT_VERSION
    item.setdefault("deduplicated", False)
    item.setdefault("replayed", False)
    return item


def _serialize_item(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for source, target, default in (
        ("before_json", "before", {}),
        ("proposed_json", "proposed", {}),
        ("transformations_json", "transformations", []),
        ("reasons_json", "reasons", []),
        ("options_json", "options", []),
    ):
        item[target] = _loads(item.pop(source), default)
    item["contract_version"] = CONTRACT_VERSION
    return item


def get_preparation_run(database_path: Path, run_id: str) -> dict[str, Any]:
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM migration_preparation_runs WHERE id=?", (run_id,)
        ).fetchone()
    if not row:
        raise PreparationError("Запуск автоматической подготовки не найден", 404)
    return _serialize_run(row)


def list_preparation_runs(database_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM migration_preparation_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_serialize_run(row) for row in rows]


def list_preparation_items(
    database_path: Path,
    run_id: str,
    *,
    classification: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    if classification and classification not in CLASSIFICATIONS:
        raise PreparationError("Неизвестная классификация материала")
    where = ["pi.run_id=?"]
    params: list[Any] = [run_id]
    if classification:
        where.append("pi.classification=?")
        params.append(classification)
    clause = " AND ".join(where)
    with connect(database_path) as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM migration_preparation_items pi WHERE {clause}", params
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT pi.*,c.title,c.content_type,c.legacy_url,c.version AS current_content_version
                FROM migration_preparation_items pi JOIN contents c ON c.id=pi.content_id
                WHERE {clause}
                ORDER BY CASE pi.classification WHEN 'technical_blocker' THEN 0
                         WHEN 'editorial_review' THEN 1 ELSE 2 END,c.content_type,c.title
                LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
    return {
        "items": [_serialize_item(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _latest_full_audit(
    connection: sqlite3.Connection,
    *,
    require_fresh: bool,
) -> sqlite3.Row:
    for row in connection.execute(
        """SELECT * FROM migration_audit_runs ORDER BY created_at DESC,id DESC"""
    ):
        scope = _loads(row["scope_json"], {})
        if not any(scope.get(key) for key in ("content_id", "content_type", "year")):
            if not require_fresh:
                return row
            if row["status"] in {"queued", "running"}:
                raise PreparationError(
                    "Полный аудит ещё выполняется", 409, "AUDIT_IN_PROGRESS",
                    {"active_run_id": row["id"]},
                )
            if row["status"] == "failed":
                raise PreparationError(
                    "Последний полный аудит завершился ошибкой",
                    409,
                    "LATEST_FULL_AUDIT_FAILED",
                    {"audit_run_id": row["id"]},
                )
            if row["rules_version"] != RULES_VERSION:
                raise PreparationError(
                    "Полный аудит выполнен устаревшей версией правил",
                    409,
                    "AUDIT_STALE",
                    {"audit_run_id": row["id"]},
                )
            missing = connection.execute(
                """SELECT COUNT(*) FROM contents c
                   WHERE c.migration_review_required=1 AND NOT EXISTS (
                     SELECT 1 FROM migration_audit_items ai
                     WHERE ai.run_id=? AND ai.content_id=c.id
                       AND ai.content_version=c.version
                   )""",
                (row["id"],),
            ).fetchone()[0]
            extras = connection.execute(
                """SELECT COUNT(*) FROM migration_audit_items ai
                   LEFT JOIN contents c ON c.id=ai.content_id
                   WHERE ai.run_id=? AND (
                     c.id IS NULL OR c.migration_review_required!=1
                     OR c.version!=ai.content_version
                   )""",
                (row["id"],),
            ).fetchone()[0]
            if missing or extras:
                raise PreparationError(
                    "Результат полного аудита устарел относительно материалов",
                    409,
                    "AUDIT_STALE",
                    {"audit_run_id": row["id"], "missing": missing, "mismatched": extras},
                )
            return row
    raise PreparationError(
        "Сначала выполните полный миграционный аудит", 409, "AUDIT_REQUIRED"
    )


def _store_preview_items(
    database_path: Path,
    run_id: str,
    schema_path: Path,
    media_dir: Path,
) -> dict[str, Any]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    with connect(database_path) as connection:
        audit_run_id = _latest_full_audit(connection, require_fresh=True)["id"]
        rows = connection.execute(
            "SELECT * FROM contents WHERE migration_review_required=1 ORDER BY content_type,title,id"
        ).fetchall()
        proposals = [propose_content(connection, row, schema, media_dir) for row in rows]
    counts = Counter(item["classification"] for item in proposals)
    counts["contents"] = len(proposals)
    counts["changed"] = sum(item["before_hash"] != item["proposed_hash"] for item in proposals)
    now = utc_now()
    with transaction(database_path) as connection:
        connection.execute("DELETE FROM migration_preparation_items WHERE run_id=?", (run_id,))
        for item in proposals:
            connection.execute(
                """INSERT INTO migration_preparation_items(
                     run_id,content_id,content_version,result_version,classification,state,
                     before_json,proposed_json,before_hash,proposed_hash,transformations_json,
                     reasons_json,recommendation,options_json,technical_blocker_count,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, item["content_id"], item["content_version"], None,
                    item["classification"], "planned",
                    _json(item["before"]), _json(item["proposed"]), item["before_hash"],
                    item["proposed_hash"], _json(item["transformations"]), _json(item["reasons"]),
                    item["recommendation"], _json(item["options"]),
                    item["technical_blocker_count"], now,
                ),
            )
        connection.execute(
            """UPDATE migration_preparation_runs SET audit_run_id=?,counts_json=?,
               status='completed',finished_at=? WHERE id=?""",
            (audit_run_id, _json(dict(counts)), now, run_id),
        )
    return get_preparation_run(database_path, run_id)


def _render_leaflet_cover(
    settings: Settings,
    connection: sqlite3.Connection,
    transformation: dict[str, Any],
) -> tuple[Path, bool]:
    stored_name = transformation["stored_name"]
    target = resolve_media_path(settings.media_dir, stored_name)
    existing = connection.execute(
        "SELECT id FROM media WHERE stored_name=?", (stored_name,)
    ).fetchone()
    if target.is_file() and existing:
        return target, False
    pdf_path = resolve_media_path(settings.media_dir, transformation["pdf_stored_name"])
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4()}.tmp")
    document = page = bitmap = None
    try:
        document = pdfium.PdfDocument(str(pdf_path))
        page = document[0]
        width, height = page.get_size()
        scale = max(0.1, min(2.0, 1600 / max(width, height)))
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
        image.thumbnail((1200, 1600))
        image.save(temporary, "WEBP", quality=88, method=6)
        temporary.replace(target)
    finally:
        if bitmap:
            bitmap.close()
        if page:
            page.close()
        if document:
            document.close()
        temporary.unlink(missing_ok=True)
    inspection = inspect_file(target, target.name)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    now = utc_now()
    connection.execute(
        """INSERT INTO media(
             id,original_name,stored_name,mime_type,size_bytes,alt_text,uploaded_by,created_at,
             sha256,kind,source,status,width,height,duration_seconds,version,updated_at,
             replaces_media_id,metadata_json
           ) VALUES(?,?,?,?,?,'',NULL,?,?,?,?,?,?,?,?,1,?,NULL,?)
           ON CONFLICT(stored_name) DO UPDATE SET status='ready',updated_at=excluded.updated_at""",
        (
            transformation["media_id"], target.name, stored_name, inspection.mime_type,
            target.stat().st_size, now, digest, inspection.kind, "migration_preparation",
            "ready", inspection.width, inspection.height, inspection.duration_seconds,
            now, _json({
                "generated_from_media_id": transformation["pdf_media_id"],
                "preparation_rules_version": PREPARATION_RULES_VERSION,
            }),
        ),
    )
    record_media_event(
        connection, transformation["media_id"], None, "migration_prepare",
        {"generated_from_media_id": transformation["pdf_media_id"]},
    )
    return target, True


def _apply_preview(settings: Settings, run_id: str, preview_run_id: str) -> dict[str, Any]:
    with connect(settings.database_path) as connection:
        preview = connection.execute(
            "SELECT * FROM migration_preparation_runs WHERE id=?", (preview_run_id,)
        ).fetchone()
        if not preview or preview["mode"] != "preview" or preview["status"] != "completed":
            raise PreparationError("Завершённый preview не найден", 409)
        items = connection.execute(
            "SELECT * FROM migration_preparation_items WHERE run_id=? ORDER BY content_id",
            (preview_run_id,),
        ).fetchall()
        technical = sum(
            item["classification"] == "technical_blocker"
            or int(item["technical_blocker_count"] or 0) > 0
            for item in items
        )
        if technical:
            raise PreparationError(
                "Preview содержит технические блокеры",
                409,
                "PREVIEW_NOT_SAFE",
                {"preview_run_id": preview_run_id, "technical_blockers": technical},
            )
        stale = []
        stale_hash = []
        for item in items:
            current = connection.execute(
                "SELECT * FROM contents WHERE id=?", (item["content_id"],)
            ).fetchone()
            if not current or current["version"] != item["content_version"]:
                stale.append(item["content_id"])
                continue
            content = admin_content(current)
            snapshot = {
                "content_type": content["content_type"], "slug": content["slug"],
                "title": content["title"], "data": content["data"],
            }
            if _hash(snapshot) != item["before_hash"]:
                stale_hash.append(item["content_id"])
        if stale_hash:
            raise PreparationError(
                f"Preview устарел: изменено содержимое {len(stale_hash)} материалов",
                409,
                "STALE_HASH",
                {"content_ids": stale_hash[:20], "count": len(stale_hash)},
            )
        if stale:
            raise PreparationError(
                f"Preview устарел: изменено материалов {len(stale)}",
                409,
                "STALE_VERSION",
                {"content_ids": stale[:20], "count": len(stale)},
            )

    created_files: list[Path] = []
    now = utc_now()
    try:
        with transaction(settings.database_path) as connection:
            connection.execute(
                "DELETE FROM migration_preparation_items WHERE run_id=?", (run_id,)
            )
            counts = Counter()
            for item in items:
                before = _loads(item["before_json"], {})
                proposed = _loads(item["proposed_json"], {})
                transformations = _loads(item["transformations_json"], [])
                for transformation in transformations:
                    if transformation.get("rule") == "leaflet_pdf_cover":
                        path, created = _render_leaflet_cover(settings, connection, transformation)
                        if created:
                            created_files.append(path)
                        provenance_identity = _hash({
                            "rule": transformation["rule"],
                            "source_media_id": transformation["pdf_media_id"],
                            "media_id": transformation["media_id"],
                        })
                        event_type = "generated" if created else "reused"
                        event_id = str(uuid.uuid5(
                            PREPARATION_NAMESPACE,
                            f"{run_id}:{transformation['media_id']}:{event_type}:{provenance_identity}",
                        ))
                        connection.execute(
                            """INSERT OR IGNORE INTO migration_generated_media_events(
                                 id,preparation_run_id,media_id,source_media_id,event_type,
                                 identity_hash,details_json,created_at
                               ) VALUES(?,?,?,?,?,?,?,?)""",
                            (
                                event_id, run_id, transformation["media_id"],
                                transformation["pdf_media_id"], event_type,
                                provenance_identity,
                                _json({"stored_name": transformation["stored_name"]}),
                                now,
                            ),
                        )
                changed = item["before_hash"] != item["proposed_hash"]
                result_version = item["content_version"]
                state = "unchanged"
                if changed:
                    current = connection.execute(
                        "SELECT * FROM contents WHERE id=?", (item["content_id"],)
                    ).fetchone()
                    result_version += 1
                    connection.execute(
                        """UPDATE contents SET content_type=?,slug=?,title=?,data_json=?,
                           version=?,updated_at=? WHERE id=?""",
                        (
                            proposed["content_type"], proposed["slug"], proposed["title"],
                            _json(proposed["data"]), result_version, now, item["content_id"],
                        ),
                    )
                    snapshot = row_to_content(connection.execute(
                        "SELECT * FROM contents WHERE id=?", (item["content_id"],)
                    ).fetchone())
                    connection.execute(
                        """INSERT INTO revisions(content_id,version,snapshot_json,actor_id,created_at)
                           VALUES(?,?,?,NULL,?)""",
                        (item["content_id"], result_version, _json(snapshot), now),
                    )
                    after = connection.execute(
                        "SELECT * FROM contents WHERE id=?", (item["content_id"],)
                    ).fetchone()
                    record_audit(
                        connection, content_id=item["content_id"], actor_id=None,
                        action="migration_prepare", before=current, after=after,
                        details={"run_id": run_id, "rules_version": PREPARATION_RULES_VERSION},
                    )
                    refresh_content_usages(connection, item["content_id"])
                    sync_content_search(connection, item["content_id"])
                    state = "applied"
                    counts["changed"] += 1
                counts[item["classification"]] += 1
                connection.execute(
                    """INSERT INTO migration_preparation_items(
                         run_id,content_id,content_version,result_version,classification,state,
                         before_json,proposed_json,before_hash,proposed_hash,transformations_json,
                         reasons_json,recommendation,options_json,technical_blocker_count,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id, item["content_id"], item["content_version"], result_version,
                        item["classification"], state, item["before_json"], item["proposed_json"],
                        item["before_hash"], item["proposed_hash"], item["transformations_json"],
                        item["reasons_json"], item["recommendation"], item["options_json"],
                        item["technical_blocker_count"], now,
                    ),
                )
            counts["contents"] = len(items)
            connection.execute(
                """UPDATE migration_preparation_runs SET audit_run_id=?,counts_json=?,
                   status='completed',finished_at=? WHERE id=?""",
                (preview["audit_run_id"], _json(dict(counts)), now, run_id),
            )
    except Exception:
        for path in created_files:
            path.unlink(missing_ok=True)
        raise
    return get_preparation_run(settings.database_path, run_id)


def execute_preparation_run(settings: Settings, run_id: str) -> dict[str, Any]:
    started_here = False
    try:
        with transaction(settings.database_path) as connection:
            run = connection.execute(
                "SELECT * FROM migration_preparation_runs WHERE id=?", (run_id,)
            ).fetchone()
            if not run:
                raise PreparationError("Запуск автоматической подготовки не найден", 404)
            if run["status"] == "completed":
                return _serialize_run(run)
            if run["status"] == "running":
                wait = True
            else:
                wait = False
                started_here = True
                connection.execute(
                    """UPDATE migration_preparation_runs SET status='running',
                       started_at=?,error=NULL WHERE id=?""",
                    (utc_now(), run_id),
                )
        if wait:
            for _ in range(9000):
                current = get_preparation_run(settings.database_path, run_id)
                if current["status"] == "completed":
                    return current
                if current["status"] == "failed":
                    raise PreparationError(current["error"] or "Подготовка завершилась с ошибкой", 409)
                time.sleep(0.1)
            raise PreparationError("Подготовка не завершилась за 15 минут", 409)
        if run["mode"] == "preview":
            return _store_preview_items(
                settings.database_path, run_id, settings.schema_path, settings.media_dir
            )
        return _apply_preview(settings, run_id, run["preview_run_id"])
    except Exception as error:
        if started_here:
            with transaction(settings.database_path) as connection:
                error_payload = {
                    "code": error.code if isinstance(error, PreparationError) else "PREPARATION_FAILED",
                    "message": re.sub(r"\s+", " ", str(error)).strip()[:500],
                    "details": error.details if isinstance(error, PreparationError) else {},
                }
                connection.execute(
                    """UPDATE migration_preparation_runs SET status='failed',error=?,error_json=?,
                       finished_at=? WHERE id=? AND status!='completed'""",
                    (
                        error_payload["message"], _json(error_payload),
                        utc_now(), run_id,
                    ),
                )
        raise


def execute_next_queued_preparation(settings: Settings) -> dict[str, Any] | None:
    with connect(settings.database_path) as connection:
        row = connection.execute(
            """SELECT id FROM migration_preparation_runs
               WHERE status='queued' ORDER BY created_at,id LIMIT 1"""
        ).fetchone()
    return execute_preparation_run(settings, row["id"]) if row else None


def recover_interrupted_preparations(database_path: Path) -> int:
    with transaction(database_path) as connection:
        changed = connection.execute(
            """UPDATE migration_preparation_runs SET status='queued',started_at=NULL,
               recovered=1,recovery_reason='APPLICATION_RESTART',
               error='Выполнение было прервано перезапуском приложения',
               error_json='{"code":"APPLICATION_RESTART","message":"Запуск восстановлен после перезапуска","details":{}}'
               WHERE status='running'"""
        )
        return changed.rowcount


async def preparation_scheduler(settings: Settings, interval_seconds: int = 10) -> None:
    recover_interrupted_preparations(settings.database_path)
    while True:
        try:
            result = await asyncio.to_thread(execute_next_queued_preparation, settings)
            if not result:
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(interval_seconds)


def preparation_summary(database_path: Path) -> dict[str, Any]:
    runs = list_preparation_runs(database_path)
    latest_applied = next(
        (run for run in runs if run["mode"] == "apply" and run["status"] == "completed"),
        None,
    )
    actionable_preview = None
    rollback_target = latest_applied
    with connect(database_path) as connection:
        for run in runs:
            if run["mode"] != "preview" or run["status"] != "completed":
                continue
            consumed = connection.execute(
                """SELECT 1 FROM migration_preparation_runs
                   WHERE mode='apply' AND preview_run_id=?
                     AND status IN ('queued','running','completed','rolled_back')""",
                (run["id"],),
            ).fetchone()
            if not consumed and not int(run["counts"].get("technical_blocker", 0)):
                actionable_preview = run
                break
        gate = _semantic_verification(connection)
    return {
        "contract_version": CONTRACT_VERSION,
        "display_run": runs[0] if runs else None,
        "actionable_preview": actionable_preview,
        "rollback_target": rollback_target,
        "latest_applied": latest_applied,
        "runs": runs,
        "editorial_gate": {
            "ready": bool(gate["ok"]),
            "code": gate["code"],
            "message": gate["message"],
        },
        "counts": latest_applied["counts"] if latest_applied else {
            "contents": 0, "auto_ready": 0, "editorial_review": 0,
            "technical_blocker": 0, "changed": 0,
        },
    }


def rollback_preparation(
    database_path: Path,
    run_id: str,
    *,
    actor_id: str | None,
) -> dict[str, Any]:
    now = utc_now()
    with transaction(database_path) as connection:
        run = connection.execute(
            "SELECT * FROM migration_preparation_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not run:
            raise PreparationError(
                "Запуск автоматической подготовки не найден", 404, "RUN_NOT_FOUND"
            )
        if run["mode"] == "apply" and run["status"] == "rolled_back":
            result = _serialize_run(run)
            result["replayed"] = True
            return result
        if run["mode"] != "apply" or run["status"] != "completed":
            raise PreparationError(
                "Откатить можно только завершённое применение",
                409,
                "ROLLBACK_TARGET_INVALID_STATE",
            )
        latest = connection.execute(
            """SELECT id FROM migration_preparation_runs
               WHERE mode='apply' AND status='completed'
               ORDER BY finished_at DESC,id DESC LIMIT 1"""
        ).fetchone()
        if not latest or latest["id"] != run_id:
            raise PreparationError(
                "Откатить можно только последнее завершённое применение",
                409,
                "ROLLBACK_TARGET_NOT_LATEST",
                {"latest_run_id": latest["id"] if latest else None},
            )
        items = connection.execute(
            """SELECT * FROM migration_preparation_items
               WHERE run_id=? AND state='applied' ORDER BY content_id""",
            (run_id,),
        ).fetchall()
        for item in items:
            current = connection.execute(
                "SELECT * FROM contents WHERE id=?", (item["content_id"],)
            ).fetchone()
            if not current or current["version"] != item["result_version"]:
                raise PreparationError(
                    "Откат остановлен: один из материалов изменён после автоматической подготовки",
                    409,
                    "ROLLBACK_CONFLICT",
                    {"content_id": item["content_id"]},
                )
        for item in items:
            current = connection.execute(
                "SELECT * FROM contents WHERE id=?", (item["content_id"],)
            ).fetchone()
            before = _loads(item["before_json"], {})
            version = current["version"] + 1
            connection.execute(
                """UPDATE contents SET content_type=?,slug=?,title=?,data_json=?,
                   version=?,updated_at=? WHERE id=?""",
                (
                    before["content_type"], before["slug"], before["title"],
                    _json(before["data"]), version, now, item["content_id"],
                ),
            )
            snapshot = row_to_content(connection.execute(
                "SELECT * FROM contents WHERE id=?", (item["content_id"],)
            ).fetchone())
            connection.execute(
                """INSERT INTO revisions(content_id,version,snapshot_json,actor_id,created_at)
                   VALUES(?,?,?,?,?)""",
                (item["content_id"], version, _json(snapshot), actor_id, now),
            )
            after = connection.execute(
                "SELECT * FROM contents WHERE id=?", (item["content_id"],)
            ).fetchone()
            record_audit(
                connection, content_id=item["content_id"], actor_id=actor_id,
                action="migration_prepare_rollback", before=current, after=after,
                details={"run_id": run_id},
            )
            refresh_content_usages(connection, item["content_id"])
            sync_content_search(connection, item["content_id"])
        for item in items:
            transformations = _loads(item["transformations_json"], [])
            for transformation in transformations:
                if transformation.get("rule") != "leaflet_pdf_cover":
                    continue
                provenance_identity = _hash({
                    "rule": transformation["rule"],
                    "source_media_id": transformation["pdf_media_id"],
                    "media_id": transformation["media_id"],
                })
                event_id = str(uuid.uuid5(
                    PREPARATION_NAMESPACE,
                    f"{run_id}:{transformation['media_id']}:retained_on_rollback:{provenance_identity}",
                ))
                connection.execute(
                    """INSERT OR IGNORE INTO migration_generated_media_events(
                         id,preparation_run_id,media_id,source_media_id,event_type,
                         identity_hash,details_json,created_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        event_id, run_id, transformation["media_id"],
                        transformation["pdf_media_id"], "retained_on_rollback",
                        provenance_identity,
                        _json({"policy": "retained-cache"}), now,
                    ),
                )
        connection.execute(
            "UPDATE migration_preparation_items SET state='rolled_back' WHERE run_id=? AND state='applied'",
            (run_id,),
        )
        connection.execute(
            """UPDATE migration_preparation_runs SET status='rolled_back',
               rolled_back_at=? WHERE id=?""",
            (now, run_id),
        )
    result = get_preparation_run(database_path, run_id)
    result["replayed"] = False
    return result


def _semantic_verification(connection: sqlite3.Connection) -> dict[str, Any]:
    latest = connection.execute(
        """SELECT * FROM migration_preparation_runs
           WHERE mode='apply' AND status='completed'
           ORDER BY finished_at DESC,id DESC LIMIT 1"""
    ).fetchone()
    if not latest:
        return {"ok": False, "code": "APPLY_REQUIRED", "message": "Нет завершённого применения"}
    post_audit: sqlite3.Row | None = None
    for candidate in connection.execute(
        """SELECT * FROM migration_audit_runs ORDER BY created_at DESC,id DESC"""
    ):
        scope = _loads(candidate["scope_json"], {})
        if (
            scope.get("after_preparation_run_id") == latest["id"]
            and not any(scope.get(key) for key in ("content_id", "content_type", "year"))
        ):
            post_audit = candidate
            break
    if not post_audit:
        return {
            "ok": False, "code": "POST_APPLY_AUDIT_REQUIRED",
            "message": "После применения требуется полный аудит",
            "run_id": latest["id"],
        }
    if post_audit["status"] != "completed":
        code = (
            "AUDIT_IN_PROGRESS"
            if post_audit["status"] in {"queued", "running"}
            else "LATEST_FULL_AUDIT_FAILED"
        )
        return {
            "ok": False, "code": code,
            "message": "Последний полный post-apply аудит не завершён успешно",
            "run_id": latest["id"], "audit_run_id": post_audit["id"],
        }
    missing = connection.execute(
        """SELECT COUNT(*) FROM contents c
           WHERE c.migration_review_required=1 AND (
             NOT EXISTS (
               SELECT 1 FROM migration_preparation_items pi
               WHERE pi.run_id=? AND pi.content_id=c.id
             ) OR NOT EXISTS (
               SELECT 1 FROM migration_audit_items ai
               WHERE ai.run_id=? AND ai.content_id=c.id AND ai.content_version=c.version
             )
           )""",
        (latest["id"], post_audit["id"]),
    ).fetchone()[0]
    technical_items = connection.execute(
        """SELECT COUNT(*) FROM migration_preparation_items
           WHERE run_id=? AND classification='technical_blocker'""",
        (latest["id"],),
    ).fetchone()[0]
    semantic_failures: list[dict[str, Any]] = []
    editorial_map = {
        "duplicate_content": "duplicate_content",
        "duplicate_title": "duplicate_title",
        "external_link_unavailable": "external_link_unavailable",
        "missing_legacy_media": "missing_legacy_media",
        "unpublished_internal_link": "unpublished_internal_link",
        "fallback_date_2000": "date_requires_editor",
        "required_field_missing": "required_field_requires_editor",
        "legacy_text": "text_extraction_ambiguous",
    }
    for row in connection.execute(
        """SELECT i.content_id,i.code,i.severity,i.field_path,pi.classification,
                  pi.reasons_json
           FROM migration_review_issues i
           LEFT JOIN migration_preparation_items pi
             ON pi.run_id=? AND pi.content_id=i.content_id
           WHERE i.audit_run_id=? AND i.status IN ('open','acknowledged')
             AND i.severity IN ('blocker','warning')
           ORDER BY i.content_id,i.code,i.field_path""",
        (latest["id"], post_audit["id"]),
    ):
        reasons = {
            reason.get("code")
            for reason in _loads(row["reasons_json"], [])
            if isinstance(reason, dict)
        }
        expected = editorial_map.get(row["code"])
        if (
            row["classification"] != "editorial_review"
            or not expected
            or expected not in reasons
        ):
            semantic_failures.append({
                "content_id": row["content_id"],
                "code": row["code"],
                "field_path": row["field_path"],
            })
    unicode_failures = 0
    for row in connection.execute(
        "SELECT title,data_json FROM contents WHERE migration_review_required=1"
    ):
        value = {"title": row["title"], "data": _loads(row["data_json"], {})}
        if _normalize_tree(value) != value:
            unicode_failures += 1
    ok = not (
        missing or technical_items or semantic_failures or unicode_failures
    )
    return {
        "ok": ok,
        "code": "OK" if ok else "SEMANTIC_VERIFY_FAILED",
        "message": (
            "Семантический шлюз пройден"
            if ok else "После применения остались немаппированные или технические нарушения"
        ),
        "run_id": latest["id"],
        "audit_run_id": post_audit["id"],
        "missing_or_stale": int(missing),
        "technical_items": int(technical_items),
        "semantic_failure_count": len(semantic_failures),
        "semantic_failures": semantic_failures[:50],
        "unicode_failure_count": unicode_failures,
    }


def verify_preparation(database_path: Path) -> dict[str, Any]:
    with connect(database_path) as connection:
        validate_preparation_schema(connection)
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        result = _semantic_verification(connection)
    if foreign_keys or not result["ok"]:
        raise PreparationError(
            f"Проверка подготовки не пройдена: fk={len(foreign_keys)}, "
            f"code={result['code']}",
            409,
            result["code"],
            {"foreign_keys": len(foreign_keys), **result},
        )
    return {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        "schema": 13,
        "rules_version": PREPARATION_RULES_VERSION,
        **result,
    }


def _run_audit(settings: Settings) -> dict[str, Any]:
    run = queue_audit(
        settings.database_path, actor_id=None, scope={"check_external": True}
    )
    return execute_audit_run(
        settings.database_path, settings.schema_path, settings.media_dir,
        settings.site_dir, run["id"], check_external=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Автоматическая подготовка мигрированных материалов"
    )
    parser.add_argument(
        "command",
        choices=("preview", "apply", "status", "verify", "rollback"),
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument("--preview-run-id")
    parser.add_argument("--run-id")
    parser.add_argument("--actor-id")
    parser.add_argument("--skip-audit", action="store_true")
    try:
        args = parser.parse_args()
        settings = Settings.from_env()
        if args.database:
            settings = Settings(**{**settings.__dict__, "database_path": args.database})
        if args.command == "status":
            result = preparation_summary(settings.database_path)
        elif args.command == "verify":
            result = verify_preparation(settings.database_path)
        elif args.command == "rollback":
            if not args.run_id:
                raise PreparationError(
                    "--run-id is required for rollback", 422, "RUN_ID_REQUIRED"
                )
            result = rollback_preparation(
                settings.database_path, args.run_id, actor_id=args.actor_id
            )
        else:
            if args.command == "preview" and not args.skip_audit:
                _run_audit(settings)
            run = queue_preparation(
                settings.database_path,
                actor_id=args.actor_id,
                mode=args.command,
                preview_run_id=args.preview_run_id,
            )
            result = execute_preparation_run(settings, run["id"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (PreparationError, AcceptanceError) as error:
        payload = {
            "ok": False,
            "contract_version": CONTRACT_VERSION,
            "error": {
                "code": error.code,
                "message": re.sub(r"\s+", " ", str(error)).strip()[:500],
                "details": error.details,
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
