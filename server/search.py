from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .db import connect, transaction
from .public_urls import content_path


SEARCHABLE_TYPES = {
    "news",
    "page",
    "parish_section",
    "clergy",
    "gallery",
    "service",
    "leaflet_issue",
    "video",
    "site_contact",
}

TYPE_LABELS = {
    "news": "Новости",
    "page": "Страницы",
    "parish_section": "Жизнь прихода",
    "clergy": "Духовенство",
    "gallery": "Фотогалерея",
    "service": "Богослужения",
    "leaflet_issue": "Иннокентиевский листок",
    "video": "Видео",
    "site_contact": "Контакты",
}

SEARCH_INDEX_SQL = """
CREATE VIRTUAL TABLE content_search USING fts5(
  content_id UNINDEXED,
  content_type UNINDEXED,
  published_version UNINDEXED,
  url UNINDEXED,
  published_at UNINDEXED,
  visible_until UNINDEXED,
  title,
  summary,
  body,
  tokenize='unicode61 remove_diacritics 2'
)
"""

SEARCH_COLUMNS = {
    "content_id", "content_type", "published_version", "url", "published_at",
    "visible_until", "title", "summary", "body",
}

TEXT_DATA_FIELDS = {
    "summary", "note", "body", "body_text", "biography", "category", "kicker",
    "liturgical_title", "location", "period", "rank", "position", "name_day",
    "address", "metro", "opening_hours", "legal_details", "contact_name", "phone",
    "email", "schedule", "photos",
}


class SearchError(ValueError):
    pass


@dataclass(frozen=True)
class SearchPage:
    query: str
    content_type: str | None
    items: list[dict[str, Any]]
    facets: dict[str, int]
    total: int
    page: int
    pages: int
    per_page: int
    invalid_page: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "content_type": self.content_type,
            "items": self.items,
            "facets": self.facets,
            "total": self.total,
            "page": self.page,
            "pages": self.pages,
            "per_page": self.per_page,
        }


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def _plain_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return " ".join(filter(None, (_plain_text(item) for item in value)))
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    for key, item in value.items():
        if key in {
            "id", "url", "image", "cover", "photo", "pdf", "file", "social_image",
            "related_content", "related_section", "target_url", "content_slug", "marks",
        }:
            continue
        if key in {"text", "value", "label", "description", "caption", "alt", "author", "source", "runs", "items", "data"}:
            parts.append(_plain_text(item))
    return " ".join(filter(None, parts))


def _published_snapshot(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    revision = connection.execute(
        "SELECT snapshot_json FROM revisions WHERE content_id=? AND version=?",
        (row["id"], row["published_version"]),
    ).fetchone()
    if revision is None:
        raise SearchError(
            f"Опубликованная ревизия {row['id']} v{row['published_version']} отсутствует"
        )
    snapshot = json.loads(revision["snapshot_json"])
    snapshot.update({
        "id": row["id"],
        "content_type": row["content_type"],
        "slug": row["published_slug"] or snapshot.get("slug") or row["slug"],
        "published_version": row["published_version"],
        "published_at": row["published_at"],
    })
    return snapshot


def public_content_url(snapshot: dict[str, Any]) -> str:
    content_type = str(snapshot.get("content_type") or "")
    data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
    slug = str(snapshot.get("slug") or "")
    if content_type == "page":
        return {
            "about_history": "/about",
            "about_shrine": "/about",
            "school_home": "/school",
            "schedule_info": "/schedule",
        }.get(str(data.get("placement") or "standalone"), content_path(content_type, slug))
    return {
        "service": "/schedule",
        "leaflet_issue": "/leaflet",
        "video": "/media",
        "site_contact": "/about#contacts",
    }.get(content_type, content_path(content_type, slug))


def _search_document(snapshot: dict[str, Any]) -> dict[str, str]:
    data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
    summary = _normalize_text(
        data.get("summary") or data.get("note") or data.get("period")
        or data.get("rank") or data.get("opening_hours") or ""
    )
    body_parts = [_plain_text(data.get(key)) for key in TEXT_DATA_FIELDS if key in data]
    visible_until = ""
    if snapshot.get("content_type") == "service":
        visible_until = _utc_iso(data.get("ends_at") or data.get("starts_at"))
    return {
        "content_id": str(snapshot["id"]),
        "content_type": str(snapshot["content_type"]),
        "published_version": str(snapshot["published_version"]),
        "url": public_content_url(snapshot),
        "published_at": _normalize_text(snapshot.get("published_at")),
        "visible_until": visible_until,
        "title": _normalize_text(snapshot.get("title")),
        "summary": summary,
        "body": _normalize_text(" ".join(body_parts)),
    }


def _utc_iso(value: Any) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return ""
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return normalized
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds")


def validate_search_schema(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='content_search'"
    ).fetchone()
    if row is None or "fts5" not in str(row["sql"] or "").lower():
        raise SearchError("FTS5-таблица content_search отсутствует")
    columns = {item["name"] for item in connection.execute("PRAGMA table_info(content_search)")}
    missing = sorted(SEARCH_COLUMNS - columns)
    if missing:
        raise SearchError("В content_search отсутствуют поля: " + ", ".join(missing))
    connection.execute(
        "SELECT rowid FROM content_search WHERE content_search MATCH ? LIMIT 1", ("schema",)
    ).fetchall()


def searchable_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in SEARCHABLE_TYPES)
    return connection.execute(
        f"""SELECT * FROM contents
              WHERE published_version IS NOT NULL
                AND status NOT IN ('archived','trash')
                AND content_type IN ({placeholders})
              ORDER BY id""",
        tuple(sorted(SEARCHABLE_TYPES)),
    ).fetchall()


