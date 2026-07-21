from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .db import connect, transaction, utc_now
from .media_library import index_library


LOGGER = logging.getLogger(__name__)
ACTIVE_STATUSES = ("queued", "running")


class MediaReindexError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400, *, active_job_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.active_job_id = active_job_id


def _loads(value: str | None) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def serialize_job(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item.pop("singleton", None)
    item["dry_run"] = bool(item["dry_run"])
    item["errors"] = _loads(item.pop("errors_json", "[]"))
    total = int(item["total_files"] or 0)
    processed = int(item["processed_files"] or 0)
    item["percent"] = round(processed * 100 / total) if total else (100 if item["status"] == "completed" else 0)
    return item


def get_job(database_path: Path, job_id: str) -> dict[str, Any]:
    with connect(database_path) as connection:
        row = connection.execute("SELECT * FROM media_reindex_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise MediaReindexError("Задача переиндексации не найдена", 404)
    return serialize_job(row)


def latest_job(database_path: Path) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM media_reindex_jobs ORDER BY created_at DESC,id DESC LIMIT 1"
        ).fetchone()
    return serialize_job(row) if row else None


def queue_job(database_path: Path, *, actor_id: str, dry_run: bool = False) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    now = utc_now()
    try:
        with transaction(database_path) as connection:
            active = connection.execute(
                "SELECT id FROM media_reindex_jobs WHERE status IN ('queued','running') LIMIT 1"
            ).fetchone()
            if active:
                raise MediaReindexError(
                    "Переиндексация уже выполняется", 409, active_job_id=active["id"]
                )
            connection.execute(
                """INSERT INTO media_reindex_jobs(
                       id,actor_id,dry_run,status,phase,created_at,updated_at
                   ) VALUES(?,?,?,'queued','queued',?,?)""",
                (job_id, actor_id, int(dry_run), now, now),
            )
    except sqlite3.IntegrityError as error:
        active = latest_job(database_path)
        raise MediaReindexError(
            "Переиндексация уже выполняется", 409,
            active_job_id=active["id"] if active else None,
        ) from error
    return get_job(database_path, job_id)


def recover_interrupted_jobs(database_path: Path) -> int:
    with transaction(database_path) as connection:
        changed = connection.execute(
            """UPDATE media_reindex_jobs SET status='queued',phase='queued',started_at=NULL,
               error='Задача возобновлена после перезапуска CMS',updated_at=?
               WHERE status='running'""",
            (utc_now(),),
        )
        return changed.rowcount


def _claim_next(database_path: Path) -> str | None:
    with transaction(database_path) as connection:
        row = connection.execute(
            "SELECT id FROM media_reindex_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        now = utc_now()
        changed = connection.execute(
            """UPDATE media_reindex_jobs SET status='running',phase='scanning',started_at=?,
               updated_at=?,error=NULL WHERE id=? AND status='queued'""",
            (now, now, row["id"]),
        )
        return row["id"] if changed.rowcount else None


def _safe_error(error: Exception, settings: Settings) -> str:
    message = str(error).replace(str(settings.root), "<root>").replace(str(settings.media_dir), "<media>")
    return f"{type(error).__name__}: {message}"[:500]


def execute_next_job(settings: Settings) -> dict[str, Any] | None:
    job_id = _claim_next(settings.database_path)
    if not job_id:
        return None
    job = get_job(settings.database_path, job_id)
    last_write = 0.0
    last_processed = -1

    def progress(snapshot: dict[str, Any]) -> None:
        nonlocal last_write, last_processed
        now_clock = time.monotonic()
        processed = int(snapshot.get("processed_files") or 0)
        total = int(snapshot.get("total_files") or 0)
        phase = str(snapshot.get("phase") or "scanning")
        if processed != total and processed - last_processed < 25 and now_clock - last_write < 0.25 and phase == "scanning":
            return
        errors = list(snapshot.get("errors") or [])
        with transaction(settings.database_path) as connection:
            connection.execute(
                """UPDATE media_reindex_jobs SET phase=?,total_files=?,processed_files=?,ready=?,
                   invalid=?,created=?,updated=?,error_count=?,errors_json=?,updated_at=? WHERE id=?""",
                (
                    phase, total, processed, int(snapshot.get("ready") or 0),
                    int(snapshot.get("invalid") or 0), int(snapshot.get("created") or 0),
                    int(snapshot.get("updated") or 0), len(errors),
                    json.dumps(errors[:100], ensure_ascii=False), utc_now(), job_id,
                ),
            )
        last_write = now_clock
        last_processed = processed

    try:
        result = index_library(
            settings.database_path,
            settings.media_dir,
            missing_report=settings.root / "outputs" / "missing-legacy-media.csv",
            dry_run=job["dry_run"],
            progress=progress,
        )
        errors = list(result.get("errors") or [])
        now = utc_now()
        with transaction(settings.database_path) as connection:
            connection.execute(
                """UPDATE media_reindex_jobs SET status='completed',phase='completed',
                   total_files=?,processed_files=?,ready=?,invalid=?,created=?,updated=?,usages=?,
                   missing_issues=?,error_count=?,errors_json=?,updated_at=?,finished_at=? WHERE id=?""",
                (
                    int(result.get("files") or 0), int(result.get("files") or 0),
                    int(result.get("ready") or 0), int(result.get("invalid") or 0),
                    int(result.get("created") or 0), int(result.get("updated") or 0),
                    int(result.get("usages") or 0), int(result.get("missing_issues") or 0),
                    len(errors), json.dumps(errors[:100], ensure_ascii=False), now, now, job_id,
                ),
            )
    except Exception as error:
        now = utc_now()
        with transaction(settings.database_path) as connection:
            connection.execute(
                """UPDATE media_reindex_jobs SET status='failed',phase='failed',error=?,
                   updated_at=?,finished_at=? WHERE id=?""",
                (_safe_error(error, settings), now, now, job_id),
            )
        LOGGER.exception("Media reindex job %s failed", job_id)
    return get_job(settings.database_path, job_id)


async def media_reindex_scheduler(settings: Settings, interval_seconds: float = 0.5) -> None:
    recover_interrupted_jobs(settings.database_path)
    while True:
        try:
            await asyncio.to_thread(execute_next_job, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Media reindex scheduler pass failed")
        await asyncio.sleep(interval_seconds)
