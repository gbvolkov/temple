from __future__ import annotations

import json
import re
import sqlite3
import uuid
from typing import Any

from .db import utc_now


ROLES = ("viewer", "editor", "publisher", "admin")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$")
SENSITIVE_DETAIL_PARTS = ("password", "token", "secret", "hash", "csrf")


class UserInputError(ValueError):
    pass


def normalize_username(value: str) -> str:
    username = value.strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise UserInputError(
            "Имя пользователя: 3–80 латинских букв, цифр и знаков . _ -"
        )
    return username


def validate_role(value: str) -> str:
    if value not in ROLES:
        raise UserInputError("Неизвестная роль пользователя")
    return value


def validate_new_password(value: str, *, username: str = "") -> str:
    if len(value) < 12 or len(value) > 128:
        raise UserInputError("Пароль должен содержать от 12 до 128 символов")
    if value.strip() != value:
        raise UserInputError("Пароль не должен начинаться или заканчиваться пробелом")
    if username and value.casefold() == username.casefold():
        raise UserInputError("Пароль не должен совпадать с именем пользователя")
    categories = sum((
        any(character.islower() for character in value),
        any(character.isupper() for character in value),
        any(character.isdigit() for character in value),
        any(not character.isalnum() for character in value),
    ))
    if categories < 3:
        raise UserInputError(
            "Используйте как минимум три типа символов: строчные, прописные, цифры, знаки"
        )
    return value


def serialize_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data["id"],
        "username": data["username"],
        "role": data["role"],
        "is_active": bool(data["is_active"]),
        "version": int(data.get("version") or 1),
        "created_at": data["created_at"],
        "updated_at": data.get("updated_at") or data["created_at"],
        "last_login_at": data.get("last_login_at"),
        "password_changed_at": data.get("password_changed_at"),
        "active_sessions": int(data.get("active_sessions") or 0),
    }


def record_user_event(
    connection: sqlite3.Connection,
    *,
    actor_id: str | None,
    target_user_id: str | None,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    safe_details = details or {}
    for key in safe_details:
        lowered = key.casefold()
        if any(part in lowered for part in SENSITIVE_DETAIL_PARTS):
            raise ValueError(f"Sensitive user-event detail is forbidden: {key}")
    connection.execute(
        """INSERT INTO user_events(
             id,actor_id,target_user_id,action,details_json,created_at
           ) VALUES(?,?,?,?,?,?)""",
        (
            str(uuid.uuid4()), actor_id, target_user_id, action,
            json.dumps(safe_details, ensure_ascii=False, sort_keys=True), utc_now(),
        ),
    )


def serialize_user_event(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    return {
        "id": data["id"],
        "actor_id": data.get("actor_id"),
        "actor_username": data.get("actor_username"),
        "target_user_id": data.get("target_user_id"),
        "target_username": data.get("target_username"),
        "action": data["action"],
        "details": json.loads(data.get("details_json") or "{}"),
        "created_at": data["created_at"],
    }
