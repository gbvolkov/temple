from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .db import connect
from .public_urls import STATIC_HASH_TARGETS, content_path
from .workflow import public_content


MOSCOW = ZoneInfo("Europe/Moscow")
MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
WEEKDAYS = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)
CONTENT_TYPE_LABELS = {
    "home_feature": "Главное",
    "news": "Новости прихода",
    "gallery": "Фотогалерея",
    "leaflet_issue": "Иннокентиевский листок",
    "clergy": "Духовенство",
    "parish_section": "Жизнь прихода",
    "page": "О храме",
    "site_contact": "Контакты",
    "service": "Богослужения",
    "video": "Видео и трансляции",
}
FALLBACK_CONTACT = {
    "address": "Москва, Бескудниковский бульвар, 1",
    "metro": "Верхние Лихоборы, северный вестибюль, выход №3",
    "phone": "+7 (499) 480-09-89",
    "email": "svtinnokentiy2025@yandex.ru",
    "social_links": [
        {"network": "telegram", "url": "https://t.me/sv_innokenty", "enabled": True},
        {"network": "vk", "url": "https://vk.com/club37731945", "enabled": True},
    ],
}


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        instant = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=MOSCOW)
    return instant.astimezone(MOSCOW)


def format_date(value: Any, *, weekday: bool = False) -> str:
    instant = parse_datetime(value)
    if instant is None:
        return ""
    prefix = f"{WEEKDAYS[instant.weekday()]}, " if weekday else ""
    return f"{prefix}{instant.day} {MONTHS[instant.month - 1]} {instant.year}"


def format_short_date(value: Any) -> str:
    instant = parse_datetime(value)
    return instant.strftime("%d.%m") if instant else ""


def format_time(value: Any) -> str:
    instant = parse_datetime(value)
    return instant.strftime("%H:%M") if instant else ""


def phone_href(value: str) -> str:
    return "tel:" + "".join(character for character in value if character.isdigit() or character == "+")


def asset_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    if value.startswith("assets/"):
        return "/" + value
    if value.startswith(("/assets/", "/media/")):
        return value
    return ""


def external_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parsed = urlsplit(value.strip())
    return value.strip() if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def file_url(value: Any) -> str:
    return asset_url(value)


def plain_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(filter(None, (plain_content(item) for item in value)))
    if isinstance(value, dict):
        return plain_content(value.get("text") or value.get("value") or value.get("body") or "")
    return ""


def paragraphs(value: Any) -> list[str]:
    normalized = plain_content(value).strip()
    return [paragraph.strip() for paragraph in normalized.split("\n\n") if paragraph.strip()]


def published_items(database_path: Path, content_type: str, *, limit: int = 200) -> list[dict[str, Any]]:
    with connect(database_path) as connection:
        rows = connection.execute(
            """SELECT * FROM contents
               WHERE published_version IS NOT NULL AND status NOT IN ('archived','trash')
                 AND content_type=?
               ORDER BY COALESCE(published_at,updated_at) DESC LIMIT ?""",
            (content_type, limit),
        ).fetchall()
        return [public_content(connection, row) for row in rows]


def published_by_slug(database_path: Path, slug: str) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        row = connection.execute(
            """SELECT * FROM contents
               WHERE published_version IS NOT NULL AND status NOT IN ('archived','trash')
                 AND (published_slug=? OR slug=?)
               LIMIT 1""",
            (slug, slug),
        ).fetchone()
        return public_content(connection, row) if row else None


def published_item(database_path: Path, slug: str, allowed_types: Iterable[str]) -> dict[str, Any] | None:
    allowed = tuple(allowed_types)
    if not allowed:
        return None
    placeholders = ",".join("?" for _ in allowed)
    with connect(database_path) as connection:
        row = connection.execute(
            f"""SELECT * FROM contents
                WHERE published_slug=? AND published_version IS NOT NULL
                  AND status NOT IN ('archived','trash')
                  AND content_type IN ({placeholders})""",
            (slug, *allowed),
        ).fetchone()
        return public_content(connection, row) if row else None


def is_school_item(item: dict[str, Any]) -> bool:
    """Return whether a published material belongs to the Sunday-school section."""
    legacy_url = str(item.get("legacy_url") or "").lower()
    slug = str(item.get("slug") or "").lower()
    category = str((item.get("data") or {}).get("category") or "").lower()
    return (
        legacy_url.startswith("/voskresnaya-shkola/")
        or slug.startswith("voskresnaya-shkola-")
        or "воскресн" in category and "школ" in category
    )


