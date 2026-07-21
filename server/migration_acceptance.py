from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from .config import Settings
from .db import connect, row_to_content, transaction, utc_now
from .media_library import media_reference_problems, refresh_content_usages
from .public_urls import content_path
from .search import sync_content_search
from .workflow import admin_content, record_audit


RULES_VERSION = "1.0.0"
SEVERITIES = ("blocker", "warning", "info")
DISPOSITIONS = ("pending", "accept", "archive", "trash")
INDIVIDUAL_WARNING_CODES = {
    "duplicate_content",
    "duplicate_media_reference",
    "duplicate_title",
    "external_link_unavailable",
    "missing_legacy_media",
    "unpublished_internal_link",
}
STATIC_PUBLIC_PATHS = {
    "/", "/schedule", "/about", "/parish", "/school", "/news", "/gallery",
    "/leaflet", "/media", "/search", "/sitemap.xml", "/robots.txt", "/rss.xml",
}
SERVICE_METADATA_FIELDS = {
    "body_text", "headings", "legacy_images", "legacy_documents", "migration_note", "new_path",
}
LINK_KEYS = {"href", "url", "external_url", "target_url", "link"}
MEDIA_KEYS = {"image", "cover", "photo", "pdf", "file"}
HTML_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
MENU_MARKERS = (
    "главное меню", "версия для печати", "вы здесь", "перейти к содержимому",
)


ACCEPTANCE_SCHEMA_SQL = """
CREATE TABLE migration_audit_runs (
  id TEXT PRIMARY KEY,
  rules_version TEXT NOT NULL,
  scope_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed')),
  counts_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  started_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX idx_migration_audit_runs_status ON migration_audit_runs(status, created_at);
CREATE TABLE migration_audit_items (
  run_id TEXT NOT NULL REFERENCES migration_audit_runs(id) ON DELETE CASCADE,
  content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
  content_version INTEGER NOT NULL,
  blocker_count INTEGER NOT NULL DEFAULT 0,
  warning_count INTEGER NOT NULL DEFAULT 0,
  info_count INTEGER NOT NULL DEFAULT 0,
  scanned_at TEXT NOT NULL,
  PRIMARY KEY(run_id, content_id)
);
CREATE INDEX idx_migration_audit_items_content ON migration_audit_items(content_id, content_version, scanned_at DESC);
CREATE TABLE migration_review_issues (
  id TEXT PRIMARY KEY,
  audit_run_id TEXT NOT NULL REFERENCES migration_audit_runs(id) ON DELETE CASCADE,
  content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
  content_version INTEGER NOT NULL,
  code TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('blocker','warning','info')),
  field_path TEXT NOT NULL DEFAULT '',
  fingerprint TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved','acknowledged','superseded')),
  message TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  detected_at TEXT NOT NULL,
  resolved_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  resolved_at TEXT,
  resolution_note TEXT,
  UNIQUE(content_id, content_version, fingerprint)
);
CREATE INDEX idx_migration_review_issues_queue ON migration_review_issues(status, severity, code, content_id);
CREATE INDEX idx_migration_review_issues_content ON migration_review_issues(content_id, content_version, status);
CREATE TABLE migration_review_batches (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('priority','archive')),
  filters_json TEXT NOT NULL DEFAULT '{}',
  sample_seed TEXT NOT NULL,
  sample_rate REAL NOT NULL DEFAULT 0.1 CHECK(sample_rate > 0 AND sample_rate <= 1),
  warning_ack_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','in_review','finalized','cancelled')),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  finalized_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finalized_at TEXT
);
CREATE INDEX idx_migration_review_batches_status ON migration_review_batches(status, updated_at DESC);
CREATE TABLE migration_review_batch_items (
  batch_id TEXT NOT NULL REFERENCES migration_review_batches(id) ON DELETE CASCADE,
  content_id TEXT NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
  content_version INTEGER NOT NULL,
  audit_run_id TEXT NOT NULL REFERENCES migration_audit_runs(id),
  sampled INTEGER NOT NULL DEFAULT 0 CHECK(sampled IN (0,1)),
  manual_reviewed INTEGER NOT NULL DEFAULT 0 CHECK(manual_reviewed IN (0,1)),
  disposition TEXT NOT NULL DEFAULT 'pending' CHECK(disposition IN ('pending','accept','archive','trash')),
  warning_ack_json TEXT NOT NULL DEFAULT '{}',
  note TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  reviewed_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at TEXT,
  decision_by TEXT REFERENCES users(id) ON DELETE SET NULL,
  decision_at TEXT,
  PRIMARY KEY(batch_id, content_id)
);
CREATE INDEX idx_migration_review_batch_items_content ON migration_review_batch_items(content_id, batch_id);
CREATE INDEX idx_migration_review_batch_items_progress ON migration_review_batch_items(batch_id, disposition, sampled, manual_reviewed);
CREATE TABLE migration_review_events (
  id TEXT PRIMARY KEY,
  batch_id TEXT REFERENCES migration_review_batches(id) ON DELETE CASCADE,
  content_id TEXT REFERENCES contents(id) ON DELETE SET NULL,
  actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
  action TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_migration_review_events_batch ON migration_review_events(batch_id, created_at DESC);
CREATE TABLE migration_link_cache (
  url_hash TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  ok INTEGER NOT NULL CHECK(ok IN (0,1)),
  status_code INTEGER,
  error TEXT,
  checked_at TEXT NOT NULL
);
CREATE INDEX idx_migration_link_cache_checked ON migration_link_cache(checked_at);
"""


ACCEPTANCE_REQUIRED_COLUMNS = {
    "migration_audit_runs": {
        "id", "rules_version", "scope_json", "status", "counts_json", "error",
        "started_by", "created_at", "started_at", "finished_at",
    },
    "migration_audit_items": {
        "run_id", "content_id", "content_version", "blocker_count", "warning_count",
        "info_count", "scanned_at",
    },
    "migration_review_issues": {
        "id", "audit_run_id", "content_id", "content_version", "code", "severity",
        "field_path", "fingerprint", "status", "message", "details_json", "detected_at",
        "resolved_by", "resolved_at", "resolution_note",
    },
    "migration_review_batches": {
        "id", "name", "kind", "filters_json", "sample_seed", "sample_rate",
        "warning_ack_json", "status", "version", "created_by", "finalized_by",
        "created_at", "updated_at", "finalized_at",
    },
    "migration_review_batch_items": {
        "batch_id", "content_id", "content_version", "audit_run_id", "sampled",
        "manual_reviewed", "disposition", "warning_ack_json", "note", "version",
        "reviewed_by", "reviewed_at", "decision_by", "decision_at",
    },
    "migration_review_events": {
        "id", "batch_id", "content_id", "actor_id", "action", "details_json", "created_at",
    },
    "migration_link_cache": {
        "url_hash", "url", "ok", "status_code", "error", "checked_at",
    },
}


ACCEPTANCE_REQUIRED_INDEXES = {
    "idx_migration_audit_runs_status", "idx_migration_audit_items_content",
    "idx_migration_review_issues_queue", "idx_migration_review_issues_content",
    "idx_migration_review_batches_status", "idx_migration_review_batch_items_content",
    "idx_migration_review_batch_items_progress", "idx_migration_review_events_batch",
    "idx_migration_link_cache_checked",
}


