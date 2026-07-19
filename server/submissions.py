from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import smtplib
import ssl
import threading
import time
import unicodedata
import uuid
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Settings
from .db import connect, transaction, utc_now


LOGGER = logging.getLogger(__name__)
MAX_PUBLIC_PAYLOAD_BYTES = 32 * 1024
SUBMISSION_STATUSES = {"new", "in_progress", "done", "spam"}
ALLOWED_TRANSITIONS = {
    "new": {"in_progress", "done", "spam"},
    "in_progress": {"new", "done", "spam"},
    "done": {"in_progress"},
    "spam": {"in_progress"},
}
RETRY_DELAYS_SECONDS = (60, 300, 900, 3600, 21600, 21600, 21600)
MAX_NOTIFICATION_ATTEMPTS = 8


class SubmissionError(RuntimeError):
    pass


class SubmissionConfigurationError(SubmissionError):
    pass


class SubmissionNotFound(SubmissionError):
    pass


class SubmissionConflict(SubmissionError):
    pass


class SubmissionRateLimit(SubmissionError):
    def __init__(self, retry_after: int):
        super().__init__("Слишком много отправок. Попробуйте позже")
        self.retry_after = max(1, retry_after)


def _normalize_text(
    value: str,
    *,
    minimum: int,
    maximum: int,
    multiline: bool = False,
) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    if "<" in normalized or ">" in normalized:
        raise ValueError("HTML в полях формы запрещён")
    allowed_controls = {"\n"} if multiline else set()
    for character in normalized:
        if unicodedata.category(character).startswith("C") and character not in allowed_controls:
            raise ValueError("Поле содержит недопустимые управляющие символы")
    if multiline:
        lines = [re.sub(r"[\t ]+", " ", line).strip() for line in normalized.split("\n")]
        normalized = "\n".join(lines).strip()
    else:
        normalized = re.sub(r"\s+", " ", normalized).strip()
    if not minimum <= len(normalized) <= maximum:
        raise ValueError(f"Длина поля должна быть от {minimum} до {maximum} символов")
    return normalized


def _normalize_name(value: str, *, minimum: int = 2, maximum: int = 120) -> str:
    normalized = _normalize_text(value, minimum=minimum, maximum=maximum)
    allowed = {" ", "-", "'", "’", "."}
    if any(
        not (unicodedata.category(character).startswith(("L", "M")) or character in allowed)
        for character in normalized
    ):
        raise ValueError("Имя может содержать буквы, пробел, дефис, апостроф и точку")
    return normalized


def _normalize_contact(value: str) -> str:
    normalized = _normalize_text(value, minimum=5, maximum=200)
    email_ok = bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized))
    phone_ok = bool(re.fullmatch(r"\+?[0-9()\-\s]+", normalized)) and 7 <= len(
        re.sub(r"\D", "", normalized)
    ) <= 15
    if not email_ok and not phone_ok:
        raise ValueError("Укажите корректный телефон или email")
    return normalized


class PrayerNotePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remembrance_type: Literal["health", "repose", "moleben"]
    names: list[str] = Field(min_length=1, max_length=10)
    website: str = Field(default="", max_length=200)

    @field_validator("names")
    @classmethod
    def validate_names(cls, values: list[str]) -> list[str]:
        return [_normalize_name(value, minimum=1, maximum=80) for value in values]

    @field_validator("website")
    @classmethod
    def normalize_website(cls, value: str) -> str:
        return unicodedata.normalize("NFKC", value).strip()


class SchoolEnrollmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_name: str
    contact: str
    child_name: str
    child_age: int = Field(ge=3, le=18)
    comment: str = Field(default="", max_length=2000)
    consent: Literal[True]
    website: str = Field(default="", max_length=200)

    @field_validator("parent_name", "child_name")
    @classmethod
    def validate_person_name(cls, value: str) -> str:
        return _normalize_name(value)

    @field_validator("contact")
    @classmethod
    def validate_contact(cls, value: str) -> str:
        return _normalize_contact(value)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str) -> str:
        if not value.strip():
            return ""
        return _normalize_text(value, minimum=1, maximum=2000, multiline=True)

    @field_validator("website")
    @classmethod
    def normalize_website(cls, value: str) -> str:
        return unicodedata.normalize("NFKC", value).strip()


class SubmissionStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    status: Literal["new", "in_progress", "done", "spam"]


class SubmissionVersionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class BurstRateLimiter:
    """A bounded in-process burst limiter backed by persistent accepted quotas."""

    def __init__(self, *, limit: int = 3, window_seconds: int = 60, max_keys: int = 10_000):
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            attempts = self._attempts[key]
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self.limit:
                retry_after = int(max(1, attempts[0] + self.window_seconds - now))
                raise SubmissionRateLimit(retry_after)
            attempts.append(now)
            if len(self._attempts) > self.max_keys:
                for stale_key in list(self._attempts):
                    queue = self._attempts[stale_key]
                    while queue and queue[0] <= cutoff:
                        queue.popleft()
                    if not queue:
                        self._attempts.pop(stale_key, None)
                    if len(self._attempts) <= self.max_keys:
                        break


def notification_configured(settings: Settings) -> bool:
    return bool(
        settings.smtp_host
        and settings.smtp_from
        and settings.submission_notify_to
        and (not settings.smtp_user or settings.smtp_password)
    )


def _trusted_proxy(settings: Settings, peer: str) -> bool:
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for value in settings.submission_trusted_proxy_networks:
        try:
            if address in ipaddress.ip_network(value, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else "unknown"
    if _trusted_proxy(settings, peer):
        real_ip = request.headers.get("x-real-ip", "").strip()
        forwarded = request.headers.get("x-forwarded-for", "")
        candidates = [real_ip] if real_ip else []
        candidates.extend(reversed([part.strip() for part in forwarded.split(",") if part.strip()]))
        for candidate in candidates:
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return peer[:200]


def _hmac_value(settings: Settings, namespace: str, value: str) -> str:
    if not settings.submission_ip_hash_secret:
        raise SubmissionConfigurationError("Публичные формы временно недоступны")
    return hmac.new(
        settings.submission_ip_hash_secret.encode("utf-8"),
        f"{namespace}\0{value}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def request_identity(request: Request, settings: Settings) -> str:
    return _hmac_value(settings, "ip", client_ip(request, settings))


def canonical_prayer_payload(payload: PrayerNotePayload) -> dict[str, Any]:
    return {"remembrance_type": payload.remembrance_type, "names": payload.names}


def canonical_school_payload(payload: SchoolEnrollmentPayload) -> dict[str, Any]:
    return {
        "parent_name": payload.parent_name,
        "contact": payload.contact,
        "child_name": payload.child_name,
        "child_age": payload.child_age,
        "comment": payload.comment,
        "consent": True,
        "consent_version": "2026-07-19",
    }


def ensure_payload_size(raw_body: bytes, canonical: dict[str, Any]) -> None:
    encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw_body) > MAX_PUBLIC_PAYLOAD_BYTES or len(encoded) > MAX_PUBLIC_PAYLOAD_BYTES:
        raise SubmissionError("Размер формы превышает 32 КиБ")


def _reference_code(submission_type: str, now: datetime) -> str:
    prefix = "Z" if submission_type == "prayer_note" else "S"
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    suffix = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"{prefix}-{now:%Y%m%d}-{suffix}"


def fake_reference(submission_type: str) -> str:
    return _reference_code(submission_type, datetime.now(UTC))


def _record_event(
    connection,
    *,
    submission_id: str,
    action: str,
    actor_id: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """INSERT INTO submission_events(
             id,submission_id,actor_id,action,from_status,to_status,details_json,created_at
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()), submission_id, actor_id, action, from_status, to_status,
            json.dumps(details or {}, ensure_ascii=False, sort_keys=True), utc_now(),
        ),
    )


def create_submission(
    database_path: Path,
    settings: Settings,
    *,
    submission_type: Literal["prayer_note", "school_enrollment"],
    payload: dict[str, Any],
    ip_hash: str,
) -> tuple[str, bool]:
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    fingerprint = _hmac_value(settings, "payload", canonical)
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat(timespec="seconds")
    duplicate_cutoff = (now_dt - timedelta(minutes=15)).isoformat(timespec="seconds")
    hour_cutoff = (now_dt - timedelta(hours=1)).isoformat(timespec="seconds")
    day_cutoff = (now_dt - timedelta(days=1)).isoformat(timespec="seconds")
    with transaction(database_path) as connection:
        duplicate = connection.execute(
            """SELECT reference_code FROM submissions
               WHERE ip_hash=? AND payload_fingerprint=? AND created_at>=?
               ORDER BY created_at DESC LIMIT 1""",
            (ip_hash, fingerprint, duplicate_cutoff),
        ).fetchone()
        if duplicate:
            return duplicate["reference_code"], False
        hour_count = connection.execute(
            "SELECT COUNT(*) FROM submissions WHERE ip_hash=? AND created_at>=?",
            (ip_hash, hour_cutoff),
        ).fetchone()[0]
        day_count = connection.execute(
            "SELECT COUNT(*) FROM submissions WHERE ip_hash=? AND created_at>=?",
            (ip_hash, day_cutoff),
        ).fetchone()[0]
        if hour_count >= 5:
            raise SubmissionRateLimit(3600)
        if day_count >= 20:
            raise SubmissionRateLimit(86400)
        submission_id = str(uuid.uuid4())
        outbox_id = str(uuid.uuid4())
        for _ in range(10):
            reference = _reference_code(submission_type, now_dt)
            if not connection.execute(
                "SELECT 1 FROM submissions WHERE reference_code=?", (reference,)
            ).fetchone():
                break
        else:
            raise SubmissionError("Не удалось создать номер заявки")
        stored_payload = dict(payload)
        if submission_type == "school_enrollment":
            stored_payload["consented_at"] = now
        stored_json = json.dumps(
            stored_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        connection.execute(
            """INSERT INTO submissions(
                 id,reference_code,submission_type,status,payload_json,ip_hash,payload_fingerprint,
                 version,created_at,updated_at
               ) VALUES(?,?,?,'new',?,?,?,1,?,?)""",
            (submission_id, reference, submission_type, stored_json, ip_hash, fingerprint, now, now),
        )
        connection.execute(
            """INSERT INTO notification_outbox(
                 id,submission_id,status,attempts,next_attempt_at,created_at,updated_at
               ) VALUES(?,?,'pending',0,?,?,?)""",
            (outbox_id, submission_id, now, now, now),
        )
        _record_event(
            connection, submission_id=submission_id, action="created", to_status="new",
            details={"submission_type": submission_type},
        )
    return reference, True


def _parse_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def serialize_submission_summary(row: Any) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data["id"],
        "reference_code": data["reference_code"],
        "submission_type": data["submission_type"],
        "status": data["status"],
        "version": data["version"],
        "created_at": data["created_at"],
        "updated_at": data["updated_at"],
        "closed_at": data["closed_at"],
        "notification": {
            "status": data.get("notification_status"),
            "attempts": data.get("notification_attempts", 0),
        },
    }


def list_submissions(
    database_path: Path,
    *,
    submission_type: str | None = None,
    status: str | None = None,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    where = ["1=1"]
    params: list[Any] = []
    if submission_type:
        if submission_type not in {"prayer_note", "school_enrollment"}:
            raise SubmissionError("Неизвестный вид заявки")
        where.append("s.submission_type=?")
        params.append(submission_type)
    if status:
        if status not in SUBMISSION_STATUSES:
            raise SubmissionError("Неизвестный статус заявки")
        where.append("s.status=?")
        params.append(status)
    if query.strip():
        where.append("s.reference_code LIKE ?")
        params.append(f"%{query.strip().upper()}%")
    clause = " AND ".join(where)
    with connect(database_path) as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM submissions s WHERE {clause}", params
        ).fetchone()[0]
        new_total = connection.execute(
            "SELECT COUNT(*) FROM submissions WHERE status='new'"
        ).fetchone()[0]
        rows = connection.execute(
            f"""SELECT s.*,o.status AS notification_status,o.attempts AS notification_attempts
                 FROM submissions s
                 LEFT JOIN notification_outbox o ON o.submission_id=s.id
                 WHERE {clause}
                 ORDER BY s.created_at DESC,s.id DESC LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
    return {
        "items": [serialize_submission_summary(row) for row in rows],
        "total": int(total), "new_total": int(new_total), "limit": limit, "offset": offset,
    }


def get_submission(database_path: Path, submission_id: str) -> dict[str, Any]:
    with connect(database_path) as connection:
        row = connection.execute(
            """SELECT s.*,o.id AS notification_id,o.status AS notification_status,
                      o.attempts AS notification_attempts,o.next_attempt_at,o.last_error,o.sent_at
               FROM submissions s LEFT JOIN notification_outbox o ON o.submission_id=s.id
               WHERE s.id=?""",
            (submission_id,),
        ).fetchone()
        if not row:
            raise SubmissionNotFound("Заявка не найдена")
        events = connection.execute(
            """SELECT e.*,u.username AS actor_username
               FROM submission_events e LEFT JOIN users u ON u.id=e.actor_id
               WHERE e.submission_id=? ORDER BY e.created_at DESC,e.id DESC""",
            (submission_id,),
        ).fetchall()
    result = serialize_submission_summary(row)
    result["payload"] = _parse_json(row["payload_json"], {})
    result["notification"] = {
        "id": row["notification_id"],
        "status": row["notification_status"],
        "attempts": row["notification_attempts"],
        "next_attempt_at": row["next_attempt_at"],
        "last_error": row["last_error"],
        "sent_at": row["sent_at"],
        "configured": None,
    }
    result["events"] = [
        {
            "id": event["id"], "action": event["action"],
            "from_status": event["from_status"], "to_status": event["to_status"],
            "details": _parse_json(event["details_json"], {}),
            "actor": event["actor_username"], "created_at": event["created_at"],
        }
        for event in events
    ]
    return result


def update_submission_status(
    database_path: Path,
    submission_id: str,
    *,
    version: int,
    status: str,
    actor_id: str,
) -> dict[str, Any]:
    if status not in SUBMISSION_STATUSES:
        raise SubmissionError("Неизвестный статус заявки")
    with transaction(database_path) as connection:
        before = connection.execute("SELECT * FROM submissions WHERE id=?", (submission_id,)).fetchone()
        if not before:
            raise SubmissionNotFound("Заявка не найдена")
        if before["version"] != version:
            raise SubmissionConflict("Заявка уже изменена; обновите очередь")
        if before["status"] != status:
            if status not in ALLOWED_TRANSITIONS[before["status"]]:
                raise SubmissionConflict(
                    f"Переход из {before['status']} в {status} недоступен"
                )
            now = utc_now()
            closed_at = now if status in {"done", "spam"} else None
            connection.execute(
                """UPDATE submissions SET status=?,version=version+1,handled_by=?,updated_at=?,closed_at=?
                   WHERE id=?""",
                (status, actor_id, now, closed_at, submission_id),
            )
            _record_event(
                connection, submission_id=submission_id, actor_id=actor_id,
                action="status_changed", from_status=before["status"], to_status=status,
            )
    return get_submission(database_path, submission_id)


def retry_notification(
    database_path: Path,
    submission_id: str,
    *,
    version: int,
    actor_id: str,
) -> dict[str, Any]:
    with transaction(database_path) as connection:
        submission = connection.execute(
            "SELECT * FROM submissions WHERE id=?", (submission_id,)
        ).fetchone()
        if not submission:
            raise SubmissionNotFound("Заявка не найдена")
        if submission["version"] != version:
            raise SubmissionConflict("Заявка уже изменена; обновите очередь")
        outbox = connection.execute(
            "SELECT * FROM notification_outbox WHERE submission_id=?", (submission_id,)
        ).fetchone()
        if not outbox:
            raise SubmissionNotFound("Уведомление заявки не найдено")
        if outbox["status"] == "sent":
            raise SubmissionConflict("Уведомление уже отправлено")
        if outbox["status"] == "sending":
            raise SubmissionConflict("Уведомление уже отправляется")
        now = utc_now()
        connection.execute(
            """UPDATE notification_outbox SET status='pending',attempts=0,next_attempt_at=?,
               locked_at=NULL,last_error=NULL,updated_at=? WHERE id=?""",
            (now, now, outbox["id"]),
        )
        _record_event(
            connection, submission_id=submission_id, actor_id=actor_id,
            action="notification_retried", details={"previous_status": outbox["status"]},
        )
    return get_submission(database_path, submission_id)


def _notification_subject(submission: Any) -> str:
    label = "Записка" if submission["submission_type"] == "prayer_note" else "Заявка в воскресную школу"
    return f"{label} · {submission['reference_code']}"


def _notification_body(submission: Any) -> str:
    payload = _parse_json(submission["payload_json"], {})
    lines = [f"Номер: {submission['reference_code']}", f"Получено: {submission['created_at']}", ""]
    if submission["submission_type"] == "prayer_note":
        type_labels = {"health": "О здравии", "repose": "Об упокоении", "moleben": "Молебен"}
        lines.extend([
            f"Вид: {type_labels.get(payload.get('remembrance_type'), 'Записка')}",
            "Имена:",
            *[f"- {name}" for name in payload.get("names", [])],
        ])
    else:
        lines.extend([
            f"Родитель: {payload.get('parent_name', '')}",
            f"Контакт: {payload.get('contact', '')}",
            f"Ребёнок: {payload.get('child_name', '')}",
            f"Возраст: {payload.get('child_age', '')}",
            f"Комментарий: {payload.get('comment') or '—'}",
            "Согласие на обработку данных: получено",
        ])
    return "\n".join(lines).strip() + "\n"


def send_notification(settings: Settings, outbox: Any, submission: Any) -> None:
    if not notification_configured(settings):
        raise SubmissionConfigurationError("SMTP не настроен")
    message = EmailMessage()
    message["Subject"] = _notification_subject(submission)
    message["From"] = settings.smtp_from
    message["To"] = ", ".join(settings.submission_notify_to)
    sender_domain = (settings.smtp_from or "temple.local").rsplit("@", 1)[-1]
    message["Message-ID"] = f"<submission-{outbox['id']}@{sender_domain}>"
    message.set_content(_notification_body(submission))
    context = ssl.create_default_context()
    if settings.smtp_security == "ssl":
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=20, context=context
        )
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
    with smtp:
        if settings.smtp_security == "starttls":
            smtp.starttls(context=context)
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(message)


def _safe_delivery_error(error: Exception) -> str:
    return f"{type(error).__name__}: delivery failed"[:500]


def process_notification_once(settings: Settings) -> bool:
    if not notification_configured(settings):
        return False
    now_dt = datetime.now(UTC)
    now = now_dt.isoformat(timespec="seconds")
    stale = (now_dt - timedelta(minutes=10)).isoformat(timespec="seconds")
    with transaction(settings.database_path) as connection:
        connection.execute(
            """UPDATE notification_outbox SET status='pending',locked_at=NULL,next_attempt_at=?,updated_at=?
               WHERE status='sending' AND locked_at<?""",
            (now, now, stale),
        )
        row = connection.execute(
            """SELECT * FROM notification_outbox
               WHERE status='pending' AND next_attempt_at<=?
               ORDER BY next_attempt_at,id LIMIT 1""",
            (now,),
        ).fetchone()
        if not row:
            return False
        claimed = connection.execute(
            """UPDATE notification_outbox SET status='sending',locked_at=?,updated_at=?
               WHERE id=? AND status='pending'""",
            (now, now, row["id"]),
        ).rowcount
        if claimed != 1:
            return False
        outbox = connection.execute(
            "SELECT * FROM notification_outbox WHERE id=?", (row["id"],)
        ).fetchone()
        submission = connection.execute(
            "SELECT * FROM submissions WHERE id=?", (outbox["submission_id"],)
        ).fetchone()
    try:
        send_notification(settings, outbox, submission)
    except Exception as error:
        attempts = int(outbox["attempts"]) + 1
        terminal = attempts >= MAX_NOTIFICATION_ATTEMPTS
        delay_index = min(attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)
        next_attempt = (datetime.now(UTC) + timedelta(seconds=RETRY_DELAYS_SECONDS[delay_index])).isoformat(
            timespec="seconds"
        )
        with transaction(settings.database_path) as connection:
            connection.execute(
                """UPDATE notification_outbox SET status=?,attempts=?,next_attempt_at=?,locked_at=NULL,
                   last_error=?,updated_at=? WHERE id=?""",
                (
                    "failed" if terminal else "pending", attempts, next_attempt,
                    _safe_delivery_error(error), utc_now(), outbox["id"],
                ),
            )
        LOGGER.warning("Submission notification delivery failed: %s", type(error).__name__)
        return True
    with transaction(settings.database_path) as connection:
        sent_at = utc_now()
        connection.execute(
            """UPDATE notification_outbox SET status='sent',attempts=attempts+1,locked_at=NULL,
               last_error=NULL,sent_at=?,updated_at=? WHERE id=?""",
            (sent_at, sent_at, outbox["id"]),
        )
        _record_event(
            connection, submission_id=outbox["submission_id"], action="notification_sent",
        )
    return True


def cleanup_expired_submissions(database_path: Path, *, now: datetime | None = None) -> int:
    current = now or datetime.now(UTC)
    prayer_cutoff = (current - timedelta(days=30)).isoformat(timespec="seconds")
    school_cutoff = (current - timedelta(days=180)).isoformat(timespec="seconds")
    with transaction(database_path) as connection:
        deleted = connection.execute(
            """DELETE FROM submissions
               WHERE status IN ('done','spam') AND closed_at IS NOT NULL
                 AND ((submission_type='prayer_note' AND closed_at<=?)
                      OR (submission_type='school_enrollment' AND closed_at<=?))""",
            (prayer_cutoff, school_cutoff),
        ).rowcount
    return int(deleted)


async def submission_scheduler(settings: Settings) -> None:
    while True:
        try:
            while await asyncio.to_thread(process_notification_once, settings):
                pass
            await asyncio.to_thread(cleanup_expired_submissions, settings.database_path)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.exception("Submission scheduler pass failed: %s", type(error).__name__)
        await asyncio.sleep(settings.submission_worker_interval_seconds)