def content_view(item: dict[str, Any]) -> dict[str, Any]:
    data = item.get("data") or {}
    date_value = data.get("publication_date") or data.get("event_date") or data.get("starts_at")
    photos = []
    source_photos = data.get("photos") or data.get("legacy_images") or []
    for photo in source_photos if isinstance(source_photos, list) else []:
        if not isinstance(photo, dict):
            continue
        image = asset_url(photo.get("image"))
        if image:
            photos.append({
                "image": image,
                "caption": photo.get("alt") or photo.get("caption") or item.get("title", ""),
            })
    body = data.get("body") or data.get("biography") or data.get("body_text") or ""
    cover = asset_url(data.get("cover") or data.get("photo"))
    return {
        **item,
        "data": data,
        "url": content_path(item["content_type"], item["slug"]),
        "label": CONTENT_TYPE_LABELS.get(item["content_type"], "Материал"),
        "summary": data.get("summary") or data.get("note") or "",
        "cover": cover,
        "cover_alt": data.get("cover_alt") or item.get("title", ""),
        "date": format_date(date_value),
        "year": format_date(date_value).rsplit(" ", 1)[-1] if format_date(date_value) else "",
        "time": format_time(data.get("starts_at")),
        "body_paragraphs": paragraphs(body),
        "photos": photos,
        "pdf": file_url(data.get("pdf")),
    }


def contact_context(item: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(FALLBACK_CONTACT)
    if item:
        data.update({key: value for key, value in (item.get("data") or {}).items() if value not in (None, "", [])})
    data["phone_href"] = phone_href(data.get("phone", ""))
    links = []
    labels = {"telegram": "Telegram", "vk": "ВКонтакте", "youtube": "YouTube", "other": "Ссылка"}
    for link in data.get("social_links", []):
        url = external_url(link.get("url")) if isinstance(link, dict) else ""
        if url and link.get("enabled", True):
            links.append({"label": labels.get(link.get("network"), labels["other"]), "url": url})
    data["links"] = links
    return data


def active_feature(features: list[dict[str, Any]], news: list[dict[str, Any]]) -> dict[str, Any] | None:
    now = datetime.now(UTC)
    active = []
    for item in features:
        data = item.get("data") or {}
        starts = parse_datetime(data.get("starts_at"))
        ends = parse_datetime(data.get("ends_at"))
        if starts and starts.astimezone(UTC) > now:
            continue
        if ends and ends.astimezone(UTC) < now:
            continue
        active.append(item)
    if active:
        return max(active, key=lambda item: (int((item.get("data") or {}).get("priority") or 0), item.get("published_at") or ""))
    return next((item for item in news if (item.get("data") or {}).get("featured")), None)


def feature_href(item: dict[str, Any] | None, linked: dict[str, Any] | None = None) -> str:
    if not item:
        return ""
    data = item.get("data") or {}
    target = str(data.get("target_url") or "").strip()
    if target.startswith("#/"):
        target = "/" + target
    if target in STATIC_HASH_TARGETS:
        target = STATIC_HASH_TARGETS[target]
    if target.startswith("/#/news/"):
        return "/news/" + target.removeprefix("/#/news/").split("?", 1)[0].split("#", 1)[0]
    if target.startswith("/#/content/"):
        return content_path(linked["content_type"], linked["slug"]) if linked else ""
    if target.startswith("/") and not target.startswith("//"):
        return target
    if external_url(target):
        return target
    return content_path(linked["content_type"], linked["slug"]) if linked else content_path(item["content_type"], item["slug"])


def next_service(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    now = datetime.now(UTC)
    candidates = []
    for item in items:
        starts = parse_datetime((item.get("data") or {}).get("starts_at"))
        if starts and starts.astimezone(UTC) >= now:
            candidates.append((starts, item))
    if not candidates:
        return None
    starts, item = min(candidates, key=lambda entry: entry[0])
    return {
        "item": item,
        "date": format_date(starts.isoformat(), weekday=True),
        "header_date": f"{starts.day} {MONTHS[starts.month - 1]}",
        "short_date": starts.strftime("%d.%m"),
        "time": starts.strftime("%H:%M"),
        "title": item["title"],
        "note": (item.get("data") or {}).get("note") or "Подробности опубликованы в расписании.",
    }


def base_context(database_path: Path, *, active_nav: str, page_title: str) -> dict[str, Any]:
    services = published_items(database_path, "service")
    contact_items = published_items(database_path, "site_contact", limit=1)
    return {
        "active_nav": active_nav,
        "page_title": page_title,
        "footer_year": datetime.now(MOSCOW).year,
        "contact": contact_context(contact_items[0] if contact_items else None),
        "next_service": next_service(services),
    }