class AcceptanceError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def validate_acceptance_schema(connection: sqlite3.Connection) -> None:
    for table, required in ACCEPTANCE_REQUIRED_COLUMNS.items():
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing = sorted(required - columns)
        if missing:
            raise RuntimeError(f"Acceptance table {table} is incomplete: {', '.join(missing)}")
    indexes = {
        row["name"] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    missing_indexes = sorted(ACCEPTANCE_REQUIRED_INDEXES - indexes)
    if missing_indexes:
        raise RuntimeError("Acceptance indexes are missing: " + ", ".join(missing_indexes))


def apply_acceptance_schema(connection: sqlite3.Connection) -> str:
    preserved_tables = (
        "contents", "revisions", "users", "media", "redirects", "submissions",
        "notification_outbox", "submission_events",
    )
    before = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in preserved_tables
    }
    # sqlite3.executescript() implicitly commits an open transaction.  The
    # migration runner owns one BEGIN IMMEDIATE for the whole migration, so
    # execute the (simple CREATE statements) one by one instead.
    for statement in ACCEPTANCE_SCHEMA_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
    validate_acceptance_schema(connection)
    after = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in preserved_tables
    }
    if before != after:
        raise RuntimeError(f"Acceptance migration changed existing rows: {before} -> {after}")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("Acceptance migration broke foreign keys")
    return "migration acceptance audit and batch schema created"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return default


def _safe_error(error: BaseException) -> str:
    return re.sub(r"\s+", " ", str(error)).strip()[:500] or error.__class__.__name__


def _fingerprint(code: str, field_path: str, details: dict[str, Any]) -> str:
    raw = _json({"code": code, "field_path": field_path, "details": details})
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    field_path: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if severity not in SEVERITIES:
        raise ValueError(f"Unknown issue severity: {severity}")
    safe_details = details or {}
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "field_path": field_path,
        "details": safe_details,
        "fingerprint": _fingerprint(code, field_path, safe_details),
    }


def _walk(value: Any, path: str = "data") -> Iterable[tuple[str, str, str | None]]:
    if isinstance(value, str):
        yield path, value, path.rsplit(".", 1)[-1].split("[")[0]
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _body_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("body") or data.get("biography") or []
    return raw if isinstance(raw, list) else []


def _content_digest(content: dict[str, Any]) -> str:
    data = {
        key: value for key, value in content["data"].items()
        if key not in SERVICE_METADATA_FIELDS
    }
    return hashlib.sha256(_json({"type": content["content_type"], "data": data}).encode()).hexdigest()


def _content_year(content: dict[str, Any]) -> int | None:
    for field in ("publication_date", "event_date", "starts_at"):
        value = content["data"].get(field)
        if isinstance(value, str) and re.match(r"^\d{4}", value):
            return int(value[:4])
    period = str(content["data"].get("period") or "")
    match = re.search(r"(?:19|20)\d{2}", period)
    return int(match.group()) if match else None


def _is_priority_content(content: dict[str, Any]) -> bool:
    year = _content_year(content)
    legacy_url = str(content.get("legacy_url") or "")
    return bool(
        year in {2024, 2025, 2026}
        or content["content_type"] in {"site_contact", "clergy", "parish_section"}
        or (content["content_type"] == "page" and "/o-hrame/" in legacy_url)
    )


