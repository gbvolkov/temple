from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Iterable

import olefile
import pypdfium2 as pdfium
import av
from PIL import Image, ImageOps, UnidentifiedImageError

from .config import Settings
from .db import connect, transaction, utc_now


Image.MAX_IMAGE_PIXELS = 40_000_000
MEDIA_NAMESPACE = uuid.UUID("adb3774d-6858-54ae-a389-19907467c72a")
CHUNK_SIZE = 1024 * 1024
DERIVATIVE_SIZES = {"thumb": (320, 320), "web": (1600, 1600)}

FORMAT_INFO = {
    "jpeg": ("image/jpeg", ".jpg", "image"),
    "png": ("image/png", ".png", "image"),
    "webp": ("image/webp", ".webp", "image"),
    "pdf": ("application/pdf", ".pdf", "document"),
    "mp4": ("video/mp4", ".mp4", "video"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx", "document"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx", "document"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx", "document"),
    "doc": ("application/msword", ".doc", "document"),
    "xls": ("application/vnd.ms-excel", ".xls", "document"),
    "ppt": ("application/vnd.ms-powerpoint", ".ppt", "document"),
    "csv": ("text/csv", ".csv", "document"),
    "txt": ("text/plain", ".txt", "document"),
}
ALLOWED_EXTENSIONS = {info[1] for info in FORMAT_INFO.values()} | {".jpeg"}
IMAGE_FORMATS = {"JPEG": "jpeg", "PNG": "png", "WEBP": "webp"}
OOXML_MARKERS = {"word/": "docx", "xl/": "xlsx", "ppt/": "pptx"}
OLE_STREAMS = {"WordDocument": "doc", "Workbook": "xls", "Book": "xls", "PowerPoint Document": "ppt"}


class MediaError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Inspection:
    format: str
    mime_type: str
    extension: str
    kind: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    metadata: dict[str, Any] | None = None


def safe_original_name(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", Path(value or "file").name).strip()
    normalized = re.sub(r"[\x00-\x1f\x7f]", "", normalized)
    return normalized[:240] or "file"


def media_url(stored_name: str) -> str:
    return "/media/" + PurePosixPath(stored_name).as_posix().lstrip("/")


def stored_name_from_url(value: str) -> str | None:
    if not isinstance(value, str) or not value.startswith("/media/"):
        return None
    raw = value.removeprefix("/media/").split("?", 1)[0].split("#", 1)[0]
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def resolve_media_path(media_dir: Path, stored_name: str) -> Path:
    candidate = (media_dir / Path(*PurePosixPath(stored_name).parts)).resolve()
    root = media_dir.resolve()
    if candidate != root and root not in candidate.parents:
        raise MediaError("Некорректный путь медиаресурса", 400)
    return candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_image(path: Path) -> Inspection:
    try:
        with Image.open(path) as image:
            detected = IMAGE_FORMATS.get(image.format or "")
            if not detected:
                raise MediaError("Разрешены только JPG, PNG и WebP", 415)
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > 40_000_000:
                raise MediaError("Изображение превышает ограничение 40 мегапикселей", 413)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise MediaError("Файл не является корректным JPG, PNG или WebP", 415) from error
    mime, extension, kind = FORMAT_INFO[detected]
    return Inspection(detected, mime, extension, kind, width=width, height=height)


def _inspect_pdf(path: Path) -> Inspection:
    try:
        document = pdfium.PdfDocument(str(path))
        page_count = len(document)
        if page_count < 1 or page_count > 1000:
            raise MediaError("PDF должен содержать от 1 до 1000 страниц", 422)
        first_page = document[0]
        page_width, page_height = first_page.get_size()
        first_page.close()
        if (
            page_width <= 0
            or page_height <= 0
            or page_width > 20_000
            or page_height > 20_000
        ):
            raise MediaError("Размер страницы PDF превышает безопасное ограничение", 413)
        document.close()
    except MediaError:
        raise
    except Exception as error:
        raise MediaError("Файл не является корректным PDF", 415) from error
    mime, extension, kind = FORMAT_INFO["pdf"]
    return Inspection(
        "pdf",
        mime,
        extension,
        kind,
        metadata={"page_count": page_count, "first_page_points": [page_width, page_height]},
    )


def _inspect_ooxml(path: Path) -> Inspection:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "[Content_Types].xml" not in names:
                raise MediaError("Некорректный файл Microsoft Office", 415)
            lowered = [name.lower() for name in names]
            if any(name.endswith("vbaproject.bin") for name in lowered):
                raise MediaError("Файлы Microsoft Office с макросами запрещены", 415)
            total = sum(info.file_size for info in archive.infolist())
            compressed = max(1, sum(info.compress_size for info in archive.infolist()))
            if total > 500 * 1024 * 1024 or total / compressed > 200:
                raise MediaError("Содержимое Office-архива превышает безопасные ограничения", 413)
            detected = next((value for marker, value in OOXML_MARKERS.items() if any(name.startswith(marker) for name in lowered)), None)
    except MediaError:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise MediaError("Некорректный файл Microsoft Office", 415) from error
    if not detected:
        raise MediaError("Не удалось определить тип файла Microsoft Office", 415)
    mime, extension, kind = FORMAT_INFO[detected]
    return Inspection(detected, mime, extension, kind)


def _inspect_ole(path: Path) -> Inspection:
    try:
        with olefile.OleFileIO(str(path)) as compound:
            streams = {"/".join(parts) for parts in compound.listdir()}
            lowered = {name.lower() for name in streams}
            if any("vba" in name or "_vba_project_cur" in name for name in lowered):
                raise MediaError("Старые Office-файлы с обнаруженными макросами запрещены", 415)
            detected = next((value for marker, value in OLE_STREAMS.items() if marker in streams), None)
    except MediaError:
        raise
    except (OSError, IOError, TypeError) as error:
        raise MediaError("Некорректный файл Microsoft Office 97–2003", 415) from error
    if not detected:
        raise MediaError("Не удалось определить тип старого Office-файла", 415)
    mime, extension, kind = FORMAT_INFO[detected]
    return Inspection(detected, mime, extension, kind, metadata={"legacy_office": True})


def _inspect_text(path: Path, expected: str) -> Inspection:
    body = path.read_bytes()
    if b"\x00" in body:
        raise MediaError("Текстовый файл содержит бинарные данные", 415)
    encoding = None
    for candidate in ("utf-8-sig", "cp1251"):
        try:
            body.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    if encoding is None:
        raise MediaError("Текстовый файл должен быть UTF-8 или Windows-1251", 415)
    mime, extension, kind = FORMAT_INFO[expected]
    return Inspection(expected, mime, extension, kind, metadata={"encoding": encoding})


def _ffprobe(path: Path) -> tuple[int | None, int | None, float | None]:
    try:
        with av.open(str(path), mode="r") as container:
            stream = next((entry for entry in container.streams if entry.type == "video"), None)
            if stream is None:
                raise MediaError("MP4 не содержит видеопотока", 415)
            codec = stream.codec_context
            duration = float(container.duration / av.time_base) if container.duration is not None else None
            width = int(codec.width) if codec.width else None
            height = int(codec.height) if codec.height else None
            if not width or not height:
                raise MediaError("Не удалось определить размер видеокадра", 415)
            return width, height, duration
    except MediaError:
        raise
    except (av.error.FFmpegError, OSError, ValueError) as error:
        raise MediaError("MP4 не прошёл проверку контейнера", 415) from error


def inspect_file(path: Path, original_name: str) -> Inspection:
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise MediaError("Формат файла не разрешён", 415)
    header = path.read_bytes()[:32]
    if header.startswith(b"%PDF-"):
        inspection = _inspect_pdf(path)
    elif len(header) >= 12 and header[4:8] == b"ftyp":
        width, height, duration = _ffprobe(path)
        mime, canonical, kind = FORMAT_INFO["mp4"]
        inspection = Inspection("mp4", mime, canonical, kind, width, height, duration)
    elif header.startswith(b"PK\x03\x04"):
        inspection = _inspect_ooxml(path)
    elif header.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        inspection = _inspect_ole(path)
    elif extension in {".csv", ".txt"}:
        inspection = _inspect_text(path, extension[1:])
    else:
        inspection = _inspect_image(path)
    accepted = {inspection.extension}
    if inspection.extension == ".jpg":
        accepted.add(".jpeg")
    if extension not in accepted:
        raise MediaError(
            f"Содержимое файла соответствует {inspection.extension}, а расширение — {extension}", 415
        )
    return inspection


def max_bytes_for(settings: Settings, kind: str) -> int:
    return {
        "image": settings.max_image_bytes,
        "video": settings.max_video_bytes,
        "document": settings.max_document_bytes,
    }[kind]


def _metadata_json(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def record_media_event(
    connection: sqlite3.Connection,
    media_id: str | None,
    actor_id: str | None,
    action: str,
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        "INSERT INTO media_events(id,media_id,actor_id,action,details_json,created_at) VALUES(?,?,?,?,?,?)",
        (str(uuid.uuid4()), media_id, actor_id, action, _metadata_json(details), utc_now()),
    )


def media_row(connection: sqlite3.Connection, media_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """SELECT m.*,
                  COUNT(DISTINCT u.content_id) AS content_count,
                  COUNT(u.media_id) AS usage_count
           FROM media m LEFT JOIN media_usages u ON u.media_id=m.id
           WHERE m.id=? GROUP BY m.id""",
        (media_id,),
    ).fetchone()


def serialize_media(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    metadata = item.pop("metadata_json", "{}")
    item["metadata"] = json.loads(metadata or "{}")
    item["url"] = media_url(item["stored_name"])
    if item.get("kind") == "image":
        item["thumbnail_url"] = f"/media-derivatives/{item['id']}/thumb.webp"
        item["preview_url"] = f"/media-derivatives/{item['id']}/web.webp"
    elif item.get("mime_type") == "application/pdf":
        item["thumbnail_url"] = f"/media-derivatives/{item['id']}/thumb.webp"
        item["preview_url"] = f"/media-derivatives/{item['id']}/web.webp"
    else:
        item["thumbnail_url"] = None
        item["preview_url"] = None
    item["usage_count"] = int(item.get("usage_count") or 0)
    item["content_count"] = int(item.get("content_count") or 0)
    return item


def iter_media_references(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            token = str(key).replace("~", "~0").replace("/", "~1")
            yield from iter_media_references(child, f"{path}/{token}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_media_references(child, f"{path}/{index}")
    elif isinstance(value, str) and stored_name_from_url(value):
        yield value, path or "/"


def refresh_content_usages(connection: sqlite3.Connection, content_id: str) -> int:
    connection.execute("DELETE FROM media_usages WHERE content_id=?", (content_id,))
    content = connection.execute(
        "SELECT data_json,published_version FROM contents WHERE id=?", (content_id,)
    ).fetchone()
    if not content:
        return 0
    now = utc_now()
    inserted = 0

    def insert_references(payload: Any, revision_version: int, is_published: bool) -> None:
        nonlocal inserted
        for url, field_path in iter_media_references(payload):
            stored_name = stored_name_from_url(url)
            row = connection.execute("SELECT id FROM media WHERE stored_name=?", (stored_name,)).fetchone()
            if not row:
                continue
            connection.execute(
                """INSERT OR IGNORE INTO media_usages(
                     media_id,content_id,revision_version,field_path,is_published,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (row["id"], content_id, revision_version, field_path, int(is_published), now),
            )
            inserted += 1

    insert_references(json.loads(content["data_json"]), 0, False)
    for revision in connection.execute(
        "SELECT version,snapshot_json FROM revisions WHERE content_id=?", (content_id,)
    ).fetchall():
        insert_references(
            json.loads(revision["snapshot_json"]),
            int(revision["version"]),
            revision["version"] == content["published_version"],
        )
    return inserted


def rebuild_usages(database_path: Path) -> int:
    with transaction(database_path) as connection:
        connection.execute("DELETE FROM media_usages")
        content_ids = [row["id"] for row in connection.execute("SELECT id FROM contents ORDER BY id")]
        return sum(refresh_content_usages(connection, content_id) for content_id in content_ids)


def media_reference_problems(
    connection: sqlite3.Connection,
    content: dict[str, Any],
    media_dir: Path,
) -> list[dict[str, str]]:
    problems: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for url, field_path in iter_media_references(content):
        key = (url, field_path)
        if key in seen:
            continue
        seen.add(key)
        stored_name = stored_name_from_url(url)
        row = connection.execute(
            "SELECT id,status,kind,alt_text FROM media WHERE stored_name=?", (stored_name,)
        ).fetchone()
        if not row:
            problems.append({"url": url, "field": field_path, "reason": "Файл не зарегистрирован в медиатеке"})
            continue
        path = resolve_media_path(media_dir, stored_name or "")
        if row["status"] != "ready" or not path.is_file():
            problems.append({"url": url, "field": field_path, "reason": "Файл отсутствует или повреждён"})
    return problems


def _insert_or_update_media(
    connection: sqlite3.Connection,
    *,
    media_id: str,
    original_name: str,
    stored_name: str,
    size_bytes: int,
    sha256: str,
    inspection: Inspection | None,
    source: str,
    status: str,
    error: str | None = None,
) -> None:
    existing = connection.execute("SELECT id,created_at FROM media WHERE stored_name=?", (stored_name,)).fetchone()
    now = utc_now()
    mime_type = inspection.mime_type if inspection else "application/octet-stream"
    kind = inspection.kind if inspection else "document"
    metadata = dict(inspection.metadata or {}) if inspection else {}
    if error:
        metadata["validation_error"] = error
    values = (
        original_name, mime_type, size_bytes, sha256, kind, source, status,
        inspection.width if inspection else None,
        inspection.height if inspection else None,
        inspection.duration_seconds if inspection else None,
        now, _metadata_json(metadata),
    )
    if existing:
        connection.execute(
            """UPDATE media SET original_name=?,mime_type=?,size_bytes=?,sha256=?,kind=?,source=?,status=?,
               width=?,height=?,duration_seconds=?,updated_at=?,metadata_json=? WHERE id=?""",
            (*values, existing["id"]),
        )
    else:
        connection.execute(
            """INSERT INTO media(
                 id,original_name,stored_name,mime_type,size_bytes,alt_text,uploaded_by,created_at,
                 sha256,kind,source,status,width,height,duration_seconds,version,updated_at,
                 replaces_media_id,metadata_json
               ) VALUES(?,?,?,?,?,'',NULL,?,?,?,?,?,?,?,?,1,?,NULL,?)""",
            (
                media_id, original_name, stored_name, mime_type, size_bytes, now,
                sha256, kind, source, status,
                inspection.width if inspection else None,
                inspection.height if inspection else None,
                inspection.duration_seconds if inspection else None,
                now, _metadata_json(metadata),
            ),
        )


def import_missing_issues(connection: sqlite3.Connection, report_path: Path | None) -> int:
    if not report_path or not report_path.exists():
        return 0
    count = 0
    with report_path.open(encoding="utf-8-sig", newline="") as source:
        for item in csv.DictReader(source):
            source_url = str(item.get("url") or "").strip()
            if not source_url:
                continue
            issue_id = str(uuid.uuid5(MEDIA_NAMESPACE, "missing:" + source_url))
            now = utc_now()
            connection.execute(
                """INSERT INTO missing_media_issues(
                     id,source_url,error,source_directory,reference_count,status,version,created_at,updated_at
                   ) VALUES(?,?,?,?,?,'pending',1,?,?)
                   ON CONFLICT(source_url) DO UPDATE SET error=excluded.error,
                     source_directory=excluded.source_directory,reference_count=excluded.reference_count,
                     updated_at=excluded.updated_at""",
                (
                    issue_id, source_url, item.get("error") or "Файл отсутствует на старом сервере",
                    item.get("directory") or "", int(item.get("references") or 0), now, now,
                ),
            )
            related = str(item.get("materials") or "")
            for legacy_path in re.findall(r"\[([^\]]+)\]", related):
                row = connection.execute(
                    "SELECT id FROM contents WHERE legacy_url=? OR legacy_url LIKE ? LIMIT 1",
                    (legacy_path, "%" + legacy_path),
                ).fetchone()
                if row:
                    connection.execute(
                        "INSERT OR IGNORE INTO missing_media_issue_contents(issue_id,content_id) VALUES(?,?)",
                        (issue_id, row["id"]),
                    )
            count += 1
    return count


def index_library(
    database_path: Path,
    media_dir: Path,
    *,
    missing_report: Path | None = None,
    dry_run: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    files = sorted(
        path for path in media_dir.rglob("*")
        if path.is_file() and not any(part.startswith(".") for part in path.relative_to(media_dir).parts)
    )
    report: dict[str, Any] = {
        "files": len(files), "ready": 0, "invalid": 0, "created": 0, "updated": 0,
        "errors": [], "usages": 0, "missing_issues": 0, "dry_run": dry_run,
    }
    prepared: list[dict[str, Any]] = []
    connection = connect(database_path)
    try:
        if progress:
            progress({**report, "phase": "scanning", "processed_files": 0, "total_files": len(files)})
        for processed, path in enumerate(files, start=1):
            stored_name = path.relative_to(media_dir).as_posix()
            existing = connection.execute(
                "SELECT id,source,original_name FROM media WHERE stored_name=?", (stored_name,)
            ).fetchone()
            original_name = existing["original_name"] if existing and existing["original_name"] else path.name
            media_id = existing["id"] if existing else str(uuid.uuid5(MEDIA_NAMESPACE, media_url(stored_name)))
            digest = sha256_file(path)
            try:
                inspection = inspect_file(path, original_name)
                status = "ready"
                error = None
                report["ready"] += 1
            except MediaError as failure:
                inspection = None
                status = "invalid"
                error = str(failure)
                report["invalid"] += 1
                report["errors"].append({"url": media_url(stored_name), "error": error})
            if not dry_run:
                prepared.append({
                    "media_id": media_id,
                    "original_name": original_name,
                    "stored_name": stored_name,
                    "size_bytes": path.stat().st_size,
                    "sha256": digest,
                    "inspection": inspection,
                    "source": existing["source"] if existing and existing["source"] else "legacy",
                    "status": status,
                    "error": error,
                })
            report["updated" if existing else "created"] += 1
            if progress:
                progress({**report, "phase": "scanning", "processed_files": processed, "total_files": len(files)})
        if not dry_run:
            if progress:
                progress({**report, "phase": "applying", "processed_files": len(files), "total_files": len(files)})
            connection.execute("BEGIN IMMEDIATE")
            for item in prepared:
                _insert_or_update_media(connection, **item)
            report["missing_issues"] = import_missing_issues(connection, missing_report)
            connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
    if not dry_run:
        if progress:
            progress({**report, "phase": "usages", "processed_files": len(files), "total_files": len(files)})
        report["usages"] = rebuild_usages(database_path)
    return report


def store_upload(
    source: BinaryIO,
    filename: str,
    settings: Settings,
    actor_id: str,
    *,
    alt_text: str = "",
    replaces_media_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    original_name = safe_original_name(filename)
    extension = Path(original_name).suffix.lower()
    claimed_kind = "image" if extension in {".jpg", ".jpeg", ".png", ".webp"} else "video" if extension == ".mp4" else "document"
    preliminary_limit = max_bytes_for(settings, claimed_kind)
    incoming = settings.media_dir / ".incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    temporary = incoming / f"{uuid.uuid4()}.upload"
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as output:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > preliminary_limit:
                    raise MediaError("Файл превышает установленный лимит", 413)
                digest.update(chunk)
                output.write(chunk)
        inspection = inspect_file(temporary, original_name)
        if size > max_bytes_for(settings, inspection.kind):
            raise MediaError("Файл превышает лимит для своего реального типа", 413)
        checksum = digest.hexdigest()
        duplicate = None
        if replaces_media_id is None:
            with connect(settings.database_path) as connection:
                duplicate = connection.execute(
                    """SELECT m.*,COUNT(DISTINCT u.content_id) AS content_count,COUNT(u.media_id) AS usage_count
                       FROM media m LEFT JOIN media_usages u ON u.media_id=m.id
                       WHERE m.sha256=? AND m.status='ready'
                       GROUP BY m.id ORDER BY (m.source='upload') DESC,m.created_at LIMIT 1""",
                    (checksum,),
                ).fetchone()
        if duplicate and resolve_media_path(settings.media_dir, duplicate["stored_name"]).is_file():
            temporary.unlink(missing_ok=True)
            return serialize_media(duplicate), True
        media_id = str(uuid.uuid4())
        stored_name = media_id + inspection.extension
        final_path = settings.media_dir / stored_name
        os.replace(temporary, final_path)
        now = utc_now()
        try:
            with transaction(settings.database_path) as connection:
                connection.execute(
                    """INSERT INTO media(
                         id,original_name,stored_name,mime_type,size_bytes,alt_text,uploaded_by,created_at,
                         sha256,kind,source,status,width,height,duration_seconds,version,updated_at,
                         replaces_media_id,metadata_json
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,'upload','ready',?,?,?,1,?,?,?)""",
                    (
                        media_id, original_name, stored_name, inspection.mime_type, size,
                        alt_text.strip()[:300], actor_id, now, checksum, inspection.kind,
                        inspection.width, inspection.height, inspection.duration_seconds,
                        now, replaces_media_id, _metadata_json(inspection.metadata),
                    ),
                )
                record_media_event(
                    connection, media_id, actor_id, "replacement" if replaces_media_id else "upload",
                    {"replaces_media_id": replaces_media_id} if replaces_media_id else {},
                )
                row = media_row(connection, media_id)
        except Exception:
            final_path.unlink(missing_ok=True)
            raise
        item = serialize_media(row)
        if inspection.kind == "image" or inspection.mime_type == "application/pdf":
            try:
                ensure_derivative(settings, media_id, "thumb")
            except Exception:
                pass
        return item, False
    finally:
        temporary.unlink(missing_ok=True)


def derivative_path(settings: Settings, media_id: str, variant: str) -> Path:
    if variant not in DERIVATIVE_SIZES:
        raise MediaError("Неизвестный размер preview", 404)
    return settings.derivatives_dir / media_id / f"{variant}.webp"


def ensure_derivative(settings: Settings, media_id: str, variant: str) -> Path:
    target = derivative_path(settings, media_id, variant)
    if target.is_file():
        return target
    with connect(settings.database_path) as connection:
        row = connection.execute("SELECT * FROM media WHERE id=? AND status='ready'", (media_id,)).fetchone()
    if not row:
        raise MediaError("Медиафайл не найден", 404)
    source = resolve_media_path(settings.media_dir, row["stored_name"])
    if not source.is_file():
        raise MediaError("Оригинал медиафайла отсутствует", 404)
    size = DERIVATIVE_SIZES[variant]
    try:
        if row["kind"] == "image":
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                image.thumbnail(size, Image.Resampling.LANCZOS, reducing_gap=3)
                rendered = image.copy()
        elif row["mime_type"] == "application/pdf":
            document = pdfium.PdfDocument(str(source))
            page = document[0]
            page_width, page_height = page.get_size()
            render_scale = max(0.05, min(2.0, max(size) / max(page_width, page_height)))
            bitmap = page.render(scale=render_scale)
            rendered = bitmap.to_pil().convert("RGB")
            rendered.thumbnail(size, Image.Resampling.LANCZOS, reducing_gap=3)
            bitmap.close()
            page.close()
            document.close()
        else:
            raise MediaError("Для этого формата preview не создаётся", 404)
    except MediaError:
        raise
    except Exception as error:
        raise MediaError("Не удалось создать preview", 422) from error
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4()}.tmp")
    try:
        rendered.save(temporary, "WEBP", quality=82, method=6)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def warm_cache(settings: Settings, variant: str = "thumb") -> dict[str, int]:
    with connect(settings.database_path) as connection:
        ids = [
            row["id"] for row in connection.execute(
                "SELECT id FROM media WHERE status='ready' AND (kind='image' OR mime_type='application/pdf')"
            )
        ]
    ready = errors = 0
    for media_id in ids:
        try:
            ensure_derivative(settings, media_id, variant)
            ready += 1
        except MediaError:
            errors += 1
    return {"total": len(ids), "ready": ready, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="Индекс и производные файлы медиатеки CMS")
    subparsers = parser.add_subparsers(dest="command", required=True)
    defaults = Settings.from_env()
    index_parser = subparsers.add_parser("index")
    index_parser.add_argument("--dry-run", action="store_true")
    index_parser.add_argument("--missing-report", type=Path)
    index_parser.add_argument("--database", type=Path, default=defaults.database_path)
    index_parser.add_argument("--media-dir", type=Path, default=defaults.media_dir)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--database", type=Path, default=defaults.database_path)
    verify_parser.add_argument("--media-dir", type=Path, default=defaults.media_dir)
    warm_parser = subparsers.add_parser("warm-cache")
    warm_parser.add_argument("--variant", choices=tuple(DERIVATIVE_SIZES), default="thumb")
    warm_parser.add_argument("--database", type=Path, default=defaults.database_path)
    warm_parser.add_argument("--media-dir", type=Path, default=defaults.media_dir)
    warm_parser.add_argument("--derivatives-dir", type=Path, default=defaults.derivatives_dir)
    args = parser.parse_args()
    if args.command == "index":
        result = index_library(
            args.database,
            args.media_dir,
            missing_report=args.missing_report,
            dry_run=args.dry_run,
        )
    elif args.command == "verify":
        with connect(args.database) as connection:
            quick_check = [row[0] for row in connection.execute("PRAGMA quick_check")]
            foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
            ready = connection.execute("SELECT COUNT(*) FROM media WHERE status='ready'").fetchone()[0]
            invalid = connection.execute("SELECT COUNT(*) FROM media WHERE status!='ready'").fetchone()[0]
            usages = connection.execute("SELECT COUNT(*) FROM media_usages").fetchone()[0]
            originals_missing = sum(
                not resolve_media_path(args.media_dir, row["stored_name"]).is_file()
                for row in connection.execute("SELECT stored_name FROM media WHERE status='ready'")
            )
        result = {
            "ok": quick_check == ["ok"] and foreign_key_errors == 0 and invalid == 0 and originals_missing == 0,
            "ready": ready,
            "invalid": invalid,
            "missing_originals": originals_missing,
            "usages": usages,
            "quick_check": quick_check,
            "foreign_key_errors": foreign_key_errors,
        }
    else:
        settings = replace(
            defaults,
            database_path=args.database,
            media_dir=args.media_dir,
            media_derivatives_dir=args.derivatives_dir,
        )
        result = warm_cache(settings, args.variant)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
