from __future__ import annotations

import json
import re
import sqlite3
import textwrap
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, unquote, urlsplit
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import Settings
from .db import connect
from .search import TYPE_LABELS, public_content_url


SITE_NAME = "Храм святителя Иннокентия"
SITE_FULL_NAME = "Храм святителя Иннокентия в Бескудникове"
DEFAULT_DESCRIPTION = "Официальный сайт храма святителя Иннокентия в Бескудникове"
DETAIL_TYPES = {"news", "gallery", "parish_section", "page", "clergy"}
STATIC_SITEMAP_PATHS = (
    "/", "/schedule", "/about", "/parish", "/school", "/news", "/gallery",
    "/leaflet", "/media",
)
PATH_DESCRIPTIONS = {
    "/": DEFAULT_DESCRIPTION,
    "/schedule": "Расписание богослужений храма святителя Иннокентия в Бескудникове.",
    "/about": "История, святыни, духовенство и контакты храма святителя Иннокентия.",
    "/parish": "Направления приходской жизни храма святителя Иннокентия.",
    "/school": "Воскресная школа храма святителя Иннокентия в Бескудникове.",
    "/news": "Новости и анонсы прихода храма святителя Иннокентия.",
    "/gallery": "Фотогалерея прихода храма святителя Иннокентия.",
    "/leaflet": "Архив выпусков Иннокентиевского приходского листка.",
    "/media": "Видео, трансляции и медиаматериалы прихода.",
    "/search": "Поиск по опубликованным материалам сайта храма святителя Иннокентия.",
}


class SocialPreviewError(RuntimeError):
    pass


def absolute_url(settings: Settings, value: str) -> str:
    if not value:
        return settings.public_base_url
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return value
    return settings.public_base_url + (value if value.startswith("/") else "/" + value)


def _truncate(value: Any, maximum: int) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(normalized) <= maximum:
        return normalized
    shortened = normalized[: maximum + 1].rsplit(" ", 1)[0].rstrip(".,;:—- ")
    return (shortened or normalized[:maximum]).rstrip() + "…"


def _item_text(item: dict[str, Any]) -> str:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    if data.get("seo_description"):
        return str(data["seo_description"])
    if data.get("summary") or data.get("note"):
        return str(data.get("summary") or data.get("note"))
    for block in item.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_data = block.get("data") if isinstance(block.get("data"), dict) else {}
        runs = block_data.get("runs") if isinstance(block_data.get("runs"), list) else []
        text = "".join(str(run.get("text") or "") for run in runs if isinstance(run, dict)).strip()
        if text:
            return text
    body = data.get("body") or data.get("biography") or data.get("body_text") or ""
    return str(body) if isinstance(body, str) else ""


def canonical_url(settings: Settings, path: str, query_params: Any) -> str:
    canonical_path = path.rstrip("/") or "/"
    allowed: list[tuple[str, str]] = []
    if canonical_path in {"/gallery", "/leaflet"}:
        year = str(query_params.get("year") or "")
        page = str(query_params.get("page") or "")
        if re.fullmatch(r"(?:19|20)\d{2}", year):
            allowed.append(("year", year))
        if re.fullmatch(r"[2-9]\d*", page):
            allowed.append(("page", page))
    suffix = "?" + urlencode(allowed) if allowed else ""
    return settings.public_base_url + canonical_path + suffix


def _coordinates(value: Any) -> tuple[float, float] | None:
    match = re.fullmatch(
        r"\s*(-?\d{1,3}(?:\.\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:\.\d+)?)\s*",
        str(value or ""),
    )
    if not match:
        return None
    latitude, longitude = float(match.group(1)), float(match.group(2))
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def _church_schema(settings: Settings, contact: dict[str, Any]) -> dict[str, Any]:
    church: dict[str, Any] = {
        "@type": "Church",
        "@id": settings.public_base_url + "/#church",
        "name": SITE_FULL_NAME,
        "url": settings.public_base_url + "/",
        "image": settings.public_base_url + "/assets/home-hero.jpg",
    }
    if contact.get("address"):
        church["address"] = {
            "@type": "PostalAddress",
            "streetAddress": contact["address"],
            "addressLocality": "Москва",
            "addressCountry": "RU",
        }
    for source, target in (("phone", "telephone"), ("email", "email")):
        if contact.get(source):
            church[target] = contact[source]
    coordinates = _coordinates(contact.get("map_coordinates"))
    if coordinates:
        church["geo"] = {
            "@type": "GeoCoordinates",
            "latitude": coordinates[0],
            "longitude": coordinates[1],
        }
    same_as = [link.get("url") for link in contact.get("links", []) if link.get("url")]
    if same_as:
        church["sameAs"] = same_as
    return church