def sync_content_search(connection: sqlite3.Connection, content_id: str) -> None:
    connection.execute("DELETE FROM content_search WHERE content_id=?", (content_id,))
    row = connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
    if (
        row is None
        or row["content_type"] not in SEARCHABLE_TYPES
        or row["published_version"] is None
        or row["status"] in {"archived", "trash"}
    ):
        return
    document = _search_document(_published_snapshot(connection, row))
    connection.execute(
        """INSERT INTO content_search(
             content_id,content_type,published_version,url,published_at,visible_until,
             title,summary,body
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        tuple(document[column] for column in (
            "content_id", "content_type", "published_version", "url", "published_at",
            "visible_until", "title", "summary", "body",
        )),
    )


def rebuild_search_index(connection: sqlite3.Connection) -> int:
    validate_search_schema(connection)
    connection.execute("DELETE FROM content_search")
    rows = searchable_rows(connection)
    for row in rows:
        sync_content_search(connection, row["id"])
    indexed = int(connection.execute("SELECT COUNT(*) FROM content_search").fetchone()[0])
    if indexed != len(rows):
        raise SearchError(f"FTS5 backfill неполон: {indexed} вместо {len(rows)}")
    return indexed


def search_index_problems(connection: sqlite3.Connection) -> list[str]:
    validate_search_schema(connection)
    expected = {
        str(row["id"]): int(row["published_version"])
        for row in searchable_rows(connection)
    }
    indexed_rows = connection.execute(
        "SELECT content_id,published_version,COUNT(*) AS amount FROM content_search GROUP BY content_id,published_version"
    ).fetchall()
    indexed = {str(row["content_id"]): int(row["published_version"]) for row in indexed_rows}
    problems: list[str] = []
    duplicates = [str(row["content_id"]) for row in indexed_rows if int(row["amount"]) != 1]
    if duplicates:
        problems.append("duplicate:" + ",".join(sorted(duplicates)))
    missing = sorted(set(expected) - set(indexed))
    extra = sorted(set(indexed) - set(expected))
    stale = sorted(key for key in expected.keys() & indexed.keys() if expected[key] != indexed[key])
    if missing:
        problems.append("missing:" + ",".join(missing))
    if extra:
        problems.append("extra:" + ",".join(extra))
    if stale:
        problems.append("stale:" + ",".join(stale))
    return problems


def reconcile_search_index(database_path: Path) -> int:
    with transaction(database_path) as connection:
        problems = search_index_problems(connection)
        if not problems:
            return 0
        expected_ids = {str(row["id"]) for row in searchable_rows(connection)}
        indexed_ids = {
            str(row["content_id"])
            for row in connection.execute("SELECT content_id FROM content_search")
        }
        for content_id in sorted(expected_ids | indexed_ids):
            sync_content_search(connection, content_id)
        remaining = search_index_problems(connection)
        if remaining:
            raise SearchError("FTS5 reconciliation не завершена: " + "; ".join(remaining))
        return len(expected_ids | indexed_ids)


def normalize_query(value: str) -> tuple[str, list[str]]:
    query = _normalize_text(value)
    if not 2 <= len(query) <= 200:
        raise SearchError("Введите от 2 до 200 символов")
    tokens = [token.casefold() for token in re.findall(r"[^\W_]+", query, flags=re.UNICODE)]
    tokens = list(dict.fromkeys(token for token in tokens if len(token) >= 2))[:12]
    if not tokens:
        raise SearchError("Запрос должен содержать слово длиной не менее двух символов")
    return query, tokens


def _match_expression(tokens: list[str]) -> str:
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)


def search_public(
    database_path: Path,
    query_value: str,
    *,
    content_type: str | None = None,
    page: int = 1,
    per_page: int = 20,
    now: datetime | None = None,
) -> SearchPage:
    query, tokens = normalize_query(query_value)
    if content_type and content_type not in SEARCHABLE_TYPES:
        raise SearchError("Неизвестный тип материала")
    if page < 1 or not 1 <= per_page <= 50:
        raise SearchError("Неверные параметры страницы")
    match = _match_expression(tokens)
    instant = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    visibility = "(content_type!='service' OR visible_until='' OR visible_until>=?)"
    with connect(database_path) as connection:
        params: list[Any] = [match, instant]
        type_sql = ""
        if content_type:
            type_sql = " AND content_type=?"
            params.append(content_type)
        total = int(connection.execute(
            f"SELECT COUNT(*) FROM content_search WHERE content_search MATCH ? AND {visibility}{type_sql}",
            params,
        ).fetchone()[0])
        facet_rows = connection.execute(
            f"""SELECT content_type,COUNT(*) AS amount FROM content_search
                  WHERE content_search MATCH ? AND {visibility}
                  GROUP BY content_type ORDER BY content_type""",
            (match, instant),
        ).fetchall()
        pages = math.ceil(total / per_page) if total else 0
        invalid_page = page > 1 and page > pages
        rows: list[sqlite3.Row] = []
        if not invalid_page:
            rows = connection.execute(
                f"""SELECT content_id,content_type,published_version,url,published_at,title,summary,
                           snippet(content_search,8,'','',' … ',24) AS excerpt,
                           bm25(content_search,0,0,0,0,0,0,10.0,5.0,1.0) AS rank
                      FROM content_search
                     WHERE content_search MATCH ? AND {visibility}{type_sql}
                     ORDER BY rank ASC,published_at DESC,content_id
                     LIMIT ? OFFSET ?""",
                (*params, per_page, (page - 1) * per_page),
            ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item.pop("rank", None)
        item["id"] = item.pop("content_id")
        item["published_version"] = int(item["published_version"])
        item["type_label"] = TYPE_LABELS.get(item["content_type"], item["content_type"])
        item["excerpt"] = _normalize_text(item.get("excerpt") or item.get("summary") or "")
        items.append(item)
    return SearchPage(
        query=query,
        content_type=content_type,
        items=items,
        facets={str(row["content_type"]): int(row["amount"]) for row in facet_rows},
        total=total,
        page=page,
        pages=pages,
        per_page=per_page,
        invalid_page=invalid_page,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверяет и перестраивает публичный FTS5-индекс")
    parser.add_argument("command", choices=("verify", "reindex"))
    parser.add_argument("--database", type=Path)
    args = parser.parse_args()
    database = args.database or Settings.from_env().database_path
    if args.command == "verify":
        with connect(database) as connection:
            problems = search_index_problems(connection)
        if problems:
            raise SystemExit("; ".join(problems))
        print("search index: ok")
        return
    with transaction(database) as connection:
        amount = rebuild_search_index(connection)
    print(f"search index rebuilt: {amount}")


if __name__ == "__main__":
    main()
