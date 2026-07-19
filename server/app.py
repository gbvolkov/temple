from __future__ import annotations

import asyncio
import json
import re
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .config import Settings
from .content_blocks import ContentDataError, EDITABLE_RELATION_TYPES, prepare_content_data
from .db import connect, init_database, row_to_content, slugify, transaction, utc_now
from .full_import import build_full_plan, execute_plan
from .importer import media_mapping, run_import
from .security import hash_password, token_hash, verify_password
from .public_site import (
    PAGE_PLACEMENTS,
    SINGLETON_PAGE_PLACEMENTS,
    active_feature,
    base_context,
    content_view,
    external_url,
    feature_href,
    is_school_item,
    pages_by_placement,
    published_by_slug,
    published_item,
    published_items,
    published_page,
    published_related_content,
    service_groups,
)
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


class PreviewPayload(BaseModel):
    content_id: str | None = None
    content_type: str
    title: str = Field(min_length=1, max_length=240)
    slug: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


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
    templates = Jinja2Templates(directory=settings.site_dir / "templates")
    cms_templates = Jinja2Templates(directory=settings.site_dir)

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
        static_paths = {"/styles.css", "/app.js", "/cms.html", "/cms.css", "/cms.js", "/cms-schema.json"}
        if (
            path != "/"
            and path.endswith("/")
            and not path.startswith(("/api/", "/assets/"))
            and not (path.startswith("/media/") and path != "/media/")
        ):
            target = path.rstrip("/") or "/"
            if request.url.query:
                target += "?" + request.url.query
            return RedirectResponse(target, status_code=308)
        if (
            path != "/"
            and path not in static_paths
            and not path.startswith(("/api/", "/media/", "/assets/"))
            and settings.database_path.exists()
        ):
            with connect(settings.database_path) as connection:
                row = connection.execute("SELECT new_path,status_code FROM redirects WHERE old_path=?", (path,)).fetchone()
            if row:
                target = row["new_path"]
                if request.url.query:
                    base, marker, fragment = target.partition("#")
                    base += ("&" if "?" in base else "?") + request.url.query
                    target = base + (marker + fragment if marker else "")
                return RedirectResponse(target, status_code=row["status_code"])
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

    def prepare_data(
        connection: sqlite3.Connection,
        content_type: str,
        data: dict[str, Any],
        *,
        content_id: str | None = None,
        existing_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return prepare_content_data(
                connection, content_type, data,
                content_id=content_id, existing_data=existing_data,
            )
        except ContentDataError as error:
            raise HTTPException(422, str(error)) from error

    def validate_stage4_data(
        connection: sqlite3.Connection,
        content_type: str,
        data: dict[str, Any],
    ) -> None:
        if content_type == "page":
            placement = data.get("placement", "standalone")
            if placement not in PAGE_PLACEMENTS:
                raise HTTPException(422, "Неизвестное назначение страницы")
        if content_type == "service" and data.get("starts_at"):
            try:
                datetime.fromisoformat(str(data["starts_at"]).replace("Z", "+00:00"))
            except ValueError as error:
                raise HTTPException(422, "Дата и время богослужения указаны неверно") from error
        schedule = data.get("schedule")
        if schedule is not None:
            if not isinstance(schedule, list):
                raise HTTPException(422, "Расписание должно состоять из строк")
            for row in schedule:
                if not isinstance(row, dict):
                    raise HTTPException(422, "Некорректная строка расписания")
                weekday = row.get("weekday")
                if weekday not in (None, "") and (not str(weekday).isdigit() or not 1 <= int(weekday) <= 7):
                    raise HTTPException(422, "День недели должен быть от 1 до 7")
                time_value = str(row.get("time") or "")
                if time_value and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_value):
                    raise HTTPException(422, "Время в расписании должно иметь формат ЧЧ:ММ")
        related_section = data.get("related_section")
        if related_section:
            exists = connection.execute(
                "SELECT 1 FROM contents WHERE content_type='parish_section' AND (id=? OR slug=?)",
                (related_section, related_section),
            ).fetchone()
            if not exists:
                raise HTTPException(422, "Связанное направление прихода не найдено")

    def require_public_slot(connection: sqlite3.Connection, row: sqlite3.Row) -> None:
        if row["content_type"] == "site_contact":
            conflict = connection.execute(
                """SELECT id FROM contents
                   WHERE id!=? AND content_type='site_contact'
                     AND (published_version IS NOT NULL OR status='scheduled')
                     AND status NOT IN ('archived','trash') LIMIT 1""",
                (row["id"],),
            ).fetchone()
            if conflict:
                raise HTTPException(409, "Контакты храма уже опубликованы или запланированы")
            return
        if row["content_type"] != "page":
            return
        data = json.loads(row["data_json"])
        placement = data.get("placement", "standalone")
        if placement not in SINGLETON_PAGE_PLACEMENTS:
            return
        conflict = connection.execute(
            """SELECT c.id FROM contents c
               LEFT JOIN revisions r ON r.content_id=c.id AND r.version=c.published_version
               WHERE c.id!=? AND c.content_type='page' AND c.status NOT IN ('archived','trash')
                 AND (
                   (c.published_version IS NOT NULL AND json_extract(r.snapshot_json,'$.data.placement')=?)
                   OR (c.status='scheduled' AND json_extract(c.data_json,'$.placement')=?)
                 ) LIMIT 1""",
            (row["id"], placement, placement),
        ).fetchone()
        if conflict:
            raise HTTPException(409, "Для этого раздела уже существует опубликованная или запланированная страница")

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

    @app.get("/api/admin/content-options")
    def content_options(
        types: str = "news,page,parish_section,gallery",
        q: str = "",
        exclude_id: str | None = None,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
        _: dict = Depends(require("viewer")),
    ):
        requested = list(dict.fromkeys(TYPE_ALIASES.get(item.strip(), item.strip()) for item in types.split(",") if item.strip()))
        if not requested or any(item not in EDITABLE_RELATION_TYPES for item in requested):
            raise HTTPException(422, "Разрешены только новости, страницы, направления и галереи")
        placeholders = ",".join("?" for _ in requested)
        where = [f"content_type IN ({placeholders})", "status!='trash'"]
        params: list[Any] = [*requested]
        if exclude_id:
            where.append("id!=?")
            params.append(exclude_id)
        if q.strip():
            where.append("(title LIKE ? OR slug LIKE ?)")
            term = f"%{q.strip()}%"
            params.extend([term, term])
        clause = " AND ".join(where)
        with connect(settings.database_path) as connection:
            total = int(connection.execute(f"SELECT COUNT(*) FROM contents WHERE {clause}", params).fetchone()[0])
            rows = connection.execute(
                f"""SELECT id,content_type,title,slug,status,published_version,updated_at
                    FROM contents WHERE {clause}
                    ORDER BY title,id LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

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
            if content_type == "site_contact" and connection.execute(
                "SELECT 1 FROM contents WHERE content_type='site_contact' AND status!='trash' LIMIT 1"
            ).fetchone():
                raise HTTPException(409, "Карточка контактов уже существует; откройте её для редактирования")
            normalized_data = prepare_data(connection, content_type, payload.data, content_id=content_id)
            validate_stage4_data(connection, content_type, normalized_data)
            slug = available_slug(connection, payload.slug or payload.title)
            connection.execute(
                """INSERT INTO contents(id,content_type,slug,title,status,data_json,migration_review_required,
                   version,created_at,updated_at) VALUES(?,?,?,?,?,?,0,1,?,?)""",
                (content_id, content_type, slug, payload.title.strip(), "draft",
                 json.dumps(normalized_data, ensure_ascii=False, sort_keys=True), now, now),
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
            existing_data = json.loads(old["data_json"])
            normalized_data = prepare_data(
                connection, old["content_type"], payload.data,
                content_id=content_id, existing_data=existing_data,
            )
            validate_stage4_data(connection, old["content_type"], normalized_data)
            slug = old["published_slug"] or available_slug(connection, payload.slug, content_id)
            data_json = json.dumps(normalized_data, ensure_ascii=False, sort_keys=True)
            changed_fields = []
            if old["title"] != payload.title.strip():
                changed_fields.append("title")
            if old["slug"] != slug:
                changed_fields.append("slug")
            if existing_data != normalized_data:
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
            require_public_slot(connection, before)
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
            require_public_slot(connection, before)
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
            if before["content_type"] == "site_contact" and connection.execute(
                "SELECT 1 FROM contents WHERE id!=? AND content_type='site_contact' AND status!='trash' LIMIT 1",
                (content_id,),
            ).fetchone():
                raise HTTPException(409, "Сначала удалите другую карточку контактов")
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

    def public_context(active_nav: str, page_title: str, **values: Any) -> dict[str, Any]:
        return {
            **base_context(settings.database_path, active_nav=active_nav, page_title=page_title),
            **values,
        }

    def render_public(request: Request, template_name: str, context: dict[str, Any], status_code: int = 200):
        return templates.TemplateResponse(
            request=request,
            name=template_name,
            context=context,
            status_code=status_code,
        )

    def render_not_found(request: Request):
        return render_public(
            request,
            "404.html",
            public_context("", "Страница не найдена"),
            status_code=404,
        )

    def preview_record(payload: PreviewPayload, normalized_data: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": payload.content_id or "preview",
            "content_type": TYPE_ALIASES.get(payload.content_type, payload.content_type),
            "slug": payload.slug or "preview",
            "title": payload.title.strip(),
            "status": "draft",
            "data": normalized_data,
            "version": 1,
            "published_version": None,
            "published_at": None,
            "updated_at": utc_now(),
            "legacy_url": None,
            "migration_review_required": False,
        }

    @app.post("/api/admin/content-preview", response_class=HTMLResponse)
    def admin_content_preview(
        request: Request,
        payload: PreviewPayload,
        _: dict = Depends(require("viewer", mutation=True)),
    ):
        content_type = TYPE_ALIASES.get(payload.content_type, payload.content_type)
        if content_type not in schema["content_types"]:
            raise HTTPException(422, "Неизвестный тип материала")
        with connect(settings.database_path) as connection:
            existing_data: dict[str, Any] | None = None
            if payload.content_id:
                existing = connection.execute("SELECT * FROM contents WHERE id=?", (payload.content_id,)).fetchone()
                if not existing:
                    raise HTTPException(404, "Материал не найден")
                if existing["content_type"] != content_type:
                    raise HTTPException(409, "Тип материала не совпадает")
                existing_data = json.loads(existing["data_json"])
            normalized = prepare_data(
                connection, content_type, payload.data,
                content_id=payload.content_id, existing_data=existing_data,
            )
            validate_stage4_data(connection, content_type, normalized)
        raw = preview_record(payload, normalized)
        item = content_view(raw)
        common = {"preview_mode": True}
        placement = normalized.get("placement", "standalone") if content_type == "page" else ""

        if content_type == "page" and placement in {"about_history", "about_shrine"}:
            history_items = pages_by_placement(settings.database_path, "about_history")
            history = content_view(history_items[0]) if history_items else None
            shrines = [content_view(candidate) for candidate in pages_by_placement(settings.database_path, "about_shrine")]
            if placement == "about_history":
                history = item
            else:
                shrines = [candidate for candidate in shrines if candidate.get("id") != item["id"]]
                shrines.append(item)
                shrines.sort(key=lambda candidate: (int((candidate.get("data") or {}).get("navigation_order") or 100), candidate["title"]))
            clergy = [content_view(candidate) for candidate in published_items(settings.database_path, "clergy")]
            return render_public(
                request, "about.html",
                public_context("about", "Предпросмотр · О храме", history=history, shrines=shrines, clergy=clergy, **common),
            )
        if content_type == "page" and placement == "school_home":
            sections = [content_view(candidate) for candidate in published_items(settings.database_path, "parish_section") if is_school_item(candidate)]
            news = [content_view(candidate) for candidate in published_items(settings.database_path, "news") if is_school_item(candidate)]
            albums = [content_view(candidate) for candidate in published_items(settings.database_path, "gallery") if is_school_item(candidate)]
            return render_public(
                request, "school.html",
                public_context("school", "Предпросмотр · Воскресная школа", school_home=item, sections=sections, news=news, albums=albums, **common),
            )
        if content_type == "page" and placement == "schedule_info":
            groups = service_groups(published_items(settings.database_path, "service"))
            return render_public(
                request, "schedule.html",
                public_context("schedule", "Предпросмотр · Расписание", service_groups=groups, info=item, **common),
            )

        related = [content_view(candidate) for candidate in published_related_content(settings.database_path, raw)]
        if content_type == "parish_section":
            return render_public(
                request, "parish_detail.html",
                public_context("parish", f"Предпросмотр · {item['title']}", item=item, back_url="/parish", related_items=related, **common),
            )
        active_nav, back_url = {
            "news": ("parish", "/news"), "gallery": ("media", "/gallery"),
            "page": ("about", "/about"), "clergy": ("about", "/about#clergy"),
        }.get(content_type, ("", "/"))
        return render_public(
            request, "detail.html",
            public_context(active_nav, f"Предпросмотр · {item['title']}", item=item, back_url=back_url, related_items=related, **common),
        )

    def parse_archive_page(value: str) -> int | None:
        return int(value) if re.fullmatch(r"[1-9]\d*", value or "") else None

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def public_home(request: Request):
        news_raw = published_items(settings.database_path, "news")
        directions_raw = published_items(settings.database_path, "parish_section")
        directions_raw.sort(key=lambda item: (int((item.get("data") or {}).get("order") or 100), item["title"]))
        issues_raw = published_items(settings.database_path, "leaflet_issue")
        leaflet_raw = next((item for item in issues_raw if (item.get("data") or {}).get("featured")), issues_raw[0] if issues_raw else None)
        features = published_items(settings.database_path, "home_feature")
        feature_raw = active_feature(features, news_raw)
        feature = content_view(feature_raw) if feature_raw else None
        if feature:
            feature_data = feature_raw.get("data") or {}
            content_slug = feature_data.get("content_slug")
            legacy_target = str(feature_data.get("target_url") or "").strip()
            if legacy_target.startswith("#/content/"):
                legacy_target = "/" + legacy_target
            if not content_slug and legacy_target.startswith("/#/content/"):
                content_slug = unquote(legacy_target.removeprefix("/#/content/").split("?", 1)[0].split("#", 1)[0])
            linked = published_by_slug(settings.database_path, content_slug) if content_slug else None
            feature["href"] = feature_href(feature_raw, linked)
        return render_public(
            request,
            "home.html",
            public_context(
                "home",
                "Главная",
                feature=feature,
                news=[content_view(item) for item in news_raw],
                directions=[content_view(item) for item in directions_raw[:4]],
                leaflet=content_view(leaflet_raw) if leaflet_raw else None,
            ),
        )

    @app.get("/schedule", response_class=HTMLResponse, include_in_schema=False)
    def public_schedule(request: Request):
        groups = service_groups(published_items(settings.database_path, "service"))
        info_items = pages_by_placement(settings.database_path, "schedule_info")
        info = content_view(info_items[0]) if info_items else None
        return render_public(
            request,
            "schedule.html",
            public_context("schedule", "Расписание богослужений", service_groups=groups, info=info),
        )

    @app.get("/about", response_class=HTMLResponse, include_in_schema=False)
    def public_about(request: Request):
        clergy = [content_view(item) for item in published_items(settings.database_path, "clergy")]
        clergy.sort(key=lambda item: (int(item["data"].get("order") or 9999), item["title"]))
        history_items = pages_by_placement(settings.database_path, "about_history")
        shrines = [content_view(item) for item in pages_by_placement(settings.database_path, "about_shrine")]
        return render_public(
            request,
            "about.html",
            public_context(
                "about", "О храме", clergy=clergy,
                history=content_view(history_items[0]) if history_items else None,
                shrines=shrines,
            ),
        )

    @app.get("/parish", response_class=HTMLResponse, include_in_schema=False)
    def public_parish(request: Request):
        raw_sections = published_items(settings.database_path, "parish_section")
        raw_sections.sort(key=lambda item: (int((item.get("data") or {}).get("order") or 100), item["title"]))
        sections = [content_view(item) for item in raw_sections]
        return render_public(
            request, "parish.html", public_context("parish", "Жизнь прихода", sections=sections)
        )

    @app.get("/school", response_class=HTMLResponse, include_in_schema=False)
    def public_school(request: Request):
        home_items = pages_by_placement(settings.database_path, "school_home")
        school_home = content_view(home_items[0]) if home_items else None
        sections = [content_view(item) for item in published_items(settings.database_path, "parish_section") if is_school_item(item)]
        news = [
            content_view(item)
            for item in published_items(settings.database_path, "news")
            if is_school_item(item)
        ]
        albums = [
            content_view(item)
            for item in published_items(settings.database_path, "gallery")
            if is_school_item(item)
        ]
        return render_public(
            request,
            "school.html",
            public_context(
                "school",
                "Воскресная школа",
                school_home=school_home,
                sections=sections,
                news=news,
                albums=albums,
            ),
        )

    @app.get("/news", response_class=HTMLResponse, include_in_schema=False)
    def public_news(request: Request):
        news = [content_view(item) for item in published_items(settings.database_path, "news")]
        return render_public(request, "news.html", public_context("parish", "Новости и анонсы", news=news))

    @app.get("/gallery", response_class=HTMLResponse, include_in_schema=False)
    def public_gallery(request: Request, year: str | None = None, page: str = "1"):
        page_number = parse_archive_page(page)
        if page_number is None or (year is not None and not re.fullmatch(r"(?:19|20)\d{2}", year)):
            return render_not_found(request)
        result = published_page(settings.database_path, "gallery", page=page_number, per_page=24, year=year)
        if result["invalid"] or (year is not None and year not in result["years"]):
            return render_not_found(request)
        return render_public(
            request,
            "gallery.html",
            public_context(
                "media", "Фотогалерея",
                albums=[content_view(item) for item in result["items"]],
                years=result["years"], selected_year=year or "", pagination=result,
            ),
        )

    @app.get("/leaflet", response_class=HTMLResponse, include_in_schema=False)
    def public_leaflet(request: Request, year: str | None = None, page: str = "1"):
        page_number = parse_archive_page(page)
        if page_number is None or (year is not None and not re.fullmatch(r"(?:19|20)\d{2}", year)):
            return render_not_found(request)
        result = published_page(settings.database_path, "leaflet_issue", page=page_number, per_page=20, year=year)
        if result["invalid"] or (year is not None and year not in result["years"]):
            return render_not_found(request)
        return render_public(
            request,
            "leaflet.html",
            public_context(
                "media", "Иннокентиевский листок",
                issues=[content_view(item) for item in result["items"]],
                years=result["years"], selected_year=year or "", pagination=result,
            ),
        )

    @app.get("/media", response_class=HTMLResponse, include_in_schema=False)
    def public_media(request: Request):
        videos = []
        for raw in published_items(settings.database_path, "video"):
            item = content_view(raw)
            item["external_url"] = external_url(item["data"].get("external_url"))
            videos.append(item)
        return render_public(request, "media.html", public_context("media", "Медиа и архив", videos=videos))

    def public_detail_response(
        request: Request,
        slug: str,
        content_types: tuple[str, ...],
        active_nav: str,
        back_url: str,
    ):
        raw = published_item(settings.database_path, slug, content_types)
        if raw is None:
            return render_not_found(request)
        item = content_view(raw)
        return render_public(
            request,
            "detail.html",
            public_context(
                active_nav, item["title"], item=item, back_url=back_url,
                related_items=[content_view(related) for related in published_related_content(settings.database_path, raw)],
            ),
        )

    @app.get("/about/clergy/{slug}", response_class=HTMLResponse, include_in_schema=False)
    def public_clergy_detail(request: Request, slug: str):
        return public_detail_response(request, slug, ("clergy",), "about", "/about#clergy")

    @app.get("/parish/{slug}", response_class=HTMLResponse, include_in_schema=False)
    def public_parish_detail(request: Request, slug: str):
        raw = published_item(settings.database_path, slug, ("parish_section",))
        if raw is None:
            return render_not_found(request)
        return render_public(
            request,
            "parish_detail.html",
            public_context(
                "parish", raw["title"], item=content_view(raw), back_url="/parish",
                related_items=[content_view(related) for related in published_related_content(settings.database_path, raw)],
            ),
        )

    @app.get("/news/{slug}", response_class=HTMLResponse, include_in_schema=False)
    def public_news_detail(request: Request, slug: str):
        return public_detail_response(request, slug, ("news",), "parish", "/news")

    @app.get("/gallery/{slug}", response_class=HTMLResponse, include_in_schema=False)
    def public_gallery_detail(request: Request, slug: str):
        return public_detail_response(request, slug, ("gallery",), "media", "/gallery")

    @app.get("/pages/{slug}", response_class=HTMLResponse, include_in_schema=False)
    def public_page_detail(request: Request, slug: str):
        return public_detail_response(request, slug, ("page",), "about", "/about")

    @app.get("/index.html", include_in_schema=False)
    def old_index():
        return RedirectResponse("/", status_code=308)

    @app.get("/cms.html", response_class=HTMLResponse, include_in_schema=False)
    def cms_page(request: Request):
        return cms_templates.TemplateResponse(
            request=request,
            name="cms.html",
            context={"public_base_url": settings.public_base_url},
        )

    def static_file(name: str) -> FileResponse:
        return FileResponse(settings.site_dir / name)

    @app.get("/styles.css", include_in_schema=False)
    def public_styles():
        return static_file("styles.css")

    @app.get("/app.js", include_in_schema=False)
    def public_script():
        return static_file("app.js")

    @app.get("/cms.css", include_in_schema=False)
    def cms_styles():
        return static_file("cms.css")

    @app.get("/cms.js", include_in_schema=False)
    def cms_script():
        return static_file("cms.js")

    @app.get("/cms-schema.json", include_in_schema=False)
    def cms_schema_file():
        return static_file("cms-schema.json")

    app.mount("/assets", StaticFiles(directory=settings.site_dir / "assets"), name="assets")
    app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

    @app.get("/{path:path}", include_in_schema=False)
    def public_unknown(request: Request, path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return render_not_found(request)

    return app


app = create_app()