def _breadcrumbs(settings: Settings, item: dict[str, Any], canonical: str) -> dict[str, Any]:
    content_type = str(item.get("content_type") or "")
    parent_path, parent_name = {
        "news": ("/news", "Новости"),
        "gallery": ("/gallery", "Фотогалерея"),
        "parish_section": ("/parish", "Жизнь прихода"),
        "clergy": ("/about", "О храме"),
        "page": ("/about", "О храме"),
    }.get(content_type, ("/", "Главная"))
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Главная", "item": settings.public_base_url + "/"},
            {"@type": "ListItem", "position": 2, "name": parent_name, "item": absolute_url(settings, parent_path)},
            {"@type": "ListItem", "position": 3, "name": item.get("title"), "item": canonical},
        ],
    }


def _detail_schema(
    settings: Settings,
    item: dict[str, Any],
    canonical: str,
    description: str,
    image: str,
) -> dict[str, Any]:
    content_type = str(item.get("content_type") or "")
    schema_type = {
        "news": "NewsArticle",
        "clergy": "Person",
        "gallery": "ImageGallery",
    }.get(content_type, "WebPage")
    detail: dict[str, Any] = {
        "@type": schema_type,
        "@id": canonical + "#main",
        "name": item.get("title"),
        "url": canonical,
        "description": description,
        "image": image,
        "mainEntityOfPage": canonical,
    }
    if schema_type == "NewsArticle":
        detail["headline"] = item.get("title")
        detail["datePublished"] = item.get("published_at")
        detail["dateModified"] = item.get("updated_at") or item.get("published_at")
        detail["publisher"] = {"@id": settings.public_base_url + "/#church"}
    return detail


def build_seo_context(
    settings: Settings,
    *,
    path: str,
    query_params: Any,
    page_title: str,
    contact: dict[str, Any],
    item: dict[str, Any] | None = None,
    noindex: bool = False,
    preview: bool = False,
) -> dict[str, Any]:
    data = item.get("data") if item and isinstance(item.get("data"), dict) else {}
    title = _truncate(data.get("seo_title") or (item or {}).get("title") or page_title, 70)
    full_title = SITE_FULL_NAME if path == "/" else f"{title} | {SITE_NAME}"
    description = _truncate(
        _item_text(item) if item else PATH_DESCRIPTIONS.get(path, DEFAULT_DESCRIPTION), 200
    ) or DEFAULT_DESCRIPTION
    canonical = canonical_url(settings, path, query_params)
    is_detail = bool(item and item.get("content_type") in DETAIL_TYPES and item.get("published_version"))
    if is_detail:
        image = absolute_url(
            settings,
            f"/social-preview/content/{item['id']}/v{item['published_version']}.jpg",
        )
    else:
        image = settings.public_base_url + "/social-preview/site.jpg"
    image_alt_source = (data.get("cover_alt") or item.get("cover_alt")) if item else SITE_FULL_NAME
    image_alt = _truncate(image_alt_source or SITE_FULL_NAME, 200)
    graph: list[dict[str, Any]] = [
        _church_schema(settings, contact),
        {
            "@type": "WebSite",
            "@id": settings.public_base_url + "/#website",
            "url": settings.public_base_url + "/",
            "name": SITE_FULL_NAME,
            "inLanguage": "ru-RU",
            "potentialAction": {
                "@type": "SearchAction",
                "target": settings.public_base_url + "/search?q={search_term_string}",
                "query-input": "required name=search_term_string",
            },
        },
    ]
    if is_detail and item:
        graph.extend((
            _detail_schema(settings, item, canonical, description, image),
            _breadcrumbs(settings, item, canonical),
        ))
    return {
        "title": title,
        "full_title": full_title,
        "description": description,
        "canonical": canonical,
        "robots": "noindex,nofollow" if preview else "noindex,follow" if noindex else "index,follow",
        "og_type": "article" if item and item.get("content_type") in {"news", "page", "parish_section"} else "profile" if item and item.get("content_type") == "clergy" else "website",
        "image": image,
        "image_alt": image_alt,
        "published_time": item.get("published_at") if item else None,
        "modified_time": (item.get("updated_at") or item.get("published_at")) if item else None,
        "json_ld": {"@context": "https://schema.org", "@graph": graph},
        "rss_url": settings.public_base_url + "/rss.xml",
    }