def _visible_values(content: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    fields = schema.get("content_types", {}).get(content["content_type"], {}).get("fields", {})
    return {
        name: (content["title"] if name == "title" else content["data"].get(name))
        for name in fields
    }


def _cyclic_internal_edges(
    contents: list[dict[str, Any]], schema: dict[str, Any]
) -> set[tuple[str, str]]:
    path_to_id: dict[str, str] = {}
    links: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for content in contents:
        for slug in (content.get("slug"), content.get("published_slug")):
            if slug:
                path_to_id[content_path(content["content_type"], slug).split("#", 1)[0]] = content["id"]
    for content in contents:
        for _, value, key in _walk(_visible_values(content, schema), "content"):
            if key not in LINK_KEYS or not value.startswith("/") or value.startswith("//"):
                continue
            target = path_to_id.get(urlsplit(value).path.rstrip("/") or "/")
            if target:
                links[content["id"]].append((target, value))

    cyclic: set[tuple[str, str]] = set()
    for source_id, edges in links.items():
        for target_id, url in edges:
            stack = [target_id]
            visited: set[str] = set()
            while stack:
                current = stack.pop()
                if current == source_id:
                    cyclic.add((source_id, url))
                    break
                if current in visited:
                    continue
                visited.add(current)
                stack.extend(candidate for candidate, _ in links.get(current, []))
    return cyclic


def _route_sets(connection: sqlite3.Connection) -> tuple[set[str], set[str], set[str]]:
    known = set(STATIC_PUBLIC_PATHS)
    public = set(STATIC_PUBLIC_PATHS)
    for row in connection.execute(
        "SELECT content_type,slug,published_slug,published_version,status FROM contents"
    ):
        known.add(content_path(row["content_type"], row["slug"]).split("#", 1)[0])
        if row["published_slug"]:
            path = content_path(row["content_type"], row["published_slug"]).split("#", 1)[0]
            known.add(path)
            if row["published_version"] and row["status"] not in {"archived", "trash"}:
                public.add(path)
    redirects = {
        row["old_path"] for row in connection.execute("SELECT old_path FROM redirects")
    }
    return known, public, redirects


def _external_result(url: str) -> tuple[bool, int | None, str | None]:
    last_error: str | None = None
    for _ in range(2):
        request = urllib.request.Request(
            url,
            method="HEAD",
            headers={"User-Agent": "TempleMigrationAcceptance/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return 200 <= response.status < 400, response.status, None
        except urllib.error.HTTPError as error:
            if error.code not in {405, 501}:
                return False, error.code, f"HTTP {error.code}"
            request = urllib.request.Request(
                url,
                method="GET",
                headers={"User-Agent": "TempleMigrationAcceptance/1.0", "Range": "bytes=0-0"},
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return 200 <= response.status < 400, response.status, None
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as fallback:
                last_error = _safe_error(fallback)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = _safe_error(error)
    return False, None, last_error or "Link check failed"


def _cached_external_result(
    connection: sqlite3.Connection,
    url: str,
    checker: Callable[[str], tuple[bool, int | None, str | None]],
) -> tuple[bool, int | None, str | None]:
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    row = connection.execute(
        "SELECT * FROM migration_link_cache WHERE url_hash=?", (url_hash,)
    ).fetchone()
    if row:
        checked = datetime.fromisoformat(row["checked_at"])
        if checked >= datetime.now(UTC) - timedelta(hours=24):
            return bool(row["ok"]), row["status_code"], row["error"]
    ok, status_code, error = checker(url)
    connection.execute(
        """INSERT INTO migration_link_cache(url_hash,url,ok,status_code,error,checked_at)
           VALUES(?,?,?,?,?,?) ON CONFLICT(url_hash) DO UPDATE SET url=excluded.url,
           ok=excluded.ok,status_code=excluded.status_code,error=excluded.error,
           checked_at=excluded.checked_at""",
        (url_hash, url, int(ok), status_code, error, utc_now()),
    )
    connection.commit()
    return ok, status_code, error


def audit_content(
    connection: sqlite3.Connection,
    content: dict[str, Any],
    schema: dict[str, Any],
    media_dir: Path,
    site_dir: Path,
    *,
    duplicate_titles: dict[tuple[str, str], list[str]],
    duplicate_digests: dict[tuple[str, str], list[str]],
    known_paths: set[str],
    public_paths: set[str],
    redirect_paths: set[str],
    cyclic_edges: set[tuple[str, str]],
    check_external: bool,
    external_checker: Callable[[str], tuple[bool, int | None, str | None]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    data = content["data"]
    definition = schema.get("content_types", {}).get(content["content_type"], {})
    fields = definition.get("fields", {})

    for name, field in fields.items():
        value = content["title"] if name == "title" else data.get(name)
        if field.get("required") and _empty(value):
            issues.append(_issue(
                "required_field_missing", "blocker", "Не заполнено обязательное поле",
                field_path=name, details={"field": name},
            ))
        if field.get("type") in {"date", "datetime"} and value:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                issues.append(_issue(
                    "invalid_date", "blocker", "Дата имеет неверный формат",
                    field_path=name, details={"field": name},
                ))
            else:
                if (
                    parsed.year == 2000
                    and content.get("migration_review_required")
                    and content["content_type"] in {"news", "gallery"}
                ):
                    issues.append(_issue(
                        "fallback_date_2000", "blocker",
                        "Техническая дата 2000 года должна быть заменена подтверждённой",
                        field_path=name, details={"field": name, "year": 2000},
                    ))

    visible = _visible_values(content, schema)
    for path, text, _ in _walk(visible, "content"):
        normalized = unicodedata.normalize("NFKC", text)
        if text != normalized:
            issues.append(_issue(
                "unicode_not_nfkc", "warning", "Текст требует Unicode-нормализации",
                field_path=path,
            ))
        if "\ufffd" in text or "Ã" in text or "Ð" in text or "Ñ" in text:
            issues.append(_issue(
                "encoding_suspect", "blocker", "Обнаружены признаки повреждённой кодировки",
                field_path=path,
            ))
        if any(unicodedata.category(char) == "Cc" and char not in "\n\r\t" for char in text):
            issues.append(_issue(
                "control_character", "blocker", "Текст содержит управляющие символы",
                field_path=path,
            ))
        if HTML_RE.search(text):
            issues.append(_issue(
                "html_in_content", "blocker", "В отображаемом тексте обнаружен HTML",
                field_path=path,
            ))
        lowered = text.casefold()
        navigation_hits = sum(marker in lowered for marker in MENU_MARKERS)
        section_hits = sum(
            label in lowered
            for label in ("о храме", "жизнь прихода", "воскресная школа", "контакты")
        )
        if navigation_hits or section_hits >= 4:
            issues.append(_issue(
                "legacy_navigation_text", "blocker",
                "В тексте обнаружены элементы старого меню или служебной навигации",
                field_path=path,
            ))

    legacy_blocks = [
        block for block in _body_blocks(data)
        if isinstance(block, dict) and block.get("type") == "legacy_text"
    ]
    if legacy_blocks or isinstance(data.get("body") or data.get("biography"), str):
        issues.append(_issue(
            "legacy_text", "warning",
            "Импортированный текст нужно просмотреть; преобразование в блоки рекомендуется",
            field_path="body", details={"blocks": max(1, len(legacy_blocks))},
        ))

    service_fields = sorted(SERVICE_METADATA_FIELDS & set(data))
    if service_fields:
        issues.append(_issue(
            "migration_provenance", "info",
            "Сохранены служебные данные read-only crawl, не выводимые на сайте",
            details={"fields": service_fields},
        ))

    media_problems = media_reference_problems(connection, content, media_dir)
    for problem in media_problems:
        issues.append(_issue(
            "media_missing_or_invalid", "blocker", problem["reason"],
            field_path=problem["field"], details={"url": problem["url"]},
        ))

    for name, field in fields.items():
        value = content["title"] if name == "title" else data.get(name)
        if not value or field.get("type") not in {"image", "media", "file"}:
            continue
        if not isinstance(value, str):
            continue
        if value.startswith(("assets/", "/assets/")):
            relative = value.removeprefix("/").removeprefix("assets/")
            if not (site_dir / "assets" / relative).is_file():
                issues.append(_issue(
                    "static_media_missing", "blocker", "Файл отсутствует в публичных assets",
                    field_path=name, details={"url": value},
                ))

    for index, block in enumerate(_body_blocks(data)):
        if not isinstance(block, dict):
            continue
        block_data = block.get("data") if isinstance(block.get("data"), dict) else {}
        if block.get("type") == "image" and block_data.get("image") and not str(block_data.get("alt") or "").strip():
            issues.append(_issue(
                "image_alt_missing", "blocker", "У изображения отсутствует alt-текст",
                field_path=f"body[{index}].data.alt",
            ))
        if block.get("type") == "gallery":
            for photo_index, photo in enumerate(block_data.get("photos") or []):
                if isinstance(photo, dict) and photo.get("image") and not str(photo.get("alt") or "").strip():
                    issues.append(_issue(
                        "image_alt_missing", "blocker", "У фотографии отсутствует alt-текст",
                        field_path=f"body[{index}].data.photos[{photo_index}].alt",
                    ))

    pending_missing = connection.execute(
        """SELECT mi.id FROM missing_media_issues mi
           JOIN missing_media_issue_contents mic ON mic.issue_id=mi.id
           WHERE mic.content_id=? AND mi.status='pending' ORDER BY mi.id""",
        (content["id"],),
    ).fetchall()
    if pending_missing:
        issues.append(_issue(
            "missing_legacy_media", "warning",
            "В исходном материале есть утраченные необязательные файлы",
            details={"count": len(pending_missing), "issue_ids": [row["id"] for row in pending_missing]},
        ))

    media_references = [
        value for _, value, _ in _walk(visible, "content")
        if value.startswith("/media/")
    ]
    repeated_media = sorted(value for value, count in Counter(media_references).items() if count > 1)
    if repeated_media:
        issues.append(_issue(
            "duplicate_media_reference", "warning",
            "Один и тот же медиафайл используется в материале несколько раз",
            details={"urls": repeated_media},
        ))

    title_key = (content["content_type"], re.sub(r"\s+", " ", content["title"].strip()).casefold())
    title_group = duplicate_titles.get(title_key, [])
    if len(title_group) > 1:
        issues.append(_issue(
            "duplicate_title", "warning", "Есть материалы того же типа с таким же заголовком",
            field_path="title", details={"content_ids": sorted(title_group)},
        ))
    digest_key = (content["content_type"], _content_digest(content))
    digest_group = duplicate_digests.get(digest_key, [])
    if len(digest_group) > 1:
        issues.append(_issue(
            "duplicate_content", "warning", "Обнаружен вероятный дубль содержимого",
            details={"content_ids": sorted(digest_group)},
        ))

    seen_links: set[str] = set()
    for path, value, key in _walk(visible, "content"):
        if key not in LINK_KEYS or not value or value in seen_links:
            continue
        seen_links.add(value)
        parsed = urlsplit(value)
        if value.startswith("/") and not value.startswith("//"):
            internal_path = parsed.path.rstrip("/") or "/"
            if internal_path.startswith(("/media/", "/assets/")):
                continue
            if internal_path not in known_paths and internal_path not in redirect_paths:
                issues.append(_issue(
                    "internal_link_missing", "blocker", "Внутренняя ссылка не существует",
                    field_path=path, details={"url": value},
                ))
            elif internal_path not in public_paths and internal_path not in redirect_paths:
                issues.append(_issue(
                    "unpublished_internal_link", "warning",
                    "Внутренняя ссылка ведёт на неопубликованный материал",
                    field_path=path, details={"url": value},
                ))
            if (content["id"], value) in cyclic_edges:
                issues.append(_issue(
                    "internal_link_cycle", "blocker",
                    "Внутренняя ссылка участвует в циклической цепочке материалов",
                    field_path=path, details={"url": value},
                ))
        elif parsed.scheme == "https" and parsed.netloc and check_external:
            ok, status_code, error = _cached_external_result(connection, value, external_checker)
            if not ok:
                issues.append(_issue(
                    "external_link_unavailable", "warning",
                    "Внешняя ссылка не ответила после повторной проверки",
                    field_path=path,
                    details={"url": value, "status_code": status_code, "error": error},
                ))
        elif parsed.scheme not in {"mailto", "tel"}:
            issues.append(_issue(
                "unsafe_link", "blocker", "Ссылка имеет небезопасный или неизвестный формат",
                field_path=path, details={"url": value},
            ))

    deduplicated: dict[str, dict[str, Any]] = {}
    for issue in issues:
        deduplicated.setdefault(issue["fingerprint"], issue)
    return list(deduplicated.values())


def queue_audit(database_path: Path, *, actor_id: str | None, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    now = utc_now()
    with transaction(database_path) as connection:
        connection.execute(
            """INSERT INTO migration_audit_runs(
                 id,rules_version,scope_json,status,counts_json,error,started_by,created_at
               ) VALUES(?,?,?,'queued','{}',NULL,?,?)""",
            (run_id, RULES_VERSION, _json(scope or {}), actor_id, now),
        )
    return get_audit_run(database_path, run_id)


def _scope_rows(connection: sqlite3.Connection, scope: dict[str, Any]) -> list[sqlite3.Row]:
    where = ["migration_review_required=1"]
    params: list[Any] = []
    if scope.get("content_id"):
        where = ["id=?"]
        params.append(scope["content_id"])
    if scope.get("content_type"):
        where.append("content_type=?")
        params.append(scope["content_type"])
    if scope.get("year"):
        term = f'{int(scope["year"]):04d}'
        where.append("data_json LIKE ?")
        params.append(f"%{term}%")
    return connection.execute(
        f"SELECT * FROM contents WHERE {' AND '.join(where)} ORDER BY content_type,title,id",
        params,
    ).fetchall()


def execute_audit_run(
    database_path: Path,
    schema_path: Path,
    media_dir: Path,
    site_dir: Path,
    run_id: str,
    *,
    check_external: bool = True,
    external_checker: Callable[[str], tuple[bool, int | None, str | None]] = _external_result,
) -> dict[str, Any]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    started_here = False
    try:
        with transaction(database_path) as connection:
            run = connection.execute("SELECT * FROM migration_audit_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise AcceptanceError("Запуск аудита не найден", 404)
            if run["status"] == "completed":
                return _serialize_run(run)
            if run["status"] == "running":
                wait_for_existing = True
            else:
                wait_for_existing = False
                connection.execute(
                    "UPDATE migration_audit_runs SET status='running',started_at=?,error=NULL WHERE id=?",
                    (utc_now(), run_id),
                )
                started_here = True

        if wait_for_existing:
            # The API worker and a CLI rollout command may observe the same
            # queued run.  The loser waits for the claimed run instead of
            # incorrectly marking it failed or performing a duplicate scan.
            for _ in range(9000):
                current = get_audit_run(database_path, run_id)
                if current["status"] == "completed":
                    return current
                if current["status"] == "failed":
                    raise AcceptanceError(current.get("error") or "Аудит завершился с ошибкой", 409)
                time.sleep(0.1)
            raise AcceptanceError("Аудит не завершился за 15 минут", 409)

        connection = connect(database_path)
        try:
            run = connection.execute("SELECT * FROM migration_audit_runs WHERE id=?", (run_id,)).fetchone()
            scope = _loads(run["scope_json"], {})
            rows = _scope_rows(connection, scope)
            contents = [admin_content(row) for row in rows]
            all_contents = [
                admin_content(row) for row in connection.execute("SELECT * FROM contents").fetchall()
            ]
            duplicate_titles: dict[tuple[str, str], list[str]] = defaultdict(list)
            duplicate_digests: dict[tuple[str, str], list[str]] = defaultdict(list)
            for content in all_contents:
                duplicate_titles[(
                    content["content_type"],
                    re.sub(r"\s+", " ", content["title"].strip()).casefold(),
                )].append(content["id"])
                duplicate_digests[(content["content_type"], _content_digest(content))].append(content["id"])
            known_paths, public_paths, redirect_paths = _route_sets(connection)
            cyclic_edges = _cyclic_internal_edges(all_contents, schema)
            scanned: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
            for content in contents:
                issues = audit_content(
                    connection, content, schema, media_dir, site_dir,
                    duplicate_titles=duplicate_titles,
                    duplicate_digests=duplicate_digests,
                    known_paths=known_paths,
                    public_paths=public_paths,
                    redirect_paths=redirect_paths,
                    cyclic_edges=cyclic_edges,
                    check_external=check_external,
                    external_checker=external_checker,
                )
                scanned.append((content, issues))
        finally:
            connection.close()

        counts = Counter()
        now = utc_now()
        with transaction(database_path) as connection:
            for content, issues in scanned:
                current = connection.execute("SELECT version FROM contents WHERE id=?", (content["id"],)).fetchone()
                if not current or current["version"] != content["version"]:
                    raise AcceptanceError(
                        f"Материал {content['id']} изменён во время аудита; запустите проверку повторно", 409
                    )
                fingerprints = {issue["fingerprint"] for issue in issues}
                old_rows = connection.execute(
                    """SELECT id,fingerprint,status FROM migration_review_issues
                       WHERE content_id=? AND content_version=?""",
                    (content["id"], content["version"]),
                ).fetchall()
                for old in old_rows:
                    if old["fingerprint"] not in fingerprints and old["status"] in {"open", "acknowledged"}:
                        connection.execute(
                            """UPDATE migration_review_issues SET status='resolved',resolved_at=?,
                               resolution_note='Исправлено при повторном автоматическом аудите'
                               WHERE id=?""",
                            (now, old["id"]),
                        )
                connection.execute(
                    """UPDATE migration_review_issues SET status='superseded',resolved_at=?
                       WHERE content_id=? AND content_version!=? AND status IN ('open','acknowledged')""",
                    (now, content["id"], content["version"]),
                )
                per_content = Counter(issue["severity"] for issue in issues)
                for issue in issues:
                    counts[issue["severity"]] += 1
                    existing = connection.execute(
                        """SELECT id,status FROM migration_review_issues
                           WHERE content_id=? AND content_version=? AND fingerprint=?""",
                        (content["id"], content["version"], issue["fingerprint"]),
                    ).fetchone()
                    issue_id = existing["id"] if existing else str(uuid.uuid4())
                    status = existing["status"] if existing and existing["status"] == "acknowledged" else "open"
                    connection.execute(
                        """INSERT INTO migration_review_issues(
                             id,audit_run_id,content_id,content_version,code,severity,field_path,
                             fingerprint,status,message,details_json,detected_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(content_id,content_version,fingerprint) DO UPDATE SET
                             audit_run_id=excluded.audit_run_id,code=excluded.code,severity=excluded.severity,
                             field_path=excluded.field_path,status=?,message=excluded.message,
                             details_json=excluded.details_json,detected_at=excluded.detected_at""",
                        (
                            issue_id, run_id, content["id"], content["version"], issue["code"],
                            issue["severity"], issue["field_path"], issue["fingerprint"], status,
                            issue["message"], _json(issue["details"]), now, status,
                        ),
                    )
                connection.execute(
                    """INSERT INTO migration_audit_items(
                         run_id,content_id,content_version,blocker_count,warning_count,info_count,scanned_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        run_id, content["id"], content["version"], per_content["blocker"],
                        per_content["warning"], per_content["info"], now,
                    ),
                )
            counts["contents"] = len(scanned)
            connection.execute(
                """UPDATE migration_audit_runs SET status='completed',counts_json=?,finished_at=?
                   WHERE id=?""",
                (_json(dict(counts)), now, run_id),
            )
        return get_audit_run(database_path, run_id)
    except Exception as error:
        if started_here:
            with transaction(database_path) as connection:
                connection.execute(
                    """UPDATE migration_audit_runs SET status='failed',error=?,finished_at=?
                       WHERE id=? AND status!='completed'""",
                    (_safe_error(error), utc_now(), run_id),
                )
        raise


def execute_next_queued_audit(settings: Settings) -> dict[str, Any] | None:
    with connect(settings.database_path) as connection:
        row = connection.execute(
            "SELECT id,scope_json FROM migration_audit_runs WHERE status='queued' ORDER BY created_at,id LIMIT 1"
        ).fetchone()
    if not row:
        return None
    scope = _loads(row["scope_json"], {})
    return execute_audit_run(
        settings.database_path,
        settings.schema_path,
        settings.media_dir,
        settings.site_dir,
        row["id"],
        check_external=bool(scope.get("check_external", True)),
    )


def recover_interrupted_audits(database_path: Path) -> int:
    with transaction(database_path) as connection:
        changed = connection.execute(
            """UPDATE migration_audit_runs SET status='queued',started_at=NULL,
               error='Выполнение было прервано перезапуском приложения'
               WHERE status='running'"""
        )
        return changed.rowcount


async def acceptance_scheduler(settings: Settings, interval_seconds: int = 10) -> None:
    import asyncio
    import logging

    logger = logging.getLogger(__name__)
    recover_interrupted_audits(settings.database_path)
    while True:
        try:
            await asyncio.to_thread(execute_next_queued_audit, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Migration acceptance audit failed; queued work will continue")
        await asyncio.sleep(interval_seconds)


def _serialize_run(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["scope"] = _loads(item.pop("scope_json"), {})
    item["counts"] = _loads(item.pop("counts_json"), {})
    return item


def get_audit_run(database_path: Path, run_id: str) -> dict[str, Any]:
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM migration_audit_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise AcceptanceError("Запуск аудита не найден", 404)
    return _serialize_run(row)


def list_audit_runs(database_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM migration_audit_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_serialize_run(row) for row in rows]


def list_issues(
    database_path: Path,
    *,
    severity: str | None = None,
    code: str | None = None,
    status: str | None = "open",
    content_type: str | None = None,
    year: int | None = None,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    where = ["i.content_version=c.version"]
    params: list[Any] = []
    if severity:
        if severity not in SEVERITIES:
            raise AcceptanceError("Неизвестный уровень проблемы")
        where.append("i.severity=?")
        params.append(severity)
    if code:
        where.append("i.code=?")
        params.append(code)
    if status:
        where.append("i.status=?")
        params.append(status)
    if content_type:
        where.append("c.content_type=?")
        params.append(content_type)
    if year:
        where.append("c.data_json LIKE ?")
        params.append(f"%{year:04d}%")
    if q.strip():
        where.append("(c.title LIKE ? OR COALESCE(c.legacy_url,'') LIKE ? OR i.message LIKE ? OR i.code LIKE ?)")
        term = f"%{q.strip()}%"
        params.extend((term, term, term, term))
    clause = " AND ".join(where)
    with connect(database_path) as connection:
        total = connection.execute(
            f"""SELECT COUNT(*) FROM migration_review_issues i
                JOIN contents c ON c.id=i.content_id WHERE {clause}""",
            params,
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT i.*,c.title,c.content_type,c.status AS content_status,
                       c.migration_review_required,c.legacy_url,c.updated_at
                FROM migration_review_issues i JOIN contents c ON c.id=i.content_id
                WHERE {clause}
                ORDER BY CASE i.severity WHEN 'blocker' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                         c.title,i.code LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["details"] = _loads(item.pop("details_json"), {})
        item["migration_review_required"] = bool(item["migration_review_required"])
        items.append(item)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _latest_audit_item(connection: sqlite3.Connection, content_id: str, version: int) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT ai.* FROM migration_audit_items ai
           JOIN migration_audit_runs ar ON ar.id=ai.run_id AND ar.status='completed'
           WHERE ai.content_id=? AND ai.content_version=?
           ORDER BY ai.scanned_at DESC,ai.run_id DESC LIMIT 1""",
        (content_id, version),
    ).fetchone()


def _warning_codes(connection: sqlite3.Connection, content_id: str, version: int) -> set[str]:
    return {
        row["code"] for row in connection.execute(
            """SELECT DISTINCT code FROM migration_review_issues
               WHERE content_id=? AND content_version=? AND severity='warning' AND status='open'""",
            (content_id, version),
        )
    }


def _event(
    connection: sqlite3.Connection,
    *,
    batch_id: str | None,
    content_id: str | None,
    actor_id: str | None,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """INSERT INTO migration_review_events(
             id,batch_id,content_id,actor_id,action,details_json,created_at
           ) VALUES(?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), batch_id, content_id, actor_id, action, _json(details or {}), utc_now()),
    )


def create_batch(
    database_path: Path,
    *,
    name: str,
    kind: str,
    content_ids: list[str],
    actor_id: str,
    filters: dict[str, Any] | None = None,
    sample_rate: float = 0.1,
    batch_id: str | None = None,
) -> dict[str, Any]:
    if kind not in {"priority", "archive"}:
        raise AcceptanceError("Неизвестный тип партии")
    if not 1 <= len(content_ids) <= 50 or len(content_ids) != len(set(content_ids)):
        raise AcceptanceError("Партия должна содержать от 1 до 50 уникальных материалов")
    if not 0 < sample_rate <= 1:
        raise AcceptanceError("Размер выборки должен быть больше 0 и не превышать 100%")
    batch_id = batch_id or str(uuid.uuid4())
    seed = hashlib.sha256(f"stage10:{batch_id}".encode()).hexdigest()
    now = utc_now()
    with transaction(database_path) as connection:
        rows = connection.execute(
            f"SELECT * FROM contents WHERE id IN ({','.join('?' for _ in content_ids)})",
            content_ids,
        ).fetchall()
        by_id = {row["id"]: row for row in rows}
        content_objects = {content_id: admin_content(row) for content_id, row in by_id.items()}
        missing = [content_id for content_id in content_ids if content_id not in by_id]
        if missing:
            raise AcceptanceError("Материалы не найдены: " + ", ".join(missing), 404)
        if any(not by_id[content_id]["migration_review_required"] for content_id in content_ids):
            raise AcceptanceError("В партию можно добавлять только материалы, требующие миграционной приёмки", 409)
        active_items = connection.execute(
            f"""SELECT bi.content_id,b.name FROM migration_review_batch_items bi
                JOIN migration_review_batches b ON b.id=bi.batch_id
                WHERE bi.content_id IN ({','.join('?' for _ in content_ids)})
                  AND b.status IN ('draft','in_review') LIMIT 1""",
            content_ids,
        ).fetchone()
        if active_items:
            raise AcceptanceError(
                f"Материал уже включён в активную партию «{active_items['name']}»", 409
            )
        if kind == "archive":
            groups = {
                (content["content_type"], _content_year(content))
                for content in content_objects.values()
            }
            if len(groups) != 1:
                raise AcceptanceError("Архивная партия должна объединять один тип материалов и один год")
        audit_items: dict[str, sqlite3.Row] = {}
        warning_codes: dict[str, set[str]] = {}
        for content_id in content_ids:
            item = _latest_audit_item(connection, content_id, by_id[content_id]["version"])
            if not item:
                raise AcceptanceError(
                    f"Для материала «{by_id[content_id]['title']}» нет актуального завершённого аудита", 409
                )
            audit_items[content_id] = item
            warning_codes[content_id] = _warning_codes(
                connection, content_id, by_id[content_id]["version"]
            )
        connection.execute(
            """INSERT INTO migration_review_batches(
                 id,name,kind,filters_json,sample_seed,sample_rate,warning_ack_json,status,
                 version,created_by,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,'{}','draft',1,?,?,?)""",
            (batch_id, name.strip()[:180], kind, _json(filters or {}), seed, sample_rate, actor_id, now, now),
        )
        sample_size = len(content_ids) if kind == "priority" else max(1, math.ceil(len(content_ids) * sample_rate))
        ordered = sorted(
            content_ids,
            key=lambda content_id: hashlib.sha256(f"{seed}:{content_id}".encode()).hexdigest(),
        )
        sampled_ids = set(ordered[:sample_size])
        for content_id in content_ids:
            if _is_priority_content(content_objects[content_id]):
                sampled_ids.add(content_id)
            if warning_codes[content_id] & INDIVIDUAL_WARNING_CODES:
                sampled_ids.add(content_id)
            row = by_id[content_id]
            connection.execute(
                """INSERT INTO migration_review_batch_items(
                     batch_id,content_id,content_version,audit_run_id,sampled,manual_reviewed,
                     disposition,warning_ack_json,note,version
                   ) VALUES(?,?,?,?,?,0,'pending','{}','',1)""",
                (
                    batch_id, content_id, row["version"], audit_items[content_id]["run_id"],
                    int(content_id in sampled_ids),
                ),
            )
        _event(
            connection, batch_id=batch_id, content_id=None, actor_id=actor_id,
            action="batch_created",
            details={"kind": kind, "items": len(content_ids), "sampled": len(sampled_ids)},
        )
    return get_batch(database_path, batch_id)


def create_pilot_batch(database_path: Path, *, actor_id: str) -> dict[str, Any]:
    pilot_marker = '"pilot":"stage10-core"'
    with connect(database_path) as connection:
        existing = connection.execute(
            "SELECT id FROM migration_review_batches WHERE filters_json LIKE ? ORDER BY created_at LIMIT 1",
            (f"%{pilot_marker}%",),
        ).fetchone()
        if existing:
            return get_batch(database_path, existing["id"])
        rows = connection.execute(
            """SELECT id FROM contents WHERE migration_review_required=1 AND (
                 content_type IN ('site_contact','clergy','parish_section')
                 OR (content_type='page' AND COALESCE(legacy_url,'') LIKE '/o-hrame/%')
               ) ORDER BY
                 CASE content_type WHEN 'site_contact' THEN 0 WHEN 'page' THEN 1
                   WHEN 'clergy' THEN 2 WHEN 'parish_section' THEN 3 ELSE 4 END,
                 title,id LIMIT 50"""
        ).fetchall()
    if not rows:
        raise AcceptanceError("Для пилотной партии нет материалов, требующих приёмки", 409)
    return create_batch(
        database_path,
        name="Пилот: ключевые разделы",
        kind="priority",
        content_ids=[row["id"] for row in rows],
        actor_id=actor_id,
        filters={"pilot": "stage10-core", "sections": ["contacts", "about", "clergy", "parish"]},
        sample_rate=1.0,
    )


def _serialize_batch_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["filters"] = _loads(item.pop("filters_json"), {})
    item["warning_acknowledgements"] = _loads(item.pop("warning_ack_json"), {})
    return item


def _serialize_batch_item(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["sampled"] = bool(item["sampled"])
    item["manual_reviewed"] = bool(item["manual_reviewed"])
    item["migration_review_required"] = bool(item.get("migration_review_required"))
    item["warning_acknowledgements"] = _loads(item.pop("warning_ack_json"), {})
    item["issues"] = _loads(item.pop("issues_json", "[]"), [])
    item["data"] = _loads(item.pop("data_json", "{}"), {})
    return item


def get_batch(database_path: Path, batch_id: str) -> dict[str, Any]:
    with connect(database_path) as connection:
        row = connection.execute(
            """SELECT b.*,u.username AS created_by_username,f.username AS finalized_by_username
               FROM migration_review_batches b
               LEFT JOIN users u ON u.id=b.created_by LEFT JOIN users f ON f.id=b.finalized_by
               WHERE b.id=?""",
            (batch_id,),
        ).fetchone()
        if not row:
            raise AcceptanceError("Партия не найдена", 404)
        items = connection.execute(
            """SELECT bi.*,c.title,c.content_type,c.status AS content_status,c.slug,c.legacy_url,
                      c.data_json,
                      c.version AS current_content_version,c.migration_review_required,
                      ai.blocker_count,ai.warning_count,ai.info_count,
                      COALESCE((SELECT json_group_array(json_object(
                        'id',ri.id,'code',ri.code,'severity',ri.severity,'field_path',ri.field_path,
                        'message',ri.message,'status',ri.status,'details',json(ri.details_json)
                      )) FROM migration_review_issues ri
                      WHERE ri.content_id=bi.content_id AND ri.content_version=bi.content_version
                        AND ri.status IN ('open','acknowledged')),'[]') AS issues_json
               FROM migration_review_batch_items bi
               JOIN contents c ON c.id=bi.content_id
               JOIN migration_audit_items ai ON ai.run_id=bi.audit_run_id AND ai.content_id=bi.content_id
               WHERE bi.batch_id=?
               ORDER BY bi.sampled DESC,c.content_type,c.title,c.id""",
            (batch_id,),
        ).fetchall()
        events = connection.execute(
            """SELECT e.*,u.username AS actor_username FROM migration_review_events e
               LEFT JOIN users u ON u.id=e.actor_id WHERE e.batch_id=?
               ORDER BY e.created_at DESC LIMIT 100""",
            (batch_id,),
        ).fetchall()
    result = _serialize_batch_row(row)
    result["items"] = [_serialize_batch_item(item) for item in items]
    result["events"] = [
        {**dict(event), "details": _loads(event["details_json"], {})}
        for event in events
    ]
    for event in result["events"]:
        event.pop("details_json", None)
    result["progress"] = {
        "items": len(result["items"]),
        "decided": sum(item["disposition"] != "pending" for item in result["items"]),
        "sampled": sum(item["sampled"] for item in result["items"]),
        "reviewed": sum(item["manual_reviewed"] for item in result["items"] if item["sampled"]),
        "blockers": sum(item["blocker_count"] for item in result["items"]),
        "warnings": sum(item["warning_count"] for item in result["items"]),
    }
    return result


def list_batches(database_path: Path, *, status: str | None = None) -> dict[str, Any]:
    where = "WHERE b.status=?" if status else ""
    params = [status] if status else []
    with connect(database_path) as connection:
        rows = connection.execute(
            f"""SELECT b.*,u.username AS created_by_username,
                       COUNT(bi.content_id) AS item_count,
                       SUM(CASE WHEN bi.disposition!='pending' THEN 1 ELSE 0 END) AS decided_count,
                       SUM(CASE WHEN bi.sampled=1 THEN 1 ELSE 0 END) AS sampled_count,
                       SUM(CASE WHEN bi.sampled=1 AND bi.manual_reviewed=1 THEN 1 ELSE 0 END) AS reviewed_count
                FROM migration_review_batches b
                LEFT JOIN users u ON u.id=b.created_by
                LEFT JOIN migration_review_batch_items bi ON bi.batch_id=b.id
                {where} GROUP BY b.id ORDER BY b.updated_at DESC""",
            params,
        ).fetchall()
    return {"items": [_serialize_batch_row(row) for row in rows], "total": len(rows)}


def update_batch_item(
    database_path: Path,
    *,
    batch_id: str,
    content_id: str,
    item_version: int,
    actor_id: str,
    manual_reviewed: bool,
    disposition: str,
    warning_acknowledgements: dict[str, str],
    note: str,
) -> dict[str, Any]:
    if disposition not in DISPOSITIONS:
        raise AcceptanceError("Неизвестное решение по материалу")
    note = note.strip()[:2000]
    acknowledgements = {
        str(code)[:100]: str(comment).strip()[:1000]
        for code, comment in warning_acknowledgements.items()
        if str(comment).strip()
    }
    with transaction(database_path) as connection:
        batch = connection.execute("SELECT * FROM migration_review_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise AcceptanceError("Партия не найдена", 404)
        if batch["status"] not in {"draft", "in_review"}:
            raise AcceptanceError("Завершённую или отменённую партию нельзя изменять", 409)
        item = connection.execute(
            "SELECT * FROM migration_review_batch_items WHERE batch_id=? AND content_id=?",
            (batch_id, content_id),
        ).fetchone()
        if not item:
            raise AcceptanceError("Материал не входит в партию", 404)
        if item["version"] != item_version:
            raise AcceptanceError("Решение уже изменено; обновите партию", 409)
        content = connection.execute("SELECT version FROM contents WHERE id=?", (content_id,)).fetchone()
        if not content or content["version"] != item["content_version"]:
            raise AcceptanceError("Материал изменён после аудита; запустите аудит и создайте новую партию", 409)
        if disposition in {"archive", "trash"} and not note:
            raise AcceptanceError("Для архива или корзины укажите редакционную причину")
        now = utc_now()
        connection.execute(
            """UPDATE migration_review_batch_items SET manual_reviewed=?,disposition=?,
               warning_ack_json=?,note=?,version=version+1,reviewed_by=?,reviewed_at=?,
               decision_by=?,decision_at=? WHERE batch_id=? AND content_id=?""",
            (
                int(manual_reviewed), disposition, _json(acknowledgements), note,
                actor_id if manual_reviewed else None, now if manual_reviewed else None,
                actor_id if disposition != "pending" else None,
                now if disposition != "pending" else None,
                batch_id, content_id,
            ),
        )
        connection.execute(
            "UPDATE migration_review_batches SET version=version+1,updated_at=? WHERE id=?",
            (now, batch_id),
        )
        _event(
            connection, batch_id=batch_id, content_id=content_id, actor_id=actor_id,
            action="item_updated",
            details={"manual_reviewed": manual_reviewed, "disposition": disposition,
                     "acknowledged_codes": sorted(acknowledgements)},
        )
    return get_batch(database_path, batch_id)


def submit_batch(database_path: Path, *, batch_id: str, version: int, actor_id: str) -> dict[str, Any]:
    with transaction(database_path) as connection:
        batch = connection.execute("SELECT * FROM migration_review_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise AcceptanceError("Партия не найдена", 404)
        if batch["version"] != version:
            raise AcceptanceError("Партия уже изменена; обновите страницу", 409)
        if batch["status"] != "draft":
            raise AcceptanceError("Отправить можно только черновик партии", 409)
        pending = connection.execute(
            "SELECT COUNT(*) FROM migration_review_batch_items WHERE batch_id=? AND disposition='pending'",
            (batch_id,),
        ).fetchone()[0]
        if pending:
            raise AcceptanceError("Укажите решение для каждого материала", 409)
        now = utc_now()
        connection.execute(
            "UPDATE migration_review_batches SET status='in_review',version=version+1,updated_at=? WHERE id=?",
            (now, batch_id),
        )
        _event(connection, batch_id=batch_id, content_id=None, actor_id=actor_id, action="batch_submitted")
    return get_batch(database_path, batch_id)


def finalize_batch(
    database_path: Path,
    *,
    batch_id: str,
    version: int,
    actor_id: str,
    warning_acknowledgements: dict[str, str],
) -> dict[str, Any]:
    batch_ack = {
        str(code)[:100]: str(comment).strip()[:1000]
        for code, comment in warning_acknowledgements.items()
        if str(comment).strip()
    }
    with transaction(database_path) as connection:
        batch = connection.execute("SELECT * FROM migration_review_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise AcceptanceError("Партия не найдена", 404)
        if batch["version"] != version:
            raise AcceptanceError("Партия уже изменена; обновите страницу", 409)
        if batch["status"] != "in_review":
            raise AcceptanceError("Финализировать можно только отправленную партию", 409)
        items = connection.execute(
            """SELECT bi.*,c.version AS current_content_version,c.title,
                      c.status AS current_status,c.migration_review_required
               FROM migration_review_batch_items bi
               JOIN contents c ON c.id=bi.content_id
               WHERE bi.batch_id=? ORDER BY bi.content_id""",
            (batch_id,),
        ).fetchall()
        for item in items:
            if item["content_version"] != item["current_content_version"]:
                raise AcceptanceError(f"Материал «{item['title']}» изменён после аудита", 409)
            if not item["migration_review_required"]:
                raise AcceptanceError(f"Материал «{item['title']}» уже прошёл миграционную приёмку", 409)
            if item["disposition"] == "pending":
                raise AcceptanceError(f"Для материала «{item['title']}» не выбрано решение", 409)
            if item["sampled"] and not item["manual_reviewed"]:
                raise AcceptanceError(f"Материал «{item['title']}» входит в обязательную выборку", 409)
            issues = connection.execute(
                """SELECT code,severity FROM migration_review_issues
                   WHERE content_id=? AND content_version=? AND status='open'""",
                (item["content_id"], item["content_version"]),
            ).fetchall()
            if item["disposition"] == "accept" and any(issue["severity"] == "blocker" for issue in issues):
                raise AcceptanceError(f"Материал «{item['title']}» содержит блокирующие ошибки", 409)
            item_ack = _loads(item["warning_ack_json"], {})
            for issue in issues:
                if issue["severity"] != "warning" or item["disposition"] != "accept":
                    continue
                if issue["code"] in INDIVIDUAL_WARNING_CODES:
                    if not item_ack.get(issue["code"]):
                        raise AcceptanceError(
                            f"Подтвердите предупреждение {issue['code']} для «{item['title']}»", 409
                        )
                elif not batch_ack.get(issue["code"]):
                    raise AcceptanceError(
                        f"Подтвердите предупреждение партии {issue['code']}", 409
                    )

        now = utc_now()
        for item in items:
            before = connection.execute("SELECT * FROM contents WHERE id=?", (item["content_id"],)).fetchone()
            item_ack = _loads(item["warning_ack_json"], {})
            if item["disposition"] == "accept":
                for issue in connection.execute(
                    """SELECT id,code,severity FROM migration_review_issues
                       WHERE content_id=? AND content_version=? AND status='open'""",
                    (item["content_id"], item["content_version"]),
                ).fetchall():
                    note = (
                        item_ack.get(issue["code"])
                        or batch_ack.get(issue["code"])
                        or "Просмотрено при редакционной приёмке"
                    )
                    connection.execute(
                        """UPDATE migration_review_issues SET status='acknowledged',resolved_by=?,
                           resolved_at=?,resolution_note=? WHERE id=?""",
                        (actor_id, now, note, issue["id"]),
                    )
                connection.execute(
                    """UPDATE contents SET migration_review_required=0,status='draft',
                       published_version=NULL,scheduled_at=NULL,reviewed_by=NULL,reviewed_at=NULL,
                       deleted_at=NULL,updated_at=?
                       WHERE id=?""",
                    (now, item["content_id"]),
                )
                action = "migration_accept"
            elif item["disposition"] == "archive":
                connection.execute(
                    """UPDATE migration_review_issues SET status='resolved',resolved_by=?,
                       resolved_at=?,resolution_note=? WHERE content_id=? AND content_version=?
                       AND status IN ('open','acknowledged')""",
                    (actor_id, now, item["note"], item["content_id"], item["content_version"]),
                )
                connection.execute(
                    """UPDATE contents SET migration_review_required=0,status='archived',
                       published_version=NULL,scheduled_at=NULL,reviewed_by=NULL,reviewed_at=NULL,
                       deleted_at=NULL,updated_at=? WHERE id=?""",
                    (now, item["content_id"]),
                )
                action = "migration_archive"
            else:
                connection.execute(
                    """UPDATE migration_review_issues SET status='resolved',resolved_by=?,
                       resolved_at=?,resolution_note=? WHERE content_id=? AND content_version=?
                       AND status IN ('open','acknowledged')""",
                    (actor_id, now, item["note"], item["content_id"], item["content_version"]),
                )
                connection.execute(
                    """UPDATE contents SET migration_review_required=0,status='trash',
                       published_version=NULL,scheduled_at=NULL,reviewed_by=NULL,reviewed_at=NULL,
                       deleted_at=?,updated_at=? WHERE id=?""",
                    (now, now, item["content_id"]),
                )
                action = "migration_trash"
            refresh_content_usages(connection, item["content_id"])
            sync_content_search(connection, item["content_id"])
            after = connection.execute("SELECT * FROM contents WHERE id=?", (item["content_id"],)).fetchone()
            record_audit(
                connection, content_id=item["content_id"], actor_id=actor_id,
                action=action, before=before, after=after,
                details={"batch_id": batch_id, "disposition": item["disposition"]},
            )
        connection.execute(
            """UPDATE migration_review_batches SET status='finalized',warning_ack_json=?,
               version=version+1,finalized_by=?,updated_at=?,finalized_at=? WHERE id=?""",
            (_json(batch_ack), actor_id, now, now, batch_id),
        )
        _event(
            connection, batch_id=batch_id, content_id=None, actor_id=actor_id,
            action="batch_finalized", details={"items": len(items)},
        )
    return get_batch(database_path, batch_id)


def cancel_batch(database_path: Path, *, batch_id: str, version: int, actor_id: str) -> dict[str, Any]:
    with transaction(database_path) as connection:
        batch = connection.execute("SELECT * FROM migration_review_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise AcceptanceError("Партия не найдена", 404)
        if batch["version"] != version:
            raise AcceptanceError("Партия уже изменена; обновите страницу", 409)
        if batch["status"] == "finalized":
            raise AcceptanceError("Финализированную партию нельзя отменить", 409)
        connection.execute(
            "UPDATE migration_review_batches SET status='cancelled',version=version+1,updated_at=? WHERE id=?",
            (utc_now(), batch_id),
        )
        _event(connection, batch_id=batch_id, content_id=None, actor_id=actor_id, action="batch_cancelled")
    return get_batch(database_path, batch_id)


def acceptance_summary(database_path: Path) -> dict[str, Any]:
    with connect(database_path) as connection:
        totals = dict(connection.execute(
            """SELECT COUNT(*) AS contents,
                      SUM(migration_review_required) AS review_required,
                      SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published
               FROM contents"""
        ).fetchone())
        severities = {
            row["severity"]: row["amount"] for row in connection.execute(
                """SELECT i.severity,COUNT(*) AS amount FROM migration_review_issues i
                   JOIN contents c ON c.id=i.content_id AND c.version=i.content_version
                   WHERE i.status='open' GROUP BY i.severity"""
            )
        }
        batches = {
            row["status"]: row["amount"] for row in connection.execute(
                "SELECT status,COUNT(*) AS amount FROM migration_review_batches GROUP BY status"
            )
        }
        by_type = [dict(row) for row in connection.execute(
            """SELECT c.content_type,COUNT(*) AS total,
                      SUM(c.migration_review_required) AS review_required,
                      (SELECT COUNT(*) FROM migration_review_issues i JOIN contents ci ON ci.id=i.content_id
                       WHERE ci.content_type=c.content_type AND i.content_version=ci.version
                         AND i.severity='blocker' AND i.status='open') AS blockers,
                      (SELECT COUNT(*) FROM migration_review_issues i JOIN contents ci ON ci.id=i.content_id
                       WHERE ci.content_type=c.content_type AND i.content_version=ci.version
                         AND i.severity='warning' AND i.status='open') AS warnings
               FROM contents c GROUP BY c.content_type ORDER BY c.content_type"""
        )]
        issue_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for row in connection.execute(
            """SELECT i.content_id,i.severity,COUNT(*) AS amount FROM migration_review_issues i
               JOIN contents c ON c.id=i.content_id AND c.version=i.content_version
               WHERE i.status='open' GROUP BY i.content_id,i.severity"""
        ):
            issue_counts[row["content_id"]][row["severity"]] = int(row["amount"])
        years: dict[str, Counter[str]] = defaultdict(Counter)
        for row in connection.execute("SELECT * FROM contents ORDER BY id"):
            content = admin_content(row)
            key = str(_content_year(content) or "unknown")
            years[key]["total"] += 1
            years[key]["review_required"] += int(content["migration_review_required"])
            years[key]["blockers"] += issue_counts[content["id"]]["blocker"]
            years[key]["warnings"] += issue_counts[content["id"]]["warning"]
        by_year = [
            {"year": key, **dict(counts)}
            for key, counts in sorted(
                years.items(),
                key=lambda item: (
                    item[0] == "unknown",
                    -(int(item[0]) if item[0].isdigit() else 0),
                ),
            )
        ]
    return {
        "totals": totals,
        "issues": {severity: int(severities.get(severity, 0)) for severity in SEVERITIES},
        "batches": batches,
        "by_type": by_type,
        "by_year": by_year,
        "runs": list_audit_runs(database_path),
    }


def verify_acceptance(database_path: Path) -> dict[str, Any]:
    with connect(database_path) as connection:
        validate_acceptance_schema(connection)
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        stale_finalized = connection.execute(
            """SELECT COUNT(*) FROM migration_review_batch_items bi
               JOIN migration_review_batches b ON b.id=bi.batch_id
               JOIN contents c ON c.id=bi.content_id
               WHERE b.status='finalized' AND c.migration_review_required=1"""
        ).fetchone()[0]
        invalid_finalized = connection.execute(
            """SELECT COUNT(*) FROM migration_review_batch_items bi
               JOIN migration_review_batches b ON b.id=bi.batch_id
               WHERE b.status='finalized' AND bi.disposition='pending'"""
        ).fetchone()[0]
    if foreign_keys or stale_finalized or invalid_finalized:
        raise AcceptanceError(
            f"Acceptance verification failed: fk={len(foreign_keys)}, stale={stale_finalized}, pending={invalid_finalized}",
            409,
        )
    return {"ok": True, "schema": 9, "rules_version": RULES_VERSION}


def _run_sync(settings: Settings, scope: dict[str, Any] | None, actor_id: str | None, check_external: bool) -> dict[str, Any]:
    run = queue_audit(settings.database_path, actor_id=actor_id, scope=scope)
    return execute_audit_run(
        settings.database_path, settings.schema_path, settings.media_dir, settings.site_dir,
        run["id"], check_external=check_external,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Редакторская приёмка перенесённых материалов")
    parser.add_argument("command", choices=("scan", "status", "verify", "create-pilot", "report"))
    parser.add_argument("--database", type=Path)
    parser.add_argument("--content-id")
    parser.add_argument("--content-type")
    parser.add_argument("--year", type=int)
    parser.add_argument("--actor-id")
    parser.add_argument("--skip-external", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    if args.database:
        settings = Settings(**{**settings.__dict__, "database_path": args.database})
    if args.command == "scan":
        scope = {key: value for key, value in {
            "content_id": args.content_id, "content_type": args.content_type, "year": args.year,
        }.items() if value is not None}
        result = _run_sync(settings, scope, args.actor_id, not args.skip_external)
    elif args.command in {"status", "report"}:
        result = acceptance_summary(settings.database_path)
    elif args.command == "verify":
        result = verify_acceptance(settings.database_path)
    else:
        if not args.actor_id:
            raise SystemExit("--actor-id is required for create-pilot")
        result = create_pilot_batch(settings.database_path, actor_id=args.actor_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
