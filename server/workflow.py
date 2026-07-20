from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .db import row_to_content, transaction, utc_now
from .media_library import media_reference_problems, refresh_content_usages
from .search import sync_content_search


LOGGER = logging.getLogger(__name__)
HIDDEN_STATUSES = {"archived", "trash"}
WORKFLOW_FIELDS = {
    "published_slug", "scheduled_at", "reviewed_by", "reviewed_at", "deleted_at",
    "is_public", "has_unpublished_changes",
}


def admin_content(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    content = row_to_content(row) if isinstance(row, sqlite3.Row) else dict(row)
    is_public = bool(content.get("published_version")) and content.get("status") not in HIDDEN_STATUSES
    content["is_public"] = is_public
    content["has_unpublished_changes"] = bool(
        is_public and content.get("version") != content.get("published_version")
    )
    return content


def public_content(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    published_version = row["published_version"]
    revision = connection.execute(
        "SELECT snapshot_json FROM revisions WHERE content_id=? AND version=?",
        (row["id"], published_version),
    ).fetchone()
    if revision is None:
        raise RuntimeError(f"Published revision {row['id']} v{published_version} is missing")
    content = json.loads(revision["snapshot_json"])
    for field in WORKFLOW_FIELDS:
        content.pop(field, None)
    content.update({
        "id": row["id"],
        "content_type": row["content_type"],
        "slug": row["published_slug"],
        "status": "published",
        "version": published_version,
        "published_version": published_version,
        "published_at": row["published_at"],
        "migration_review_required": False,
    })
    return content


def record_audit(
    connection: sqlite3.Connection,
    *,
    content_id: str,
    actor_id: str | None,
    action: str,
    before: sqlite3.Row | dict[str, Any] | None,
    after: sqlite3.Row | dict[str, Any],
    details: dict[str, Any] | None = None,
) -> None:
    before_status = before["status"] if before is not None else None
    connection.execute(
        """INSERT INTO audit_events(
             id,content_id,actor_id,action,from_status,to_status,content_version,
             published_version,details_json,created_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()),
            content_id,
            actor_id,
            action,
            before_status,
            after["status"],
            after["version"],
            after["published_version"],
            json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
            datetime.now(UTC).isoformat(timespec="microseconds"),
        ),
    )


def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Datetime must include a timezone")
    return value.astimezone(UTC)


def publish_due_content(
    database_path: Path,
    *,
    now: datetime | None = None,
    media_dir: Path | None = None,
) -> list[str]:
    instant = normalize_utc(now or datetime.now(UTC))
    instant_text = instant.isoformat(timespec="seconds")
    published: list[str] = []
    with transaction(database_path) as connection:
        rows = connection.execute(
            """SELECT * FROM contents
               WHERE status='scheduled' AND scheduled_at IS NOT NULL AND scheduled_at <= ?
               ORDER BY scheduled_at,id""",
            (instant_text,),
        ).fetchall()
        for before in rows:
            if media_dir is not None:
                problems = media_reference_problems(connection, admin_content(before), media_dir)
                if problems:
                    connection.execute(
                        """UPDATE contents SET status='in_review',scheduled_at=NULL,updated_at=?
                           WHERE id=? AND status='scheduled' AND version=?""",
                        (instant_text, before["id"], before["version"]),
                    )
                    after = connection.execute("SELECT * FROM contents WHERE id=?", (before["id"],)).fetchone()
                    record_audit(
                        connection,
                        content_id=before["id"],
                        actor_id=None,
                        action="schedule_cancelled_media_error",
                        before=before,
                        after=after,
                        details={"media_errors": len(problems)},
                    )
                    continue
            changed = connection.execute(
                """UPDATE contents
                   SET status='published',published_version=version,
                       published_slug=COALESCE(published_slug,slug),published_at=?,scheduled_at=NULL,
                       updated_at=?
                   WHERE id=? AND status='scheduled' AND version=? AND scheduled_at=?""",
                (instant_text, instant_text, before["id"], before["version"], before["scheduled_at"]),
            )
            if changed.rowcount != 1:
                continue
            after = connection.execute("SELECT * FROM contents WHERE id=?", (before["id"],)).fetchone()
            refresh_content_usages(connection, before["id"])
            sync_content_search(connection, before["id"])
            record_audit(
                connection,
                content_id=before["id"],
                actor_id=None,
                action="scheduled_publish",
                before=before,
                after=after,
                details={"scheduled_at": before["scheduled_at"]},
            )
            published.append(before["id"])
    return published


async def publication_scheduler(
    database_path: Path,
    *,
    media_dir: Path | None = None,
    interval_seconds: int = 60,
) -> None:
    while True:
        try:
            published = publish_due_content(database_path, media_dir=media_dir)
            if published:
                LOGGER.info("Published %d scheduled contents", len(published))
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Scheduled publication pass failed; it will be retried")
        await asyncio.sleep(interval_seconds)
