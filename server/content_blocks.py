from __future__ import annotations

import copy
import json
import re
import sqlite3
import uuid
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlsplit


MAX_BLOCKS = 200
MAX_GALLERY_IMAGES = 100
MAX_RELATIONS = 20
MAX_DATA_BYTES = 1024 * 1024
MAX_TEXT_LENGTH = 20_000
EDITABLE_RELATION_TYPES = {"news", "page", "parish_section", "gallery"}
CANONICAL_BLOCK_TYPES = {
    "paragraph", "heading", "list", "image", "gallery", "quote", "video", "file", "callout",
}
COMPATIBILITY_BLOCK_TYPES = CANONICAL_BLOCK_TYPES | {"legacy_text"}
INLINE_MARKS = {"bold", "italic"}
HTML_TAG = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")


class ContentDataError(ValueError):
    pass


def _error(message: str) -> None:
    raise ContentDataError(message)


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        _error(f"{label}: неизвестные поля: {', '.join(sorted(unknown))}")


def _reject_html(value: Any, label: str) -> None:
    if isinstance(value, str) and HTML_TAG.search(value):
        _error(f"{label}: HTML не разрешён")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_html(item, f"{label}, элемент {index + 1}")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_html(item, f"{label}, {key}")


def _text(value: Any, label: str, *, required: bool = False, maximum: int = MAX_TEXT_LENGTH) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        _error(f"{label}: ожидается текст")
    if len(value) > maximum:
        _error(f"{label}: превышена допустимая длина")
    if HTML_TAG.search(value):
        _error(f"{label}: HTML не разрешён")
    if required and not value.strip():
        _error(f"{label}: заполните текст")
    return value


