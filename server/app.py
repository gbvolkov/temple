from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .db import connect, init_database, row_to_content, slugify, transaction, utc_now
from .full_import import build_full_plan, execute_plan
from .importer import media_mapping, run_import
from .security import hash_password, token_hash, verify_password
from .workflow import (
    admin_content as serialize_admin_content,
    publication_scheduler,
    public_content as published_content,
    record_audit,
)


ROLE_LEVEL = {"viewer": 0, "editor": 1, "publisher": 2, "admin": 3}
TYPE_ALIASES = {"leaflet": "leaflet_issue", "section": "parish_section"}
MAX_MEDIA_BYTES = 15 * 1024 * 1024
ALLOWED_MEDIA = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "application/pdf": ".pdf", "video/mp4": ".mp4",
}


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=300)


class ContentCreate(BaseModel):
    content_type: str
    title: str = Field(min_length=1, max_length=240)
    slug: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ContentUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    slug: str
    data: dict[str, Any]
    version: int = Field(ge=1)


class VersionPayload(BaseModel):
    version: int = Field(ge=1)


class SchedulePayload(VersionPayload):
    scheduled_at: datetime


def load_schema(settings: Settings) -> dict:
    return json.loads(settings.schema_path.read_text(encoding="utf-8"))


def ensure_bootstrap_admin(settings: Settings) -> None:
    if not settings.bootstrap_password:
        return
    with transaction(settings.database_path) as connection:
        exists = connection.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        if not exists:
            connection.execute(
                "INSERT INTO users(id,username,password_hash,role,is_active,created_at) VALUES(?,?,?,?,1,?)",
                (str(uuid.uuid4()), settings.bootstrap_user, hash_password(settings.bootstrap_password), "admin", utc_now()),
            )


def snapshot(connection: sqlite3.Connection, content_id: str, actor_id: str | None) -> None:
    row = connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
    content = row_to_content(row)
    connection.execute(
        "INSERT INTO revisions(content_id,version,snapshot_json,actor_id,created_at) VALUES(?,?,?,?,?)",
        (content_id, content["version"], json.dumps(content, ensure_ascii=False), actor_id, utc_now()),
    )


def available_slug(connection: sqlite3.Connection, desired: str, content_id: str | None = None) -> str:
    base = slugify(desired)
    candidate = base
    suffix = 2
    while True:
        row = connection.execute(
            "SELECT id FROM contents WHERE slug=? OR published_slug=?", (candidate, candidate)
        ).fetchone()
        if row is None or row["id"] == content_id:
            return candidate
        candidate = f"{base}-{suffix}"
        suffix += 1


