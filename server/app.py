from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
import sqlite3
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .config import Settings
from .content_blocks import ContentDataError, EDITABLE_RELATION_TYPES, prepare_content_data
from .db import connect, init_database, row_to_content, slugify, transaction, utc_now
from .full_import import build_full_plan, execute_plan
from .importer import media_mapping, run_import
from .media_library import (
    MediaError,
    ensure_derivative,
    index_library,
    media_reference_problems,
    media_row,
    rebuild_usages,
    record_media_event,
    refresh_content_usages,
    resolve_media_path,
    serialize_media,
    store_upload,
)
from .migration_acceptance import (
    AcceptanceError,
    acceptance_scheduler,
    acceptance_summary,
    cancel_batch,
    create_batch,
    create_pilot_batch,
    execute_audit_run,
    finalize_batch,
    get_audit_run,
    get_batch,
    list_batches,
    list_issues,
    queue_audit,
    submit_batch,
    update_batch_item,
)
from .security import hash_password, token_hash, verify_password
from .search import (
    SEARCHABLE_TYPES,
    TYPE_LABELS as SEARCH_TYPE_LABELS,
    SearchError,
    reconcile_search_index,
    search_public,
    sync_content_search,
)
from .submissions import (
    MAX_PUBLIC_PAYLOAD_BYTES,
    BurstRateLimiter,
    PrayerNotePayload,
    SchoolEnrollmentPayload,
    SubmissionConfigurationError,
    SubmissionConflict,
    SubmissionError,
    SubmissionNotFound,
    SubmissionRateLimit,
    SubmissionStatusPayload,
    SubmissionVersionPayload,
    canonical_prayer_payload,
    canonical_school_payload,
    create_submission,
    ensure_payload_size,
    fake_reference,
    get_submission,
    list_submissions,
    notification_configured,
    request_identity,
    retry_notification,
    submission_scheduler,
    update_submission_status,
)
from .public_site import (
    PAGE_PLACEMENTS,
    SINGLETON_PAGE_PLACEMENTS,
    active_feature,
    base_context,
    content_view,
    external_url,
    format_date,
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
from .seo import (
    SITE_NAME,
    SocialPreviewError,
    build_seo_context,
    robots_text,
    rss_xml,
    sitemap_xml,
    site_social_preview_path,
    social_preview_path,
)
from .workflow import (
    admin_content as serialize_admin_content,
    publication_scheduler,
    public_content as published_content,
    record_audit,
)
from .user_management import (
    ROLES,
    UserInputError,
    normalize_username,
    record_user_event,
    serialize_user,
    serialize_user_event,
    validate_new_password,
    validate_role,
)


ROLE_LEVEL = {"viewer": 0, "editor": 1, "publisher": 2, "admin": 3}
TYPE_ALIASES = {"leaflet": "leaflet_issue", "section": "parish_section"}
LOGGER = logging.getLogger(__name__)


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


class MediaUpdate(BaseModel):
    version: int = Field(ge=1)
    alt_text: str = Field(default="", max_length=300)


class UserCreatePayload(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=12, max_length=128)
    role: Literal["viewer", "editor", "publisher", "admin"]


class UserUpdatePayload(BaseModel):
    version: int = Field(ge=1)
    role: Literal["viewer", "editor", "publisher", "admin"]
    is_active: bool


class UserVersionPayload(BaseModel):
    version: int = Field(ge=1)


class PasswordChangePayload(BaseModel):
    current_password: str = Field(min_length=1, max_length=300)
    new_password: str = Field(min_length=12, max_length=128)


class BulkContentItem(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    version: int = Field(ge=1)


class BulkWorkflowPayload(BaseModel):
    action: Literal["review", "archive", "publish"]
    items: list[BulkContentItem] = Field(min_length=1, max_length=100)


class MigrationAuditPayload(BaseModel):
    content_type: str | None = None
    year: int | None = Field(default=None, ge=1900, le=2100)
    check_external: bool = True


class MigrationBatchCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    kind: Literal["priority", "archive"]
    content_ids: list[str] = Field(min_length=1, max_length=50)
    filters: dict[str, Any] = Field(default_factory=dict)
    sample_rate: float = Field(default=0.1, gt=0, le=1)


class MigrationBatchItemPayload(BaseModel):
    version: int = Field(ge=1)
    manual_reviewed: bool = False
    disposition: Literal["pending", "accept", "archive", "trash"] = "pending"
    warning_acknowledgements: dict[str, str] = Field(default_factory=dict)
    note: str = Field(default="", max_length=2000)


class MigrationBatchVersionPayload(BaseModel):
    version: int = Field(ge=1)


class MigrationBatchFinalizePayload(MigrationBatchVersionPayload):
    warning_acknowledgements: dict[str, str] = Field(default_factory=dict)


def load_schema(settings: Settings) -> dict:
    return json.loads(settings.schema_path.read_text(encoding="utf-8"))


def ensure_bootstrap_admin(settings: Settings) -> None:
    if not settings.bootstrap_password:
        return
    with transaction(settings.database_path) as connection:
        exists = connection.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        if not exists:
            now = utc_now()
            connection.execute(
                """INSERT INTO users(
                     id,username,password_hash,role,is_active,created_at,updated_at,password_changed_at
                   ) VALUES(?,?,?,?,1,?,?,?)""",
                (
                    str(uuid.uuid4()), settings.bootstrap_user,
                    hash_password(settings.bootstrap_password), "admin", now, now, now,
                ),
            )


def snapshot(connection: sqlite3.Connection, content_id: str, actor_id: str | None) -> None:
    row = connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
    content = row_to_content(row)
    connection.execute(
        "INSERT INTO revisions(content_id,version,snapshot_json,actor_id,created_at) VALUES(?,?,?,?,?)",
        (content_id, content["version"], json.dumps(content, ensure_ascii=False), actor_id, utc_now()),
    )
    refresh_content_usages(connection, content_id)


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
    settings.derivatives_dir.mkdir(parents=True, exist_ok=True)
    templates = Jinja2Templates(directory=settings.site_dir / "templates")
    cms_templates = Jinja2Templates(directory=settings.site_dir)
    submission_limiter = BurstRateLimiter()

    def public_media_variant(url: object, variant: str = "web") -> str:
        """Return a stable derivative URL only for indexed local images."""
        value = str(url or "")
        if variant not in {"thumb", "web"} or not value.startswith("/media/"):
            return ""
        stored_name = unquote(value.removeprefix("/media/")).replace("\\", "/")
        with connect(settings.database_path) as connection:
            row = connection.execute(
                "SELECT id FROM media WHERE stored_name=? AND kind='image' AND status='ready'",
                (stored_name,),
            ).fetchone()
        return f"/media-derivatives/{row['id']}/{variant}.webp" if row else ""

    templates.env.globals["media_variant_url"] = public_media_variant

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        init_database(settings.database_path)
        reconcile_search_index(settings.database_path)
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        settings.derivatives_dir.mkdir(parents=True, exist_ok=True)
        ensure_bootstrap_admin(settings)
        scheduler_task = asyncio.create_task(
            publication_scheduler(settings.database_path, media_dir=settings.media_dir)
        )
        submission_task = asyncio.create_task(submission_scheduler(settings))
        acceptance_task = asyncio.create_task(acceptance_scheduler(settings))
        try:
            yield
        finally:
            scheduler_task.cancel()
            submission_task.cancel()
            acceptance_task.cancel()
            for task in (scheduler_task, submission_task, acceptance_task):
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="CMS храма святителя Иннокентия", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.schema = schema

    @app.middleware("http")
    async def legacy_redirects(request: Request, call_next):
        path = request.url.path
        submission_paths = {
            "/api/public/submissions/prayer-note",
            "/api/public/submissions/school-enrollment",
        }
        if path in submission_paths:
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_PUBLIC_PAYLOAD_BYTES:
                        return JSONResponse(
                            {"detail": "Размер формы превышает 32 КиБ"}, status_code=413
                        )
                except ValueError:
                    pass
            if len(await request.body()) > MAX_PUBLIC_PAYLOAD_BYTES:
                return JSONResponse(
                    {"detail": "Размер формы превышает 32 КиБ"}, status_code=413
                )
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
        response = await call_next(request)
        if path.startswith("/api/admin/submissions"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def current_user(request: Request) -> dict:
        raw = request.cookies.get("cms_session")
        if not raw:
            raise HTTPException(401, "Требуется вход в CMS")
        with connect(settings.database_path) as connection:
            row = connection.execute(
                """SELECT users.*, sessions.csrf_token, sessions.expires_at,
                          sessions.token_hash AS session_token_hash FROM sessions
                   JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND users.is_active=1""",
                (token_hash(raw),),
            ).fetchone()
        if not row or datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
            raise HTTPException(401, "Сессия истекла")
        return dict(row)

    def session_user(user: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(user)
        return {
            "id": data["id"],
            "username": data["username"],
            "role": data["role"],
            "is_active": bool(data["is_active"]),
        }

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

    def acceptance_http_error(error: AcceptanceError) -> HTTPException:
        return HTTPException(error.status_code, str(error))

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

    def require_ready(connection: sqlite3.Connection, content: dict[str, Any]) -> None:
        if content["migration_review_required"]:
            raise HTTPException(409, "Сначала проверьте импортированный материал и отметьте его проверенным")
        missing = missing_required(schema, content)
        if missing:
            raise HTTPException(422, {"message": "Заполните обязательные поля", "fields": missing})
        media_problems = media_reference_problems(connection, content, settings.media_dir)
        if media_problems:
            raise HTTPException(
                422,
                {"message": "Исправьте отсутствующие или повреждённые файлы", "media": media_problems},
            )

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
        if content_type in {"news", "gallery", "parish_section", "page", "clergy"}:
            for field, maximum, label in (
                ("seo_title", 70, "SEO-заголовок"),
                ("seo_description", 200, "SEO-описание"),
            ):
                value = data.get(field)
                if value is not None and not isinstance(value, str):
                    raise HTTPException(422, f"{label} должен быть строкой")
                if isinstance(value, str) and len(value.strip()) > maximum:
                    raise HTTPException(422, f"{label} не должен превышать {maximum} символов")
            social_image = data.get("social_image")
            if social_image is not None and not isinstance(social_image, str):
                raise HTTPException(422, "Изображение для соцсетей должно быть локальным медиафайлом")
            if isinstance(social_image, str) and social_image and not social_image.startswith("/media/"):
                raise HTTPException(422, "Изображение для соцсетей нужно выбрать из медиатеки")
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
            user = connection.execute(
                "SELECT * FROM users WHERE username=? COLLATE NOCASE AND is_active=1",
                (payload.username.strip(),),
            ).fetchone()
            if not user or not verify_password(payload.password, user["password_hash"]):
                raise HTTPException(401, "Неверное имя пользователя или пароль")
            raw_token = secrets.token_urlsafe(36)
            csrf = secrets.token_urlsafe(24)
            now = utc_now()
            expires = (datetime.now(UTC) + timedelta(hours=settings.session_hours)).isoformat(timespec="seconds")
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            connection.execute(
                "INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES(?,?,?,?,?)",
                (token_hash(raw_token), user["id"], csrf, expires, now),
            )
            connection.execute(
                "UPDATE users SET last_login_at=?,updated_at=? WHERE id=?",
                (now, now, user["id"]),
            )
            record_user_event(
                connection, actor_id=user["id"], target_user_id=user["id"], action="login",
            )
        response.set_cookie(
            "cms_session", raw_token, httponly=True, samesite="strict",
            secure=settings.environment == "production", max_age=settings.session_hours * 3600, path="/",
        )
        return {"user": session_user(user), "csrf_token": csrf}

    @app.get("/api/admin/session")
    def session(request: Request):
        try:
            user = current_user(request)
        except HTTPException:
            return {"authenticated": False}
        return {"authenticated": True, "user": session_user(user), "csrf_token": user["csrf_token"]}

    @app.post("/api/admin/logout")
    def logout(request: Request, response: Response, user: dict = Depends(require("viewer", mutation=True))):
        raw = request.cookies.get("cms_session")
        if raw:
            with transaction(settings.database_path) as connection:
                connection.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(raw),))
                record_user_event(
                    connection, actor_id=user["id"], target_user_id=user["id"], action="logout",
                )
        response.delete_cookie("cms_session", path="/")
        return {"ok": True}

    @app.post("/api/admin/change-password")
    def change_password(
        payload: PasswordChangePayload,
        response: Response,
        user: dict = Depends(require("viewer", mutation=True)),
    ):
        try:
            new_password = validate_new_password(payload.new_password, username=user["username"])
        except UserInputError as error:
            raise HTTPException(422, str(error)) from error
        with transaction(settings.database_path) as connection:
            current = connection.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            if not current or not verify_password(payload.current_password, current["password_hash"]):
                raise HTTPException(401, "Текущий пароль указан неверно")
            if verify_password(new_password, current["password_hash"]):
                raise HTTPException(409, "Новый пароль должен отличаться от текущего")
            now = utc_now()
            connection.execute(
                """UPDATE users SET password_hash=?,password_changed_at=?,updated_at=?,version=version+1
                   WHERE id=?""",
                (hash_password(new_password), now, now, user["id"]),
            )
            closed = connection.execute(
                "DELETE FROM sessions WHERE user_id=?", (user["id"],)
            ).rowcount
            record_user_event(
                connection, actor_id=user["id"], target_user_id=user["id"],
                action="password_change", details={"closed_sessions": closed},
            )
        response.delete_cookie("cms_session", path="/")
        return {"ok": True, "reauthenticate": True}

    @app.get("/api/admin/users")
    def list_users(_: dict = Depends(require("admin"))):
        now = utc_now()
        with connect(settings.database_path) as connection:
            rows = connection.execute(
                """SELECT u.*,COUNT(s.token_hash) AS active_sessions
                   FROM users u LEFT JOIN sessions s
                     ON s.user_id=u.id AND s.expires_at>?
                   GROUP BY u.id ORDER BY u.username COLLATE NOCASE""",
                (now,),
            ).fetchall()
        return {"items": [serialize_user(row) for row in rows], "roles": list(ROLES)}

    @app.post("/api/admin/users", status_code=201)
    def create_user(payload: UserCreatePayload, actor: dict = Depends(require("admin", mutation=True))):
        try:
            username = normalize_username(payload.username)
            role = validate_role(payload.role)
            password = validate_new_password(payload.password, username=username)
        except UserInputError as error:
            raise HTTPException(422, str(error)) from error
        user_id = str(uuid.uuid4())
        now = utc_now()
        try:
            with transaction(settings.database_path) as connection:
                connection.execute(
                    """INSERT INTO users(
                         id,username,password_hash,role,is_active,version,created_at,updated_at,password_changed_at
                       ) VALUES(?,?,?,?,1,1,?,?,?)""",
                    (user_id, username, hash_password(password), role, now, now, now),
                )
                record_user_event(
                    connection, actor_id=actor["id"], target_user_id=user_id,
                    action="user_create", details={"role": role},
                )
                row = connection.execute(
                    "SELECT users.*,0 AS active_sessions FROM users WHERE id=?", (user_id,)
                ).fetchone()
        except sqlite3.IntegrityError as error:
            raise HTTPException(409, "Пользователь с таким именем уже существует") from error
        return serialize_user(row)

    @app.patch("/api/admin/users/{user_id}")
    def update_user(
        user_id: str,
        payload: UserUpdatePayload,
        actor: dict = Depends(require("admin", mutation=True)),
    ):
        try:
            role = validate_role(payload.role)
        except UserInputError as error:
            raise HTTPException(422, str(error)) from error
        with transaction(settings.database_path) as connection:
            before = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not before:
                raise HTTPException(404, "Пользователь не найден")
            if before["version"] != payload.version:
                raise HTTPException(409, "Учётная запись уже изменена; обновите список")
            if user_id == actor["id"] and (role != "admin" or not payload.is_active):
                raise HTTPException(409, "Нельзя снять собственные права администратора или заблокировать себя")
            removes_active_admin = (
                before["role"] == "admin"
                and bool(before["is_active"])
                and (role != "admin" or not payload.is_active)
            )
            if removes_active_admin:
                active_admins = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role='admin' AND is_active=1"
                ).fetchone()[0]
                if active_admins <= 1:
                    raise HTTPException(409, "В CMS должен остаться хотя бы один активный администратор")
            if before["role"] == role and bool(before["is_active"]) == payload.is_active:
                return serialize_user({**dict(before), "active_sessions": 0})
            now = utc_now()
            connection.execute(
                """UPDATE users SET role=?,is_active=?,updated_at=?,version=version+1
                   WHERE id=?""",
                (role, int(payload.is_active), now, user_id),
            )
            closed = 0
            if not payload.is_active:
                closed = connection.execute(
                    "DELETE FROM sessions WHERE user_id=?", (user_id,)
                ).rowcount
            record_user_event(
                connection, actor_id=actor["id"], target_user_id=user_id,
                action="user_update",
                details={
                    "from_role": before["role"], "to_role": role,
                    "from_active": bool(before["is_active"]), "to_active": payload.is_active,
                    "closed_sessions": closed,
                },
            )
            row = connection.execute(
                """SELECT u.*,(SELECT COUNT(*) FROM sessions s
                   WHERE s.user_id=u.id AND s.expires_at>?) AS active_sessions
                   FROM users u WHERE u.id=?""",
                (now, user_id),
            ).fetchone()
        return serialize_user(row)

    @app.post("/api/admin/users/{user_id}/terminate-sessions")
    def terminate_user_sessions(
        user_id: str,
        payload: UserVersionPayload,
        actor: dict = Depends(require("admin", mutation=True)),
    ):
        if user_id == actor["id"]:
            raise HTTPException(409, "Для завершения собственной сессии используйте кнопку выхода")
        with transaction(settings.database_path) as connection:
            target = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if not target:
                raise HTTPException(404, "Пользователь не найден")
            if target["version"] != payload.version:
                raise HTTPException(409, "Учётная запись уже изменена; обновите список")
            closed = connection.execute("DELETE FROM sessions WHERE user_id=?", (user_id,)).rowcount
            record_user_event(
                connection, actor_id=actor["id"], target_user_id=user_id,
                action="sessions_terminated", details={"closed_sessions": closed},
            )
        return {"ok": True, "closed_sessions": closed}

    @app.get("/api/admin/user-events")
    def user_events(
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0),
        _: dict = Depends(require("admin")),
    ):
        with connect(settings.database_path) as connection:
            total = connection.execute("SELECT COUNT(*) FROM user_events").fetchone()[0]
            rows = connection.execute(
                """SELECT e.*,actor.username AS actor_username,target.username AS target_username
                   FROM user_events e
                   LEFT JOIN users actor ON actor.id=e.actor_id
                   LEFT JOIN users target ON target.id=e.target_user_id
                   ORDER BY e.created_at DESC,e.id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return {
            "items": [serialize_user_event(row) for row in rows],
            "total": total, "limit": limit, "offset": offset,
        }

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

    @app.get("/api/public/search")
    def public_search_api(
        q: str = Query(min_length=2, max_length=200),
        content_type: str | None = None,
        page: int = Query(1, ge=1),
        per_page: int = Query(20, ge=1, le=50),
    ):
        try:
            return search_public(
                settings.database_path,
                q,
                content_type=content_type,
                page=page,
                per_page=per_page,
            ).as_dict()
        except SearchError as error:
            raise HTTPException(422, str(error)) from error

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

    def submission_http_error(error: SubmissionError) -> HTTPException:
        if isinstance(error, SubmissionNotFound):
            return HTTPException(404, str(error))
        if isinstance(error, SubmissionConflict):
            return HTTPException(409, str(error))
        if isinstance(error, SubmissionConfigurationError):
            return HTTPException(503, str(error))
        if isinstance(error, SubmissionRateLimit):
            return HTTPException(
                429, str(error), headers={"Retry-After": str(error.retry_after)}
            )
        return HTTPException(422, str(error))

    async def accept_public_submission(
        request: Request,
        *,
        submission_type: Literal["prayer_note", "school_enrollment"],
        payload: PrayerNotePayload | SchoolEnrollmentPayload,
    ) -> JSONResponse:
        try:
            raw_body = await request.body()
            canonical = (
                canonical_prayer_payload(payload)
                if isinstance(payload, PrayerNotePayload)
                else canonical_school_payload(payload)
            )
            ensure_payload_size(raw_body, canonical)
            ip_hash = request_identity(request, settings)
            submission_limiter.check(ip_hash)
            if payload.website:
                reference = fake_reference(submission_type)
            else:
                reference, _ = create_submission(
                    settings.database_path,
                    settings,
                    submission_type=submission_type,
                    payload=canonical,
                    ip_hash=ip_hash,
                )
        except SubmissionError as error:
            if str(error) == "Размер формы превышает 32 КиБ":
                raise HTTPException(413, str(error)) from error
            raise submission_http_error(error) from error
        response = JSONResponse(
            {"accepted": True, "reference_code": reference}, status_code=201
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/public/submissions/prayer-note", status_code=201)
    async def submit_prayer_note(payload: PrayerNotePayload, request: Request):
        return await accept_public_submission(
            request, submission_type="prayer_note", payload=payload
        )

    @app.post("/api/public/submissions/school-enrollment", status_code=201)
    async def submit_school_enrollment(payload: SchoolEnrollmentPayload, request: Request):
        return await accept_public_submission(
            request, submission_type="school_enrollment", payload=payload
        )

    @app.get("/api/admin/submissions")
    def admin_submissions(
        submission_type: str | None = Query(None, alias="type"),
        status: str | None = None,
        q: str = "",
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        _: dict = Depends(require("publisher")),
    ):
        try:
            return list_submissions(
                settings.database_path,
                submission_type=submission_type,
                status=status,
                query=q,
                limit=limit,
                offset=offset,
            )
        except SubmissionError as error:
            raise submission_http_error(error) from error

    @app.get("/api/admin/submissions/{submission_id}")
    def admin_submission(
        submission_id: str,
        _: dict = Depends(require("publisher")),
    ):
        try:
            result = get_submission(settings.database_path, submission_id)
        except SubmissionError as error:
            raise submission_http_error(error) from error
        result["notification"]["configured"] = notification_configured(settings)
        return result

    @app.patch("/api/admin/submissions/{submission_id}/status")
    def admin_submission_status(
        submission_id: str,
        payload: SubmissionStatusPayload,
        user: dict = Depends(require("publisher", mutation=True)),
    ):
        try:
            result = update_submission_status(
                settings.database_path,
                submission_id,
                version=payload.version,
                status=payload.status,
                actor_id=user["id"],
            )
        except SubmissionError as error:
            raise submission_http_error(error) from error
        result["notification"]["configured"] = notification_configured(settings)
        return result

    @app.post("/api/admin/submissions/{submission_id}/retry-notification")
    def admin_submission_retry_notification(
        submission_id: str,
        payload: SubmissionVersionPayload,
        user: dict = Depends(require("publisher", mutation=True)),
    ):
        try:
            result = retry_notification(
                settings.database_path,
                submission_id,
                version=payload.version,
                actor_id=user["id"],
            )
        except SubmissionError as error:
            raise submission_http_error(error) from error
        result["notification"]["configured"] = notification_configured(settings)
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
        statuses: str | None = None,
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
        if statuses:
            requested_statuses = list(dict.fromkeys(
                item.strip() for item in statuses.split(",") if item.strip()
            ))
            allowed_statuses = {"draft", "in_review", "scheduled", "published", "archived", "trash"}
            if not requested_statuses or any(item not in allowed_statuses for item in requested_statuses):
                raise HTTPException(422, "Неизвестный статус материала")
            placeholders = ",".join("?" for _ in requested_statuses)
            where.append(f"status IN ({placeholders})")
            params.extend(requested_statuses)
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

    @app.post("/api/admin/content-bulk")
    def bulk_content_workflow(
        payload: BulkWorkflowPayload,
        user: dict = Depends(require("editor", mutation=True)),
    ):
        if payload.action == "review":
            raise HTTPException(
                409,
                "Массовая отметка миграционных материалов отключена; используйте аудит и редакционную партию",
            )
        if payload.action in {"archive", "publish"} and ROLE_LEVEL[user["role"]] < ROLE_LEVEL["publisher"]:
            raise HTTPException(403, "Только выпускающий редактор может публиковать и архивировать")
        ids = [item.id for item in payload.items]
        if len(set(ids)) != len(ids):
            raise HTTPException(422, "Материал не должен повторяться в массовой операции")
        batch_id = str(uuid.uuid4())
        updated: list[dict[str, Any]] = []
        with transaction(settings.database_path) as connection:
            for item in payload.items:
                before = content_or_404(connection, item.id)
                require_version(before, item.version)
                if payload.action == "archive":
                    require_state(
                        before, {"draft", "in_review", "scheduled", "published"},
                        "массовое архивирование",
                    )
                    connection.execute(
                        """UPDATE contents SET status='archived',published_version=NULL,
                           scheduled_at=NULL,reviewed_by=NULL,reviewed_at=NULL,
                           deleted_at=NULL,updated_at=? WHERE id=?""",
                        (utc_now(), item.id),
                    )
                    refresh_content_usages(connection, item.id)
                    audit_action = "archive"
                else:
                    require_state(before, {"in_review"}, "массовая публикация")
                    require_ready(connection, serialize_admin_content(before))
                    require_public_slot(connection, before)
                    now = utc_now()
                    connection.execute(
                        """UPDATE contents SET status='published',published_version=version,
                           published_slug=COALESCE(published_slug,slug),published_at=?,
                           scheduled_at=NULL,reviewed_by=?,reviewed_at=?,deleted_at=NULL,
                           updated_at=? WHERE id=?""",
                        (now, user["id"], now, now, item.id),
                    )
                    refresh_content_usages(connection, item.id)
                    audit_action = "publish"
                after = content_or_404(connection, item.id)
                if payload.action in {"archive", "publish"}:
                    sync_content_search(connection, item.id)
                record_audit(
                    connection, content_id=item.id, actor_id=user["id"],
                    action=audit_action, before=before, after=after,
                    details={"bulk": True, "batch_id": batch_id},
                )
                updated.append(serialize_admin_content(after))
        return {
            "ok": True, "action": payload.action, "batch_id": batch_id,
            "updated": len(updated), "items": updated,
        }

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
        with connect(settings.database_path) as connection:
            before = content_or_404(connection, content_id)
            require_version(before, payload.version)
        if not before["migration_review_required"]:
            return serialize_admin_content(before)
        raise HTTPException(
            409,
            "Старая отметка проверки больше не снимает миграционный флаг; запустите аудит и включите материал в редакционную партию",
        )

    @app.post("/api/admin/contents/{content_id}/submit-review")
    def submit_review(content_id: str, payload: VersionPayload, user: dict = Depends(require("editor", mutation=True))):
        with transaction(settings.database_path) as connection:
            before = content_or_404(connection, content_id)
            require_version(before, payload.version)
            require_state(before, {"draft"}, "отправка на проверку")
            require_ready(connection, serialize_admin_content(before))
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
            require_ready(connection, serialize_admin_content(before))
            require_public_slot(connection, before)
            now = utc_now()
            connection.execute(
                """UPDATE contents SET status='published',published_version=version,
                   published_slug=COALESCE(published_slug,slug),published_at=?,scheduled_at=NULL,
                   reviewed_by=?,reviewed_at=?,deleted_at=NULL,updated_at=? WHERE id=?""",
                (now, user["id"], now, now, content_id),
            )
            row = content_or_404(connection, content_id)
            refresh_content_usages(connection, content_id)
            sync_content_search(connection, content_id)
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
            require_ready(connection, serialize_admin_content(before))
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
            refresh_content_usages(connection, content_id)
            sync_content_search(connection, content_id)
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
            refresh_content_usages(connection, content_id)
            sync_content_search(connection, content_id)
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
            refresh_content_usages(connection, content_id)
            sync_content_search(connection, content_id)
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

    @app.get("/api/admin/media")
    def list_media(
        q: str = "",
        kind: str | None = Query(default=None, pattern="^(image|video|document)$"),
        source: str | None = Query(default=None, pattern="^(legacy|upload)$"),
        status: str | None = Query(default=None, pattern="^(pending|ready|invalid|missing)$"),
        usage: str | None = Query(default=None, pattern="^(used|unused)$"),
        sort: str = Query(default="newest", pattern="^(newest|oldest|name|size)$"),
        limit: int = Query(48, ge=1, le=200),
        offset: int = Query(0, ge=0),
        _: dict = Depends(require("viewer")),
    ):
        conditions: list[str] = []
        params: list[Any] = []
        if q.strip():
            needle = f"%{q.strip().lower()}%"
            conditions.append(
                "(LOWER(m.original_name) LIKE ? OR LOWER(m.alt_text) LIKE ? OR LOWER(m.stored_name) LIKE ? "
                "OR EXISTS(SELECT 1 FROM media_usages su JOIN contents sc ON sc.id=su.content_id "
                "WHERE su.media_id=m.id AND LOWER(sc.title) LIKE ?))"
            )
            params.extend([needle, needle, needle, needle])
        for column, value in (("kind", kind), ("source", source), ("status", status)):
            if value:
                conditions.append(f"m.{column}=?")
                params.append(value)
        if usage == "used":
            conditions.append("EXISTS(SELECT 1 FROM media_usages ux WHERE ux.media_id=m.id)")
        elif usage == "unused":
            conditions.append("NOT EXISTS(SELECT 1 FROM media_usages ux WHERE ux.media_id=m.id)")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        order = {
            "newest": "COALESCE(m.updated_at,m.created_at) DESC,m.id DESC",
            "oldest": "m.created_at,m.id",
            "name": "LOWER(m.original_name),m.id",
            "size": "m.size_bytes DESC,m.id",
        }[sort]
        with connect(settings.database_path) as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM media m{where}", params).fetchone()[0]
            rows = connection.execute(
                f"""SELECT m.*,COUNT(DISTINCT u.content_id) AS content_count,COUNT(u.media_id) AS usage_count
                    FROM media m LEFT JOIN media_usages u ON u.media_id=m.id
                    {where} GROUP BY m.id ORDER BY {order} LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
        return {"items": [serialize_media(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    @app.get("/api/admin/media/{media_id}")
    def get_media(media_id: str, _: dict = Depends(require("viewer"))):
        with connect(settings.database_path) as connection:
            row = media_row(connection, media_id)
            if not row:
                raise HTTPException(404, "Медиафайл не найден")
            events = connection.execute(
                """SELECT e.*,u.username AS actor_username FROM media_events e
                   LEFT JOIN users u ON u.id=e.actor_id WHERE e.media_id=?
                   ORDER BY e.created_at DESC,e.id DESC LIMIT 50""",
                (media_id,),
            ).fetchall()
        item = serialize_media(row)
        item["events"] = [
            {**dict(event), "details": json.loads(event["details_json"] or "{}")}
            for event in events
        ]
        for event in item["events"]:
            event.pop("details_json", None)
        return item

    @app.get("/api/admin/media/{media_id}/usages")
    def media_usages(
        media_id: str,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        _: dict = Depends(require("viewer")),
    ):
        with connect(settings.database_path) as connection:
            if not connection.execute("SELECT 1 FROM media WHERE id=?", (media_id,)).fetchone():
                raise HTTPException(404, "Медиафайл не найден")
            total = connection.execute("SELECT COUNT(*) FROM media_usages WHERE media_id=?", (media_id,)).fetchone()[0]
            rows = connection.execute(
                """SELECT u.*,c.title,c.content_type,c.status,c.version AS current_version
                   FROM media_usages u JOIN contents c ON c.id=u.content_id
                   WHERE u.media_id=? ORDER BY u.is_published DESC,c.title,u.revision_version DESC
                   LIMIT ? OFFSET ?""",
                (media_id, limit, offset),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}

    @app.post("/api/admin/media", status_code=201)
    async def upload_media(
        response: Response,
        file: UploadFile = File(...),
        alt_text: str = "",
        user: dict = Depends(require("editor", mutation=True)),
    ):
        try:
            item, deduplicated = await asyncio.to_thread(
                store_upload, file.file, file.filename or "file", settings, user["id"], alt_text=alt_text
            )
        except MediaError as error:
            raise HTTPException(error.status_code, str(error)) from error
        if deduplicated:
            response.status_code = 200
        return {**item, "deduplicated": deduplicated}

    @app.patch("/api/admin/media/{media_id}")
    def update_media(
        media_id: str,
        payload: MediaUpdate,
        user: dict = Depends(require("editor", mutation=True)),
    ):
        with transaction(settings.database_path) as connection:
            before = connection.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
            if not before:
                raise HTTPException(404, "Медиафайл не найден")
            if before["version"] != payload.version:
                raise HTTPException(409, "Медиафайл уже изменён; обновите список")
            alt_text = payload.alt_text.strip()
            if alt_text == before["alt_text"]:
                row = media_row(connection, media_id)
            else:
                connection.execute(
                    "UPDATE media SET alt_text=?,version=version+1,updated_at=? WHERE id=?",
                    (alt_text, utc_now(), media_id),
                )
                record_media_event(connection, media_id, user["id"], "metadata_update", {"field": "alt_text"})
                row = media_row(connection, media_id)
        return serialize_media(row)

    @app.post("/api/admin/media/{media_id}/replacement", status_code=201)
    async def replace_media(
        media_id: str,
        file: UploadFile = File(...),
        alt_text: str = "",
        user: dict = Depends(require("editor", mutation=True)),
    ):
        with connect(settings.database_path) as connection:
            if not connection.execute("SELECT 1 FROM media WHERE id=?", (media_id,)).fetchone():
                raise HTTPException(404, "Исходный медиафайл не найден")
        try:
            item, deduplicated = await asyncio.to_thread(
                store_upload,
                file.file,
                file.filename or "file",
                settings,
                user["id"],
                alt_text=alt_text,
                replaces_media_id=media_id,
            )
        except MediaError as error:
            raise HTTPException(error.status_code, str(error)) from error
        return {**item, "deduplicated": deduplicated, "replaces_media_id": media_id}

    @app.delete("/api/admin/media/{media_id}", status_code=204)
    def delete_media(
        media_id: str,
        version: int = Query(ge=1),
        user: dict = Depends(require("admin", mutation=True)),
    ):
        rebuild_usages(settings.database_path)
        with transaction(settings.database_path) as connection:
            row = connection.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Медиафайл не найден")
            if row["version"] != version:
                raise HTTPException(409, "Медиафайл уже изменён; обновите список")
            usages = connection.execute("SELECT COUNT(*) FROM media_usages WHERE media_id=?", (media_id,)).fetchone()[0]
            if usages:
                raise HTTPException(409, f"Файл используется в {usages} местах и не может быть удалён")
            record_media_event(connection, media_id, user["id"], "delete", {"stored_name": row["stored_name"]})
            connection.execute("DELETE FROM media WHERE id=?", (media_id,))
        resolve_media_path(settings.media_dir, row["stored_name"]).unlink(missing_ok=True)
        shutil.rmtree(settings.derivatives_dir / media_id, ignore_errors=True)
        return Response(status_code=204)

    @app.post("/api/admin/media/reindex")
    async def reindex_media(
        dry_run: bool = True,
        _: dict = Depends(require("admin", mutation=True)),
    ):
        return await asyncio.to_thread(
            index_library,
            settings.database_path,
            settings.media_dir,
            missing_report=settings.root / "outputs" / "missing-legacy-media.csv",
            dry_run=dry_run,
        )

    @app.get("/api/admin/media-issues")
    def list_media_issues(
        q: str = "",
        status: str | None = Query(default=None, pattern="^(pending|resolved)$"),
        limit: int = Query(48, ge=1, le=200),
        offset: int = Query(0, ge=0),
        _: dict = Depends(require("viewer")),
    ):
        conditions = []
        params: list[Any] = []
        if q.strip():
            conditions.append("(LOWER(i.source_url) LIKE ? OR LOWER(i.source_directory) LIKE ? OR LOWER(i.error) LIKE ?)")
            needle = f"%{q.strip().lower()}%"
            params.extend([needle, needle, needle])
        if status:
            conditions.append("i.status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with connect(settings.database_path) as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM missing_media_issues i{where}", params).fetchone()[0]
            rows = connection.execute(
                f"""SELECT i.*,COUNT(ic.content_id) AS content_count
                    FROM missing_media_issues i LEFT JOIN missing_media_issue_contents ic ON ic.issue_id=i.id
                    {where} GROUP BY i.id ORDER BY (i.status='pending') DESC,i.source_url LIMIT ? OFFSET ?""",
                [*params, limit, offset],
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["contents"] = [
                    dict(content) for content in connection.execute(
                        """SELECT c.id,c.title,c.content_type,c.status FROM missing_media_issue_contents ic
                           JOIN contents c ON c.id=ic.content_id WHERE ic.issue_id=? ORDER BY c.title""",
                        (row["id"],),
                    ).fetchall()
                ]
                items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.post("/api/admin/media-issues/{issue_id}/replacement", status_code=201)
    async def resolve_media_issue(
        issue_id: str,
        file: UploadFile = File(...),
        alt_text: str = "",
        user: dict = Depends(require("editor", mutation=True)),
    ):
        with connect(settings.database_path) as connection:
            issue = connection.execute("SELECT * FROM missing_media_issues WHERE id=?", (issue_id,)).fetchone()
        if not issue:
            raise HTTPException(404, "Запись об утраченном файле не найдена")
        try:
            item, deduplicated = await asyncio.to_thread(
                store_upload, file.file, file.filename or "file", settings, user["id"], alt_text=alt_text
            )
        except MediaError as error:
            raise HTTPException(error.status_code, str(error)) from error
        with transaction(settings.database_path) as connection:
            current = connection.execute("SELECT * FROM missing_media_issues WHERE id=?", (issue_id,)).fetchone()
            connection.execute(
                """UPDATE missing_media_issues SET status='resolved',replacement_media_id=?,
                   version=version+1,updated_at=?,resolved_at=? WHERE id=?""",
                (item["id"], utc_now(), utc_now(), issue_id),
            )
            record_media_event(
                connection, item["id"], user["id"], "resolve_missing",
                {"issue_id": issue_id, "source_url": current["source_url"]},
            )
        return {"media": item, "deduplicated": deduplicated, "issue_id": issue_id}

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
        acceptance = acceptance_summary(settings.database_path)
        return {
            "runs": runs,
            "totals": totals,
            "by_type": by_type,
            "review_by_type": review_by_type,
            "source": source.name,
            "acceptance": acceptance,
        }

    @app.post("/api/admin/migration/audits", status_code=202)
    def create_migration_audit(
        payload: MigrationAuditPayload | None = None,
        user: dict = Depends(require("publisher", mutation=True)),
    ):
        payload = payload or MigrationAuditPayload()
        scope = {
            key: value for key, value in {
                "content_type": TYPE_ALIASES.get(payload.content_type, payload.content_type)
                if payload.content_type else None,
                "year": payload.year,
                "check_external": payload.check_external,
            }.items() if value is not None
        }
        return queue_audit(settings.database_path, actor_id=user["id"], scope=scope)

    @app.get("/api/admin/migration/audits/{run_id}")
    def migration_audit(run_id: str, _: dict = Depends(require("viewer"))):
        try:
            return get_audit_run(settings.database_path, run_id)
        except AcceptanceError as error:
            raise acceptance_http_error(error) from error

    @app.post("/api/admin/contents/{content_id}/migration-audit")
    async def audit_single_content(
        content_id: str,
        payload: MigrationAuditPayload | None = None,
        user: dict = Depends(require("editor", mutation=True)),
    ):
        payload = payload or MigrationAuditPayload()
        with connect(settings.database_path) as connection:
            content_or_404(connection, content_id)
        run = queue_audit(
            settings.database_path,
            actor_id=user["id"],
            scope={"content_id": content_id, "check_external": payload.check_external},
        )
        try:
            return await asyncio.to_thread(
                execute_audit_run,
                settings.database_path,
                settings.schema_path,
                settings.media_dir,
                settings.site_dir,
                run["id"],
                check_external=payload.check_external,
            )
        except AcceptanceError as error:
            raise acceptance_http_error(error) from error

    @app.get("/api/admin/migration/issues")
    def migration_issues(
        severity: str | None = None,
        code: str | None = None,
        status: str | None = "open",
        content_type: str | None = None,
        year: int | None = Query(default=None, ge=1900, le=2100),
        q: str = "",
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        _: dict = Depends(require("viewer")),
    ):
        try:
            return list_issues(
                settings.database_path,
                severity=severity,
                code=code,
                status=status,
                content_type=TYPE_ALIASES.get(content_type, content_type) if content_type else None,
                year=year,
                q=q,
                limit=limit,
                offset=offset,
            )
        except AcceptanceError as error:
            raise acceptance_http_error(error) from error

    @app.get("/api/admin/migration/batches")
    def migration_batches(status: str | None = None, _: dict = Depends(require("viewer"))):
        return list_batches(settings.database_path, status=status)

    @app.post("/api/admin/migration/batches", status_code=201)
    def create_migration_batch(
        payload: MigrationBatchCreatePayload,
        user: dict = Depends(require("publisher", mutation=True)),
    ):
        try:
            return create_batch(
                settings.database_path,
                name=payload.name,
                kind=payload.kind,
                content_ids=payload.content_ids,
                actor_id=user["id"],
                filters=payload.filters,
                sample_rate=payload.sample_rate,
            )
        except AcceptanceError as error:
            raise acceptance_http_error(error) from error

    @app.post("/api/admin/migration/batches/pilot", status_code=201)
    def create_migration_pilot(user: dict = Depends(require("publisher", mutation=True))):
        try:
            return create_pilot_batch(settings.database_path, actor_id=user["id"])
        except AcceptanceError as error:
            raise acceptance_http_error(error) from error

    @app.get("/api/admin/migration/batches/{batch_id}")
    def migration_batch(batch_id: str, _: dict = Depends(require("viewer"))):
        try:
            return get_batch(settings.database_path, batch_id)
        except AcceptanceError as error:
            raise acceptance_http_error(error) from error

    @app.patch("/api/admin/migration/batches/{batch_id}/items/{content_id}")
    def migration_batch_item(
        batch_id: str,
        content_id: str,
        payload: MigrationBatchItemPayload,
        user: dict = Depends(require("editor", mutation=True)),
    ):
        try:
            return update_batch_item(
                settings.database_path,
                batch_id=batch_id,
                content_id=content_id,
                item_version=payload.version,
                actor_id=user["id"],
                manual_reviewed=payload.manual_reviewed,
                disposition=payload.disposition,
                warning_acknowledgements=payload.warning_acknowledgements,
                note=payload.note,
            )
        except AcceptanceError as error:
            raise acceptance_http_error(error) from error

    @app.post("/api/admin/migration/batches/{batch_id}/submit")
    def submit_migration_batch(
        batch_id: str,
        payload: MigrationBatchVersionPayload,
        user: dict = Depends(require("editor", mutation=True)),
    ):
        try:
            return submit_batch(
                settings.database_path, batch_id=batch_id,
                version=payload.version, actor_id=user["id"],
            )
        except AcceptanceError as error:
            raise acceptance_http_error(error) from error

    @app.post("/api/admin/migration/batches/{batch_id}/finalize")
    def finalize_migration_batch(
        batch_id: str,
        payload: MigrationBatchFinalizePayload,
        user: dict = Depends(require("publisher", mutation=True)),
    ):
        try:
            return finalize_batch(
                settings.database_path,
                batch_id=batch_id,
                version=payload.version,
                actor_id=user["id"],
                warning_acknowledgements=payload.warning_acknowledgements,
            )
        except AcceptanceError as error:
            raise acceptance_http_error(error) from error

    @app.post("/api/admin/migration/batches/{batch_id}/cancel")
    def cancel_migration_batch(
        batch_id: str,
        payload: MigrationBatchVersionPayload,
        user: dict = Depends(require("publisher", mutation=True)),
    ):
        try:
            return cancel_batch(
                settings.database_path, batch_id=batch_id,
                version=payload.version, actor_id=user["id"],
            )
        except AcceptanceError as error:
            raise acceptance_http_error(error) from error

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
            rebuild_usages(settings.database_path)
            return {
                "dry_run": False,
                "records_found": full_result["records_found"] + leaflet_result["records_found"],
                "imported": full_result["imported"] + leaflet_result["imported"],
                "updated": full_result["updated"] + leaflet_result["updated"],
                "skipped": full_result["skipped"] + leaflet_result["skipped"],
                "errors": full_result["errors"] + leaflet_result["errors"],
                "broken_pages": full_result["broken"],
            }
        result = run_import(
            settings.database_path, settings.legacy_sections_path,
            dry_run=dry_run, media_manifest=manifest_path, actor_id=user["id"],
        )
        if not dry_run:
            rebuild_usages(settings.database_path)
        return result

    def public_context(active_nav: str, page_title: str, **values: Any) -> dict[str, Any]:
        return {
            **base_context(settings.database_path, active_nav=active_nav, page_title=page_title),
            **values,
        }

    def render_public(request: Request, template_name: str, context: dict[str, Any], status_code: int = 200):
        context = dict(context)
        context["seo"] = build_seo_context(
            settings,
            path=request.url.path,
            query_params=request.query_params,
            page_title=str(context.get("page_title") or SITE_NAME),
            contact=context.get("contact") or {},
            item=context.get("item"),
            noindex=bool(context.get("seo_noindex")) or status_code >= 400 or request.url.path == "/search",
            preview=bool(context.get("preview_mode")),
        )
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

    @app.exception_handler(Exception)
    async def public_server_error(request: Request, error: Exception):
        LOGGER.error(
            "Unhandled request error at %s", request.url.path,
            exc_info=(type(error), error, error.__traceback__),
        )
        if request.url.path == "/api" or request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Внутренняя ошибка сервера"}, status_code=500)
        try:
            return render_public(
                request,
                "500.html",
                public_context("", "Ошибка сервера", seo_noindex=True),
                status_code=500,
            )
        except Exception:
            return HTMLResponse(
                "<!doctype html><html lang='ru'><title>Ошибка сервера</title>"
                "<h1>Сайт временно недоступен</h1></html>",
                status_code=500,
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

    @app.get("/__stage9-error-test", include_in_schema=False)
    def seo_error_test(request: Request):
        if settings.environment != "test":
            return render_not_found(request)
        raise RuntimeError("stage 9 public error test")

    @app.get("/search", response_class=HTMLResponse, include_in_schema=False)
    def public_search_page(
        request: Request,
        q: str = "",
        content_type: str | None = Query(default=None, alias="type"),
        page: str = "1",
    ):
        page_number = parse_archive_page(page)
        if page_number is None:
            return render_not_found(request)
        query = re.sub(r"\s+", " ", q).strip()
        result = None
        error_message = ""
        status_code = 200
        if content_type and content_type not in SEARCHABLE_TYPES:
            error_message = "Неизвестный тип материала."
            status_code = 400
        elif query:
            try:
                result = search_public(
                    settings.database_path,
                    query,
                    content_type=content_type,
                    page=page_number,
                    per_page=20,
                )
            except SearchError as error:
                error_message = str(error)
                status_code = 400
            if result and result.invalid_page:
                return render_not_found(request)
            if result:
                for item in result.items:
                    item["date"] = format_date(item.get("published_at"))
        return render_public(
            request,
            "search.html",
            public_context(
                "", "Поиск", query=query, selected_type=content_type or "",
                search_result=result, search_error=error_message,
                search_types=[
                    {"value": value, "label": SEARCH_TYPE_LABELS[value]}
                    for value in sorted(SEARCHABLE_TYPES, key=lambda key: SEARCH_TYPE_LABELS[key])
                ],
                seo_noindex=True,
            ),
            status_code=status_code,
        )

    @app.get("/sitemap.xml", include_in_schema=False)
    def public_sitemap():
        return Response(
            content=sitemap_xml(settings),
            media_type="application/xml",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.get("/robots.txt", include_in_schema=False)
    def public_robots():
        return PlainTextResponse(
            robots_text(settings),
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/rss.xml", include_in_schema=False)
    def public_rss():
        return Response(
            content=rss_xml(settings),
            media_type="application/rss+xml",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.get("/social-preview/content/{content_id}/v{version}.jpg", include_in_schema=False)
    def public_social_preview(content_id: str, version: int):
        try:
            path = social_preview_path(settings, content_id, version)
        except SocialPreviewError as error:
            raise HTTPException(404, str(error)) from error
        return FileResponse(
            path,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/social-preview/site.jpg", include_in_schema=False)
    def public_site_social_preview():
        return FileResponse(
            site_social_preview_path(settings),
            media_type="image/jpeg",
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "X-Content-Type-Options": "nosniff",
            },
        )

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
        raw = published_item(settings.database_path, slug, ("page",))
        if raw is None:
            return render_not_found(request)
        placement = str((raw.get("data") or {}).get("placement") or "standalone")
        target = {
            "about_history": "/about",
            "about_shrine": "/about",
            "school_home": "/school",
            "schedule_info": "/schedule",
        }.get(placement)
        if target:
            return RedirectResponse(target, status_code=301)
        item = content_view(raw)
        return render_public(
            request,
            "detail.html",
            public_context(
                "about", item["title"], item=item, back_url="/about",
                related_items=[
                    content_view(related)
                    for related in published_related_content(settings.database_path, raw)
                ],
            ),
        )

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

    @app.get("/media-derivatives/{media_id}/{variant}.webp", include_in_schema=False)
    def media_derivative(media_id: str, variant: str):
        try:
            path = ensure_derivative(settings, media_id, variant)
        except MediaError as error:
            raise HTTPException(error.status_code, str(error)) from error
        return FileResponse(
            path,
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/media/{media_path:path}", include_in_schema=False)
    def media_original(media_path: str):
        try:
            path = resolve_media_path(settings.media_dir, media_path)
        except MediaError as error:
            raise HTTPException(error.status_code, str(error)) from error
        if not path.is_file():
            raise HTTPException(404, "Медиафайл не найден")
        with connect(settings.database_path) as connection:
            row = connection.execute("SELECT * FROM media WHERE stored_name=?", (media_path,)).fetchone()
        media_type = row["mime_type"] if row else None
        headers = {"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"}
        if row and row["kind"] == "document" and row["mime_type"] != "application/pdf":
            safe_name = row["original_name"].replace('"', "")
            headers["Content-Disposition"] = f'attachment; filename="{safe_name.encode("ascii", "ignore").decode() or "document"}"'
        return FileResponse(path, media_type=media_type, headers=headers)

    app.mount("/assets", StaticFiles(directory=settings.site_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def public_unknown(request: Request, path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return render_not_found(request)

    return app


app = create_app()