def safe_link(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    parsed = urlsplit(value)
    if parsed.scheme == "https" and parsed.netloc:
        return value
    if parsed.scheme in {"mailto", "tel"} and parsed.path:
        return value
    return ""


def safe_media(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in parsed.path:
        return ""
    path = parsed.path
    if not (path.startswith("assets/") or path.startswith(("/assets/", "/media/"))):
        return ""
    decoded = unquote(path)
    segments = decoded.removeprefix("/").split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        return ""
    if decoded != path:
        return ""
    if path:
        return path
    return ""


def safe_video(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    parsed = urlsplit(value)
    return value if parsed.scheme == "https" and parsed.netloc else ""


def normalize_runs(value: Any, label: str = "Текст") -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"text": _text(value, label), "marks": []}] if value else []
    if not isinstance(value, list):
        _error(f"{label}: неверный формат")
    result: list[dict[str, Any]] = []
    for index, run in enumerate(value):
        if not isinstance(run, dict):
            _error(f"{label}, фрагмент {index + 1}: неверный формат")
        _reject_unknown(run, {"text", "marks", "href"}, f"{label}, фрагмент {index + 1}")
        text = _text(run.get("text"), f"{label}, фрагмент {index + 1}")
        marks = run.get("marks") or []
        if not isinstance(marks, list) or any(mark not in INLINE_MARKS for mark in marks):
            _error(f"{label}, фрагмент {index + 1}: неизвестное форматирование")
        marks = list(dict.fromkeys(marks))
        href = run.get("href")
        normalized: dict[str, Any] = {"text": text, "marks": marks}
        if href not in (None, ""):
            normalized_href = safe_link(href)
            if not normalized_href:
                _error(f"{label}, фрагмент {index + 1}: небезопасная ссылка")
            normalized["href"] = normalized_href
        result.append(normalized)
    if sum(len(item["text"]) for item in result) > MAX_TEXT_LENGTH:
        _error(f"{label}: превышена допустимая длина")
    return result


def runs_text(runs: Iterable[dict[str, Any]]) -> str:
    return "".join(str(run.get("text") or "") for run in runs)


def _legacy_block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    data = block.get("data") if isinstance(block.get("data"), dict) else {}
    return str(block.get("text") or block.get("value") or data.get("text") or data.get("value") or "")


def legacy_to_blocks(value: Any) -> list[dict[str, Any]]:
    text = _legacy_block_text(value) if not isinstance(value, str) else value
    parts = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return [
        {
            "id": str(uuid.uuid4()),
            "type": "paragraph",
            "data": {"runs": [{"text": part, "marks": []}]},
        }
        for part in parts
    ]


def _block_id(value: Any, label: str) -> str:
    if value in (None, ""):
        return str(uuid.uuid4())
    if not isinstance(value, str) or len(value) > 120:
        _error(f"{label}: неверный ID")
    return value


def _normalize_gallery_items(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        _error(f"{label}: фотографии должны быть списком")
    if len(value) > MAX_GALLERY_IMAGES:
        _error(f"{label}: допускается не более {MAX_GALLERY_IMAGES} фотографий")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            _error(f"{label}, фотография {index + 1}: неверный формат")
        _reject_unknown(
            raw, {"id", "image", "url", "alt", "caption", "order"},
            f"{label}, фотография {index + 1}",
        )
        image = safe_media(raw.get("image") or raw.get("url"))
        if not image:
            _error(f"{label}, фотография {index + 1}: неверный адрес изображения")
        item = {
            "id": _block_id(raw.get("id"), f"{label}, фотография {index + 1}"),
            "image": image,
            "alt": _text(raw.get("alt"), f"{label}, фотография {index + 1}, alt", required=True, maximum=500),
            "caption": _text(raw.get("caption"), f"{label}, фотография {index + 1}, подпись", maximum=1000),
            "order": index + 1,
        }
        result.append(item)
    ids = [item["id"] for item in result]
    if len(ids) != len(set(ids)):
        _error(f"{label}: ID фотографий не должны повторяться")
    return result


def normalize_block(block: Any, index: int, *, allow_legacy: bool) -> dict[str, Any]:
    label = f"Блок {index + 1}"
    if isinstance(block, str):
        if allow_legacy:
            return {"id": str(uuid.uuid4()), "type": "legacy_text", "data": {"text": block}}
        return legacy_to_blocks(block)[0] if block.strip() else {
            "id": str(uuid.uuid4()), "type": "paragraph", "data": {"runs": []},
        }
    if not isinstance(block, dict):
        _error(f"{label}: неверный формат")
    _reject_unknown(block, {"id", "type", "data", "text", "value"}, label)
    block_type = block.get("type") or "paragraph"
    if block_type not in COMPATIBILITY_BLOCK_TYPES:
        _error(f"{label}: неизвестный тип {block_type}")
    if block_type == "legacy_text":
        if not allow_legacy:
            _error(f"{label}: legacy_text нельзя создавать или изменять")
        return {
            "id": _block_id(block.get("id"), label),
            "type": "legacy_text",
            "data": {"text": _text(_legacy_block_text(block), label)},
        }

    if "data" in block and not isinstance(block["data"], dict):
        _error(f"{label}: data должен быть объектом")
    data = block.get("data") if isinstance(block.get("data"), dict) else {}
    result: dict[str, Any] = {"id": _block_id(block.get("id"), label), "type": block_type, "data": {}}
    if block_type == "paragraph":
        _reject_unknown(data, {"runs", "text", "value"}, label)
        result["data"] = {"runs": normalize_runs(data.get("runs", data.get("text", block.get("text", ""))), label)}
    elif block_type == "heading":
        _reject_unknown(data, {"level", "runs", "text", "value"}, label)
        level = data.get("level", 2)
        if level not in (2, 3, "2", "3"):
            _error(f"{label}: разрешены только H2 и H3")
        result["data"] = {
            "level": int(level),
            "runs": normalize_runs(data.get("runs", data.get("text", block.get("text", ""))), label),
        }
    elif block_type == "list":
        _reject_unknown(data, {"style", "items"}, label)
        style = data.get("style", "bulleted")
        if style not in {"bulleted", "numbered"}:
            _error(f"{label}: неизвестный вид списка")
        items = data.get("items") or []
        if not isinstance(items, list) or len(items) > 100:
            _error(f"{label}: неверный список")
        normalized_items = []
        for item_index, item in enumerate(items):
            raw_runs = item.get("runs") if isinstance(item, dict) else item
            normalized_items.append({"runs": normalize_runs(raw_runs, f"{label}, пункт {item_index + 1}")})
        result["data"] = {"style": style, "items": normalized_items}
    elif block_type == "image":
        _reject_unknown(data, {"image", "url", "alt", "caption"}, label)
        image = safe_media(data.get("image") or data.get("url"))
        if not image:
            _error(f"{label}: неверный адрес изображения")
        result["data"] = {
            "image": image,
            "alt": _text(data.get("alt"), f"{label}, alt", required=True, maximum=500),
            "caption": _text(data.get("caption"), f"{label}, подпись", maximum=1000),
        }
    elif block_type == "gallery":
        _reject_unknown(data, {"items"}, label)
        result["data"] = {"items": _normalize_gallery_items(data.get("items") or [], label)}
    elif block_type == "quote":
        _reject_unknown(data, {"runs", "text", "author", "source"}, label)
        result["data"] = {
            "runs": normalize_runs(data.get("runs", data.get("text", "")), label),
            "author": _text(data.get("author"), f"{label}, автор", maximum=500),
            "source": _text(data.get("source"), f"{label}, источник", maximum=1000),
        }
    elif block_type == "video":
        _reject_unknown(data, {"url", "caption"}, label)
        url = safe_video(data.get("url"))
        if not url:
            _error(f"{label}: нужна корректная HTTPS-ссылка на видео")
        result["data"] = {"url": url, "caption": _text(data.get("caption"), f"{label}, подпись", maximum=1000)}
    elif block_type == "file":
        _reject_unknown(data, {"url", "label", "description"}, label)
        url = safe_media(data.get("url"))
        if not url:
            _error(f"{label}: файл должен находиться в /media или /assets")
        result["data"] = {
            "url": url,
            "label": _text(data.get("label"), f"{label}, название файла", required=True, maximum=500),
            "description": _text(data.get("description"), f"{label}, описание", maximum=2000),
        }
    elif block_type == "callout":
        _reject_unknown(data, {"tone", "title", "runs", "text"}, label)
        tone = data.get("tone", "info")
        if tone not in {"info", "important"}:
            _error(f"{label}: неизвестный вид плашки")
        result["data"] = {
            "tone": tone,
            "title": _text(data.get("title"), f"{label}, заголовок", maximum=500),
            "runs": normalize_runs(data.get("runs", data.get("text", "")), label),
        }
    return result


def _legacy_body_unchanged(incoming: Any, existing: Any) -> bool:
    def is_legacy_block(block: Any) -> bool:
        if isinstance(block, str):
            return True
        if not isinstance(block, dict):
            return True
        if block.get("type") == "legacy_text":
            return True
        data = block.get("data")
        if not block.get("id") or not isinstance(data, dict):
            return True
        if block.get("type") in {"paragraph", "heading", "quote", "callout"} and "runs" not in data:
            return True
        return False

    return incoming == existing and (
        isinstance(incoming, str)
        or isinstance(incoming, list) and any(is_legacy_block(block) for block in incoming)
    )


def normalize_body(value: Any, *, existing: Any = None) -> Any:
    if value is None:
        return []
    if _legacy_body_unchanged(value, existing):
        return copy.deepcopy(value)
    if isinstance(value, str):
        return legacy_to_blocks(_text(value, "Полный текст"))
    if not isinstance(value, list):
        _error("Полный текст должен быть массивом блоков")
    if len(value) > MAX_BLOCKS:
        _error(f"Допускается не более {MAX_BLOCKS} блоков")
    result = [normalize_block(block, index, allow_legacy=False) for index, block in enumerate(value)]
    ids = [block["id"] for block in result]
    if len(ids) != len(set(ids)):
        _error("ID блоков не должны повторяться")
    return result


def prepare_content_data(
    connection: sqlite3.Connection,
    content_type: str,
    data: dict[str, Any],
    *,
    content_id: str | None = None,
    existing_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        _error("Данные материала должны быть объектом")
    result = copy.deepcopy(data)
    existing_data = existing_data or {}
    for field in ("body", "biography"):
        if field in result:
            result[field] = normalize_body(result[field], existing=existing_data.get(field))

    if content_type == "gallery" and "photos" in result:
        if "photos" in existing_data and result["photos"] == existing_data["photos"]:
            # Не переписываем старый snapshot при сохранении другого поля.
            result["photos"] = copy.deepcopy(existing_data["photos"])
        else:
            result["photos"] = _normalize_gallery_items(
                result.get("photos") or [], "Фотографии альбома"
            )

    related = result.get("related_content", [])
    if related in (None, ""):
        related = []
    if not isinstance(related, list) or any(not isinstance(item, str) for item in related):
        _error("Связанные материалы должны быть списком ID")
    related = [item.strip() for item in related if item.strip()]
    if len(related) != len(set(related)):
        _error("Связанные материалы не должны повторяться")
    if len(related) > MAX_RELATIONS:
        _error(f"Допускается не более {MAX_RELATIONS} связанных материалов")
    if content_id and content_id in related:
        _error("Материал нельзя связать с самим собой")
    if related:
        placeholders = ",".join("?" for _ in related)
        rows = connection.execute(
            f"SELECT id,content_type FROM contents WHERE id IN ({placeholders})", related,
        ).fetchall()
        found = {row["id"]: row["content_type"] for row in rows}
        if set(related) != set(found):
            _error("Один из связанных материалов не найден")
        if any(found[item] not in EDITABLE_RELATION_TYPES for item in related):
            _error("Связывать можно только новости, страницы, направления и галереи")
    if content_type in EDITABLE_RELATION_TYPES:
        # Старые ревизии не получают новое пустое поле при обычном no-op сохранении.
        if "related_content" in data or "related_content" in existing_data:
            result["related_content"] = related
        else:
            result.pop("related_content", None)
    elif related:
        _error("Этот тип материала не поддерживает связи")
    else:
        result.pop("related_content", None)

    for key, value in result.items():
        if key in existing_data and value == existing_data[key]:
            continue
        _reject_html(value, key)

    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(serialized) > MAX_DATA_BYTES:
        _error("Данные материала превышают 1 МиБ")
    return result


def _legacy_render_blocks(value: Any) -> list[dict[str, Any]]:
    text = ""
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        text = "\n\n".join(filter(None, (_legacy_block_text(item) for item in value)))
    elif isinstance(value, dict):
        text = _legacy_block_text(value)
    return [
        {"id": f"legacy-{index}", "type": "paragraph", "data": {"runs": [{"text": part.strip(), "marks": []}]}}
        for index, part in enumerate(re.split(r"\n\s*\n", text)) if part.strip()
    ]


def blocks_for_render(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return _legacy_render_blocks(value)
    result: list[dict[str, Any]] = []
    for index, block in enumerate(value):
        if isinstance(block, dict) and block.get("type") in CANONICAL_BLOCK_TYPES and isinstance(block.get("data"), dict):
            try:
                result.append(normalize_block(block, index, allow_legacy=False))
            except ContentDataError:
                result.extend(_legacy_render_blocks(block))
        else:
            result.extend(_legacy_render_blocks(block))
    return result


def video_embed_url(value: Any) -> str:
    url = safe_video(value)
    if not url:
        return ""
    parsed = urlsplit(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.strip("/")
    if host == "youtu.be" and path:
        return f"https://www.youtube-nocookie.com/embed/{path.split('/')[0]}"
    if host in {"youtube.com", "m.youtube.com"}:
        if path.startswith("watch"):
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            return f"https://www.youtube-nocookie.com/embed/{video_id}" if video_id else ""
        if path.startswith(("embed/", "shorts/")):
            return f"https://www.youtube-nocookie.com/embed/{path.split('/', 1)[1].split('/')[0]}"
    if host in {"rutube.ru", "www.rutube.ru"}:
        match = re.search(r"(?:video|play/embed)/([a-zA-Z0-9]+)", path)
        return f"https://rutube.ru/play/embed/{match.group(1)}" if match else ""
    if host in {"vk.com", "m.vk.com", "vkvideo.ru"}:
        if path.startswith("video_ext.php"):
            return url
        match = re.search(r"video(-?\d+)_([0-9]+)", path)
        if match:
            return f"https://vk.com/video_ext.php?oid={match.group(1)}&id={match.group(2)}&hd=2"
    return ""


def enrich_blocks_for_render(value: Any) -> list[dict[str, Any]]:
    blocks = blocks_for_render(value)
    for block in blocks:
        data = block["data"]
        if block["type"] == "image" and str(data.get("image", "")).startswith("assets/"):
            data["image"] = "/" + data["image"]
        if block["type"] == "gallery":
            for item in data.get("items", []):
                if str(item.get("image", "")).startswith("assets/"):
                    item["image"] = "/" + item["image"]
        if block["type"] == "file" and str(data.get("url", "")).startswith("assets/"):
            data["url"] = "/" + data["url"]
        if block["type"] == "video":
            data["embed_url"] = video_embed_url(data.get("url"))
    return blocks