def missing_required(schema: dict, content: dict) -> list[str]:
    definition = schema["content_types"].get(content["content_type"], {})
    missing: list[str] = []
    for name, field in definition.get("fields", {}).items():
        if not field.get("required"):
            continue
        value = content["title"] if name == "title" else content["data"].get(name)
        if value is None or value == "" or value == []:
            missing.append(name)
    return missing


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    schema = load_schema(settings)
    settings.media_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_database(settings.database_path)
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        ensure_bootstrap_admin(settings)
        scheduler_task = asyncio.create_task(publication_scheduler(settings.database_path))
        try:
            yield
        finally:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task

    app = FastAPI(title="CMS храма святителя Иннокентия", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.schema = schema

    @app.middleware("http")
    async def legacy_redirects(request: Request, call_next):
        path = request.url.path
        if path != "/" and not path.startswith(("/api/", "/media/")) and settings.database_path.exists():
            with connect(settings.database_path) as connection:
                row = connection.execute("SELECT new_path,status_code FROM redirects WHERE old_path=?", (path,)).fetchone()
            if row:
                return RedirectResponse(row["new_path"], status_code=row["status_code"])
        return await call_next(request)

    def current_user(request: Request) -> dict:
        raw = request.cookies.get("cms_session")
        if not raw:
            raise HTTPException(401, "Требуется вход в CMS")
        with connect(settings.database_path) as connection:
            row = connection.execute(
                """SELECT users.*, sessions.csrf_token, sessions.expires_at FROM sessions
                   JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND users.is_active=1""",
                (token_hash(raw),),
            ).fetchone()
        if not row or datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            raise HTTPException(401, "Сессия истекла")
        return dict(row)

    def require(min_role: str, *, mutation: bool = False):
        def dependency(
            request: Request,
            user: dict = Depends(current_user),
            x_csrf_token: str | None = Header(default=None),
        ) -> dict:
            if ROLE_LEVEL[user["role"]] < ROLE_LEVEL[min_role]:
                raise HTTPException(403, "Недостаточно прав")
            if mutation and (not x_csrf_token or not secrets.compare_digest(x_csrf_token, user["csrf_token"])):
                raise HTTPException(403, "Неверный CSRF-токен")
            return user
        return dependency

    def content_or_404(connection: sqlite3.Connection, content_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Материал не найден")
        return row

    def require_version(row: sqlite3.Row, version: int) -> None:
        if row["version"] != version:
            raise HTTPException(409, "Материал уже изменён; обновите страницу")

    def require_state(row: sqlite3.Row, allowed: set[str], action: str) -> None:
        if row["status"] not in allowed:
            raise HTTPException(409, f"Действие «{action}» недоступно из состояния {row['status']}")

    def require_ready(content: dict[str, Any]) -> None:
        if content["migration_review_required"]:
            raise HTTPException(409, "Сначала проверьте импортированный материал и отметьте его проверенным")
        missing = missing_required(schema, content)
        if missing:
            raise HTTPException(422, {"message": "Заполните обязательные поля", "fields": missing})

    @app.get("/api/health")
    def health():
        return {"status": "ok", "environment": settings.environment, "schema_version": schema["schema_version"]}

    @app.post("/api/admin/login")
    def login(payload: LoginPayload, response: Response):
        with transaction(settings.database_path) as connection:
            user = connection.execute("SELECT * FROM users WHERE username=? AND is_active=1", (payload.username,)).fetchone()
            if not user or not verify_password(payload.password, user["password_hash"]):
                raise HTTPException(401, "Неверное имя пользователя или пароль")
            raw_token = secrets.token_urlsafe(36)
            csrf = secrets.token_urlsafe(24)
            expires = (datetime.now(UTC) + timedelta(hours=settings.session_hours)).isoformat(timespec="seconds")
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_now(),))
            connection.execute(
                "INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES(?,?,?,?,?)",
                (token_hash(raw_token), user["id"], csrf, expires, utc_now()),
            )
        response.set_cookie(
            "cms_session", raw_token, httponly=True, samesite="strict",
            secure=settings.environment == "production", max_age=settings.session_hours * 3600, path="/",
        )
        return {"user": {"id": user["id"], "username": user["username"], "role": user["role"]}, "csrf_token": csrf}

    @app.get("/api/admin/session")
    def session(request: Request):
        try:
            user = current_user(request)
        except HTTPException:
            return {"authenticated": False}
        return {"authenticated": True, "user": {"id": user["id"], "username": user["username"], "role": user["role"]}, "csrf_token": user["csrf_token"]}

    @app.post("/api/admin/logout")
    def logout(request: Request, response: Response, _: dict = Depends(require("viewer", mutation=True))):
        raw = request.cookies.get("cms_session")
        if raw:
            with transaction(settings.database_path) as connection:
                connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(raw),))
        response.delete_cookie("cms_session", path="/")
        return {"ok": True}

    @app.get("/api/public/content")
    def public_content(content_type: str | None = None, limit: int = Query(50, ge=1, le=200)):
        query = "SELECT * FROM contents WHERE published_version IS NOT NULL AND status NOT IN ('archived','trash')"
        params: list[Any] = []
        if content_type:
            query += " AND content_type=?"
            params.append(TYPE_ALIASES.get(content_type, content_type))
        query += " ORDER BY COALESCE(published_at,updated_at) DESC LIMIT ?"
        params.append(limit)
        with connect(settings.database_path) as connection:
            return [published_content(connection, row) for row in connection.execute(query, params).fetchall()]

    @app.get("/api/public/content/{slug}")
    def public_content_item(slug: str):
        with connect(settings.database_path) as connection:
            row = connection.execute(
                """SELECT * FROM contents WHERE published_slug=? AND published_version IS NOT NULL
                   AND status NOT IN ('archived','trash')""",
                (slug,),
            ).fetchone()
            result = published_content(connection, row) if row else None
        if not row:
            raise HTTPException(404, "Материал не найден")
        return result

    @app.get("/api/admin/contents")
    def admin_contents(
        content_type: str | None = None,
        status: str | None = None,
        limit: int = Query(200, ge=1, le=500),
        _: dict = Depends(require("viewer")),
    ):
        query = "SELECT * FROM contents WHERE 1=1"
        params: list[Any] = []
        if content_type:
            query += " AND content_type=?"
            params.append(TYPE_ALIASES.get(content_type, content_type))
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with connect(settings.database_path) as connection:
            return [serialize_admin_content(row) for row in connection.execute(query, params).fetchall()]

    @app.get("/api/admin/content-index")
    def content_index(
        content_type: str | None = None,
        status: str | None = None,
        review_required: bool | None = None,
        q: str = "",
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0),
        _: dict = Depends(require("viewer")),
    ):
        where = ["1=1"]
        params: list[Any] = []
        if content_type:
            where.append("content_type=?")
            params.append(TYPE_ALIASES.get(content_type, content_type))
        if status:
            where.append("status=?")
            params.append(status)
        if review_required is not None:
            where.append("migration_review_required=?")
            params.append(int(review_required))
        if q.strip():
            where.append("(title LIKE ? OR slug LIKE ? OR COALESCE(legacy_url,'') LIKE ?)")
            term = f"%{q.strip()}%"
            params.extend([term, term, term])
        clause = " AND ".join(where)
        with connect(settings.database_path) as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM contents WHERE {clause}", params).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM contents WHERE {clause} ORDER BY updated_at DESC,title LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return {"items": [serialize_admin_content(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    @app.get("/api/admin/contents/{content_id}")
    def admin_content(content_id: str, _: dict = Depends(require("viewer"))):
        with connect(settings.database_path) as connection:
            row = connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Материал не найден")
        return serialize_admin_content(row)

    @app.post("/api/admin/contents", status_code=201)
    def create_content(payload: ContentCreate, user: dict = Depends(require("editor", mutation=True))):
        content_type = TYPE_ALIASES.get(payload.content_type, payload.content_type)
        if content_type not in schema["content_types"]:
            raise HTTPException(422, "Неизвестный тип материала")
        now = utc_now()
        content_id = str(uuid.uuid4())
        with transaction(settings.database_path) as connection:
            slug = available_slug(connection, payload.slug or payload.title)
            connection.execute(
                """INSERT INTO contents(id,content_type,slug,title,status,data_json,migration_review_required,
                   version,created_at,updated_at) VALUES(?,?,?,?,?,?,0,1,?,?)""",
                (content_id, content_type, slug, payload.title.strip(), "draft",
                 json.dumps(payload.data, ensure_ascii=False, sort_keys=True), now, now),
            )
            snapshot(connection, content_id, user["id"])
            row = connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
            record_audit(
                connection, content_id=content_id, actor_id=user["id"], action="create",
                before=None, after=row, details={"content_type": content_type},
            )
        return serialize_admin_content(row)

    @app.put("/api/admin/contents/{content_id}")
    def update_content(content_id: str, payload: ContentUpdate, user: dict = Depends(require("editor", mutation=True))):
        with transaction(settings.database_path) as connection:
            old = content_or_404(connection, content_id)
            require_version(old, payload.version)
            require_state(old, {"draft", "in_review", "scheduled", "published"}, "редактирование")
            if old["published_slug"] and payload.slug != old["published_slug"]:
                raise HTTPException(409, "URL опубликованного материала зафиксирован и пока не может быть изменён")
            slug = old["published_slug"] or available_slug(connection, payload.slug, content_id)
            data_json = json.dumps(payload.data, ensure_ascii=False, sort_keys=True)
            changed_fields = []
            if old["title"] != payload.title.strip():
                changed_fields.append("title")
            if old["slug"] != slug:
                changed_fields.append("slug")
            if old["data_json"] != data_json:
                changed_fields.append("data")
            if not changed_fields:
                return serialize_admin_content(old)
            version = old["version"] + 1
            connection.execute(
                """UPDATE contents
                   SET slug=?,title=?,data_json=?,status='draft',version=?,updated_at=?,
                       scheduled_at=NULL,reviewed_by=NULL,reviewed_at=NULL,deleted_at=NULL
                   WHERE id=?""",
                (slug, payload.title.strip(), data_json, version, utc_now(), content_id),
            )
            snapshot(connection, content_id, user["id"])
            row = connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
            record_audit(
                connection, content_id=content_id, actor_id=user["id"], action="update",
                before=old, after=row,
                details={"changed_fields": changed_fields, "cancelled_schedule": old["status"] == "scheduled"},
            )
        return serialize_admin_content(row)

    @app.post("/api/admin/contents/{content_id}/review")
    def review_content(content_id: str, payload: VersionPayload, user: dict = Depends(require("editor", mutation=True))):
        with transaction(settings.database_path) as connection:
            before = content_or_404(connection, content_id)
            require_version(before, payload.version)
            if not before["migration_review_required"]:
                return serialize_admin_content(before)
            connection.execute(
                "UPDATE contents SET migration_review_required=0,updated_at=? WHERE id=?",
                (utc_now(), content_id),
            )
            row = connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
            record_audit(
                connection, content_id=content_id, actor_id=user["id"], action="migration_review",
                before=before, after=row,
            )
        return serialize_admin_content(row)

    @app.post("/api/admin/contents/{content_id}/submit-review")
    def submit_review(content_id: str, payload: VersionPayload, user: dict = Depends(require("editor", mutation=True))):
        with transaction(settings.database_path) as connection:
            before = content_or_404(connection, content_id)
            require_version(before, payload.version)
            require_state(before, {"draft"}, "отправка на проверку")
            require_ready(serialize_admin_content(before))
            connection.execute(
                """UPDATE contents SET status='in_review',scheduled_at=NULL,reviewed_by=NULL,
                   reviewed_at=NULL,deleted_at=NULL,updated_at=? WHERE id=?""",
                (utc_now(), content_id),
            )
            row = content_or_404(connection, content_id)
            record_audit(
                connection, content_id=content_id, actor_id=user["id"], action="submit_review",
                before=before, after=row,
            )
        return serialize_admin_content(row)

    @app.post("/api/admin/contents/{content_id}/return-to-draft")
    def return_to_draft(content_id: str, payload: VersionPayload, user: dict = Depends(require("publisher", mutation=True))):
        with transaction(settings.database_path) as connection:
            before = content_or_404(connection, content_id)
            require_version(before, payload.version)
            require_state(before, {"in_review", "scheduled"}, "возврат редактору")
            connection.execute(
                """UPDATE contents SET status='draft',scheduled_at=NULL,reviewed_by=NULL,
                   reviewed_at=NULL,updated_at=? WHERE id=?""",
                (utc_now(), content_id),
            )
            row = content_or_404(connection, content_id)
            record_audit(
                connection, content_id=content_id, actor_id=user["id"], action="return_to_draft",
                before=before, after=row,
                details={"cancelled_schedule": before["status"] == "scheduled"},
            )
        return serialize_admin_content(row)

    @app.post("/api/admin/contents/{content_id}/publish")
    def publish_content(content_id: str, payload: VersionPayload, user: dict = Depends(require("publisher", mutation=True))):
        with transaction(settings.database_path) as connection:
            before = content_or_404(connection, content_id)
            require_version(before, payload.version)
            require_state(before, {"in_review"}, "публикация")
            require_ready(serialize_admin_content(before))
            now = utc_now()
            connection.execute(
                """UPDATE contents SET status='published',published_version=version,
                   published_slug=COALESCE(published_slug,slug),published_at=?,scheduled_at=NULL,
                   reviewed_by=?,reviewed_at=?,deleted_at=NULL,updated_at=? WHERE id=?""",
                (now, user["id"], now, now, content_id),
            )
            row = content_or_404(connection, content_id)
            record_audit(
                connection, content_id=content_id, actor_id=user["id"], action="publish",
                before=before, after=row,
            )
        return serialize_admin_content(row)

    @app.post("/api/admin/contents/{content_id}/schedule")
    def schedule_content(content_id: str, payload: SchedulePayload, user: dict = Depends(require("publisher", mutation=True))):
        if payload.scheduled_at.tzinfo is None or payload.scheduled_at.utcoffset() is None:
            raise HTTPException(422, "Дата публикации должна содержать часовой пояс")
        scheduled = payload.scheduled_at.astimezone(UTC)
        if scheduled <= datetime.now(UTC):
            raise HTTPException(422, "Дата отложенной публикации должна быть в будущем")
        scheduled_at = scheduled.isoformat(timespec="seconds")
        with transaction(settings.database_path) as connection:
            before = content_or_404(connection, content_id)
            require_version(before, payload.version)
            require_state(before, {"in_review"}, "планирование")
            require_ready(serialize_admin_content(before))
            now = utc_now()
            connection.execute(
                """UPDATE contents SET status='scheduled',scheduled_at=?,reviewed_by=?,reviewed_at=?,
                   deleted_at=NULL,updated_at=? WHERE id=?""",
                (scheduled_at, user["id"], now, now, content_id),
            )
            row = content_or_404(connection, content_id)
            record_audit(
                connection, content_id=content_id, actor_id=user["id"], action="schedule",
                before=before, after=row, details={"scheduled_at": scheduled_at},
            )
        return serialize_admin_content(row)

    @app.post("/api/admin/contents/{content_id}/archive")
    def archive_content(content_id: str, payload: VersionPayload, user: dict = Depends(require("publisher", mutation=True))):
        with transaction(settings.database_path) as connection:
            before = content_or_404(connection, content_id)
            require_version(before, payload.version)
            require_state(before, {"draft", "in_review", "scheduled", "published"}, "архивирование")
            connection.execute(
                """UPDATE contents SET status='archived',published_version=NULL,scheduled_at=NULL,
                   reviewed_by=NULL,reviewed_at=NULL,deleted_at=NULL,updated_at=? WHERE id=?""",
                (utc_now(), content_id),
            )
            row = content_or_404(connection, content_id)
            record_audit(
                connection, content_id=content_id, actor_id=user["id"], action="archive",
                before=before, after=row,
            )
        return serialize_admin_content(row)

    @app.post("/api/admin/contents/{content_id}/trash")
    def trash_content(content_id: str, payload: VersionPayload, user: dict = Depends(require("publisher", mutation=True))):
        with transaction(settings.database_path) as connection:
            before = content_or_404(connection, content_id)
            require_version(before, payload.version)
            require_state(before, {"draft", "in_review", "scheduled", "published", "archived"}, "перемещение в корзину")
            now = utc_now()
            connection.execute(
                """UPDATE contents SET status='trash',published_version=NULL,scheduled_at=NULL,
                   reviewed_by=NULL,reviewed_at=NULL,deleted_at=?,updated_at=? WHERE id=?""",
                (now, now, content_id),
            )
            row = content_or_404(connection, content_id)
            record_audit(
                connection, content_id=content_id, actor_id=user["id"], action="trash",
                before=before, after=row,
            )
        return serialize_admin_content(row)

    @app.post("/api/admin/contents/{content_id}/restore")
    def restore_content(content_id: str, payload: VersionPayload, user: dict = Depends(require("publisher", mutation=True))):
        with transaction(settings.database_path) as connection:
            before = content_or_404(connection, content_id)
            require_version(before, payload.version)
            require_state(before, {"archived", "trash"}, "восстановление")
            connection.execute(
                """UPDATE contents SET status='draft',published_version=NULL,scheduled_at=NULL,
                   reviewed_by=NULL,reviewed_at=NULL,deleted_at=NULL,updated_at=? WHERE id=?""",
                (utc_now(), content_id),
            )
            row = content_or_404(connection, content_id)
            record_audit(
                connection, content_id=content_id, actor_id=user["id"], action="restore",
                before=before, after=row,
            )
        return serialize_admin_content(row)

    @app.get("/api/admin/contents/{content_id}/revisions")
    def revisions(
        content_id: str,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        _: dict = Depends(require("viewer")),
    ):
        with connect(settings.database_path) as connection:
            content = content_or_404(connection, content_id)
            total = connection.execute("SELECT COUNT(*) FROM revisions WHERE content_id=?", (content_id,)).fetchone()[0]
            rows = connection.execute(
                """SELECT revisions.*,users.username AS actor_username FROM revisions
                   LEFT JOIN users ON users.id=revisions.actor_id
                   WHERE content_id=? ORDER BY version DESC LIMIT ? OFFSET ?""",
                (content_id, limit, offset),
            ).fetchall()
        items = []
        for revision in rows:
            snapshot_data = json.loads(revision["snapshot_json"])
            items.append({
                "id": revision["id"], "version": revision["version"], "actor_id": revision["actor_id"],
                "actor_username": revision["actor_username"], "created_at": revision["created_at"],
                "title": snapshot_data.get("title", ""), "status": snapshot_data.get("status", "draft"),
                "is_current": revision["version"] == content["version"],
                "is_published": revision["version"] == content["published_version"],
            })
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/admin/contents/{content_id}/revisions/{revision_version}")
    def revision_detail(content_id: str, revision_version: int, _: dict = Depends(require("viewer"))):
        with connect(settings.database_path) as connection:
            content = content_or_404(connection, content_id)
            row = connection.execute(
                """SELECT revisions.*,users.username AS actor_username FROM revisions
                   LEFT JOIN users ON users.id=revisions.actor_id
                   WHERE content_id=? AND version=?""",
                (content_id, revision_version),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Ревизия не найдена")
        return {
            "id": row["id"], "version": row["version"], "actor_id": row["actor_id"],
            "actor_username": row["actor_username"], "created_at": row["created_at"],
            "is_current": row["version"] == content["version"],
            "is_published": row["version"] == content["published_version"],
            "snapshot": json.loads(row["snapshot_json"]),
        }

    @app.post("/api/admin/contents/{content_id}/revisions/{revision_version}/restore")
    def restore_revision(
        content_id: str,
        revision_version: int,
        payload: VersionPayload,
        user: dict = Depends(require("editor", mutation=True)),
    ):
        with transaction(settings.database_path) as connection:
            before = content_or_404(connection, content_id)
            require_version(before, payload.version)
            require_state(before, {"draft", "in_review", "scheduled", "published"}, "восстановление ревизии")
            revision = connection.execute(
                "SELECT snapshot_json FROM revisions WHERE content_id=? AND version=?",
                (content_id, revision_version),
            ).fetchone()
            if not revision:
                raise HTTPException(404, "Ревизия не найдена")
            source = json.loads(revision["snapshot_json"])
            source_data = source.get("data", {})
            slug = before["published_slug"] or available_slug(
                connection, source.get("slug") or before["slug"], content_id
            )
            next_version = before["version"] + 1
            connection.execute(
                """UPDATE contents SET slug=?,title=?,data_json=?,status='draft',version=?,
                   scheduled_at=NULL,reviewed_by=NULL,reviewed_at=NULL,deleted_at=NULL,updated_at=?
                   WHERE id=?""",
                (
                    slug, source.get("title") or before["title"],
                    json.dumps(source_data, ensure_ascii=False, sort_keys=True),
                    next_version, utc_now(), content_id,
                ),
            )
            snapshot(connection, content_id, user["id"])
            row = content_or_404(connection, content_id)
            record_audit(
                connection, content_id=content_id, actor_id=user["id"], action="restore_revision",
                before=before, after=row, details={"source_version": revision_version},
            )
        return serialize_admin_content(row)

    @app.get("/api/admin/contents/{content_id}/audit-events")
    def audit_events(
        content_id: str,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        _: dict = Depends(require("viewer")),
    ):
        with connect(settings.database_path) as connection:
            content_or_404(connection, content_id)
            total = connection.execute("SELECT COUNT(*) FROM audit_events WHERE content_id=?", (content_id,)).fetchone()[0]
            rows = connection.execute(
                """SELECT audit_events.*,users.username AS actor_username FROM audit_events
                   LEFT JOIN users ON users.id=audit_events.actor_id
                   WHERE content_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
                (content_id, limit, offset),
            ).fetchall()
        items = []
        for event in rows:
            item = dict(event)
            item["details"] = json.loads(item.pop("details_json"))
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.post("/api/admin/media", status_code=201)
    async def upload_media(
        file: UploadFile = File(...),
        alt_text: str = "",
        user: dict = Depends(require("editor", mutation=True)),
    ):
        if file.content_type not in ALLOWED_MEDIA:
            raise HTTPException(415, "Разрешены JPG, PNG, WebP, PDF и MP4")
        body = await file.read(MAX_MEDIA_BYTES + 1)
        if len(body) > MAX_MEDIA_BYTES:
            raise HTTPException(413, "Файл больше 15 МБ")
        media_id = str(uuid.uuid4())
        stored_name = f"{media_id}{ALLOWED_MEDIA[file.content_type]}"
        (settings.media_dir / stored_name).write_bytes(body)
        with transaction(settings.database_path) as connection:
            connection.execute(
                "INSERT INTO media(id,original_name,stored_name,mime_type,size_bytes,alt_text,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (media_id, file.filename or stored_name, stored_name, file.content_type, len(body), alt_text, user["id"], utc_now()),
            )
        return {"id": media_id, "url": f"/media/{stored_name}", "name": file.filename, "mime_type": file.content_type}

    @app.get("/api/admin/migration")
    def migration_status(_: dict = Depends(require("viewer"))):
        with connect(settings.database_path) as connection:
            runs = [dict(row) for row in connection.execute(
                "SELECT id,source_name,source_fingerprint,status,imported,updated,skipped,errors,started_at,finished_at "
                "FROM migration_runs ORDER BY started_at DESC LIMIT 20"
            ).fetchall()]
            totals = dict(connection.execute(
                "SELECT COUNT(*) AS contents, SUM(migration_review_required) AS review_required FROM contents"
            ).fetchone())
            totals["redirects"] = connection.execute("SELECT COUNT(*) FROM redirects").fetchone()[0]
            by_type = {row["content_type"]: row["count"] for row in connection.execute(
                "SELECT content_type,COUNT(*) AS count FROM contents GROUP BY content_type"
            ).fetchall()}
            review_by_type = {}
            for row in connection.execute(
                """SELECT content_type,COUNT(*) AS total,SUM(migration_review_required) AS review_required,
                   SUM(CASE WHEN status='published' THEN 1 ELSE 0 END) AS published
                   FROM contents GROUP BY content_type ORDER BY content_type"""
            ).fetchall():
                total = int(row["total"] or 0)
                remaining = int(row["review_required"] or 0)
                review_by_type[row["content_type"]] = {
                    "total": total,
                    "review_required": remaining,
                    "reviewed": total - remaining,
                    "published": int(row["published"] or 0),
                }
            totals["reviewed"] = int(totals["contents"] or 0) - int(totals["review_required"] or 0)
        source = settings.legacy_crawl_path if settings.legacy_crawl_path and settings.legacy_crawl_path.exists() else settings.legacy_sections_path
        return {"runs": runs, "totals": totals, "by_type": by_type, "review_by_type": review_by_type, "source": source.name}

    @app.post("/api/admin/migration/import")
    def import_legacy(dry_run: bool = True, user: dict = Depends(require("admin", mutation=True))):
        if not settings.legacy_sections_path.exists():
            raise HTTPException(404, "Read-only снимок старого сайта не найден")
        manifest_path = settings.media_manifest_path
        crawl_path = settings.legacy_crawl_path
        if crawl_path and crawl_path.exists():
            crawl = json.loads(crawl_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"errors": []}
            failed_urls = {item["url"] for item in manifest.get("errors", [])}
            plan = build_full_plan(crawl, media_mapping(manifest_path), failed_urls)
            leaflet_preview = run_import(settings.database_path, settings.legacy_sections_path, dry_run=True, media_manifest=manifest_path, leaflets_only=True)
            if dry_run:
                return {
                    "dry_run": True,
                    "records_found": len(plan["records"]) + leaflet_preview["records_found"],
                    "full_pages": len(plan["records"]),
                    "leaflet_records": leaflet_preview["records_found"],
                    "broken_pages": len(plan["broken"]),
                    "errors": leaflet_preview["errors"],
                }
            full_result = execute_plan(settings.database_path, crawl_path, plan, actor_id=user["id"])
            leaflet_result = run_import(
                settings.database_path, settings.legacy_sections_path,
                media_manifest=manifest_path, leaflets_only=True, actor_id=user["id"],
            )
            return {
                "dry_run": False,
                "records_found": full_result["records_found"] + leaflet_result["records_found"],
                "imported": full_result["imported"] + leaflet_result["imported"],
                "updated": full_result["updated"] + leaflet_result["updated"],
                "skipped": full_result["skipped"] + leaflet_result["skipped"],
                "errors": full_result["errors"] + leaflet_result["errors"],
                "broken_pages": full_result["broken"],
            }
        return run_import(
            settings.database_path, settings.legacy_sections_path,
            dry_run=dry_run, media_manifest=manifest_path, actor_id=user["id"],
        )

    app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")
    app.mount("/", StaticFiles(directory=settings.site_dir, html=True), name="site")
    return app


app = create_app()