def _font(size: int, *, serif: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "C:/Windows/Fonts/georgia.ttf",
        ]
        if serif
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default(size=max(10, size // 2))


def _source_image(settings: Settings, value: Any) -> Path | None:
    path = str(value or "")
    if path.startswith("assets/"):
        path = "/" + path
    if path.startswith("/assets/"):
        candidate = (settings.site_dir / "assets" / unquote(path.removeprefix("/assets/"))).resolve()
        root = (settings.site_dir / "assets").resolve()
    elif path.startswith("/media/"):
        candidate = (settings.media_dir / unquote(path.removeprefix("/media/"))).resolve()
        root = settings.media_dir.resolve()
    else:
        return None
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _wrap_title(draw: ImageDraw.ImageDraw, title: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    words = title.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width or not current:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == 2:
            break
    if current and len(lines) < 3:
        lines.append(current)
    consumed = " ".join(lines)
    if len(consumed) < len(title) and lines:
        lines[-1] = lines[-1].rstrip(".,;:—- ") + "…"
    return lines[:3]


def _render_social_card(
    settings: Settings,
    output: Path,
    *,
    title: str,
    label: str,
    image_value: Any,
) -> Path:
    if output.is_file():
        return output
    source = _source_image(settings, image_value)
    try:
        if source:
            with Image.open(source) as opened:
                canvas = ImageOps.fit(
                    ImageOps.exif_transpose(opened).convert("RGB"), (1200, 630),
                    method=Image.Resampling.LANCZOS,
                )
        else:
            canvas = Image.new("RGB", (1200, 630), "#173a3a")
    except Exception:
        canvas = Image.new("RGB", (1200, 630), "#173a3a")
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle((0, 0, 1200, 630), fill=(10, 30, 31, 118))
    overlay_draw.rectangle((0, 290, 1200, 630), fill=(10, 30, 31, 205))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(canvas)
    title_font = _font(60, serif=True)
    label_font = _font(24)
    brand_font = _font(28)
    draw.text((72, 320), label.upper(), font=label_font, fill="#e1bc79")
    y = 370
    for line in _wrap_title(draw, _truncate(title, 150), title_font, 1040):
        draw.text((72, y), line, font=title_font, fill="white")
        y += 68
    draw.text((72, 574), SITE_FULL_NAME, font=brand_font, fill="#dce8e6")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    canvas.convert("RGB").save(temporary, "JPEG", quality=88, optimize=True)
    temporary.replace(output)
    return output


def site_social_preview_path(settings: Settings) -> Path:
    return _render_social_card(
        settings,
        settings.derivatives_dir / "social" / "site-v1.jpg",
        title=SITE_FULL_NAME,
        label="Официальный сайт прихода",
        image_value="/assets/home-hero.jpg",
    )


def social_preview_path(settings: Settings, content_id: str, version: int) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", content_id) or version < 1:
        raise SocialPreviewError("Некорректный идентификатор social preview")
    with connect(settings.database_path) as connection:
        content = connection.execute("SELECT * FROM contents WHERE id=?", (content_id,)).fetchone()
        if content is None or content["status"] in {"archived", "trash"}:
            raise SocialPreviewError("Материал не опубликован")
        current = int(content["published_version"] or 0) == version
        was_published = connection.execute(
            """SELECT 1 FROM audit_events
                 WHERE content_id=? AND published_version=?
                   AND action IN ('publish','scheduled_publish') LIMIT 1""",
            (content_id, version),
        ).fetchone()
        if not current and not was_published:
            raise SocialPreviewError("Версия не публиковалась")
        revision = connection.execute(
            "SELECT snapshot_json FROM revisions WHERE content_id=? AND version=?",
            (content_id, version),
        ).fetchone()
    if revision is None:
        raise SocialPreviewError("Опубликованная версия не найдена")
    snapshot = json.loads(revision["snapshot_json"])
    snapshot.update({
        "id": content_id,
        "content_type": content["content_type"],
        "published_version": version,
    })
    data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
    return _render_social_card(
        settings,
        settings.derivatives_dir / "social" / content_id / f"v{version}.jpg",
        title=str(snapshot.get("title") or SITE_FULL_NAME),
        label=TYPE_LABELS.get(str(snapshot.get("content_type")), "Материал прихода"),
        image_value=data.get("social_image") or data.get("cover") or data.get("photo") or "/assets/home-hero.jpg",
    )


def sitemap_xml(settings: Settings) -> bytes:
    namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ElementTree.register_namespace("", namespace)
    root = ElementTree.Element(f"{{{namespace}}}urlset")
    entries: dict[str, str | None] = {path: None for path in STATIC_SITEMAP_PATHS}
    with connect(settings.database_path) as connection:
        rows = connection.execute(
            """SELECT c.*,r.snapshot_json,r.created_at AS revision_created_at
                 FROM contents c JOIN revisions r
                   ON r.content_id=c.id AND r.version=c.published_version
                WHERE c.published_version IS NOT NULL
                  AND c.status NOT IN ('archived','trash')
                  AND c.content_type IN ('news','gallery','parish_section','clergy','page')"""
        ).fetchall()
    for row in rows:
        snapshot = json.loads(row["snapshot_json"])
        snapshot.update({
            "id": row["id"], "content_type": row["content_type"],
            "slug": row["published_slug"] or snapshot.get("slug") or row["slug"],
        })
        path = public_content_url(snapshot).split("#", 1)[0].split("?", 1)[0]
        if path in STATIC_SITEMAP_PATHS and row["content_type"] == "page":
            continue
        entries[path] = snapshot.get("updated_at") or row["revision_created_at"]
    for path, lastmod in entries.items():
        node = ElementTree.SubElement(root, f"{{{namespace}}}url")
        ElementTree.SubElement(node, f"{{{namespace}}}loc").text = absolute_url(settings, path)
        if lastmod:
            ElementTree.SubElement(node, f"{{{namespace}}}lastmod").text = str(lastmod)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def robots_text(settings: Settings) -> str:
    return "\n".join((
        "User-agent: *",
        "Disallow: /api/",
        "Disallow: /cms.html",
        f"Sitemap: {settings.public_base_url}/sitemap.xml",
        "",
    ))


def rss_xml(settings: Settings) -> bytes:
    rss = ElementTree.Element("rss", {"version": "2.0"})
    channel = ElementTree.SubElement(rss, "channel")
    ElementTree.SubElement(channel, "title").text = f"Новости — {SITE_FULL_NAME}"
    ElementTree.SubElement(channel, "link").text = settings.public_base_url + "/news"
    ElementTree.SubElement(channel, "description").text = PATH_DESCRIPTIONS["/news"]
    ElementTree.SubElement(channel, "language").text = "ru-RU"
    with connect(settings.database_path) as connection:
        rows = connection.execute(
            """SELECT c.*,r.snapshot_json FROM contents c
                 JOIN revisions r ON r.content_id=c.id AND r.version=c.published_version
                WHERE c.published_version IS NOT NULL
                  AND c.status NOT IN ('archived','trash') AND c.content_type='news'
                ORDER BY COALESCE(c.published_at,c.updated_at) DESC LIMIT 50"""
        ).fetchall()
    newest: datetime | None = None
    for row in rows:
        snapshot = json.loads(row["snapshot_json"])
        data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
        slug = row["published_slug"] or snapshot.get("slug") or row["slug"]
        link = settings.public_base_url + "/news/" + str(slug)
        item = ElementTree.SubElement(channel, "item")
        ElementTree.SubElement(item, "title").text = str(snapshot.get("title") or row["title"])
        ElementTree.SubElement(item, "link").text = link
        ElementTree.SubElement(item, "guid", {"isPermaLink": "true"}).text = link
        ElementTree.SubElement(item, "description").text = _truncate(data.get("summary") or data.get("note"), 500)
        raw_date = row["published_at"] or snapshot.get("updated_at")
        try:
            published = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            published = published.astimezone(UTC)
            newest = max(newest, published) if newest else published
            ElementTree.SubElement(item, "pubDate").text = format_datetime(published)
        except (TypeError, ValueError):
            pass
    if newest:
        ElementTree.SubElement(channel, "lastBuildDate").text = format_datetime(newest)
    return ElementTree.tostring(rss, encoding="utf-8", xml_declaration=True)
