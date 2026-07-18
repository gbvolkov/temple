from __future__ import annotations

import re
from urllib.parse import quote


DETAIL_PREFIXES = {
    "news": "/news",
    "gallery": "/gallery",
    "parish_section": "/parish",
    "clergy": "/about/clergy",
    "page": "/pages",
}

LEGACY_INDEX_PATHS = {
    "/", "/o-hrame/novosti-prihoda.html", "/o-hrame/anonsy.html",
    "/o-hrame/duhovenstvo.html", "/o-hrame/nebesnyy-pokrovitel.html",
    "/o-hrame/svyatyni-hrama.html", "/o-hrame/raspisanie-bogosluzheniy.html",
    "/o-hrame/fotogalereya.html", "/zhivoe-slovo/prihodskoy-listok.html",
    "/voskresnaya-shkola/obshhaya-informaciya.html",
    "/voskresnaya-shkola/zhizn.html", "/voskresnaya-shkola/raspisanie-zanyatiy.html",
    "/kontakty.html", "/требы-онлайн.html",
    "/прямая-трансляция-богослужений-из-храма.html",
}

STATIC_HASH_TARGETS = {
    "/#/": "/",
    "/#/schedule": "/schedule",
    "/#/about": "/about",
    "/#/about#contacts": "/about#contacts",
    "/#/parish": "/parish",
    "/#/school": "/school",
    "/#/news": "/news",
    "/#/gallery": "/gallery",
    "/#/leaflet": "/leaflet",
    "/#/media": "/media",
}


def content_path(content_type: str, slug: str) -> str:
    prefix = DETAIL_PREFIXES.get(content_type)
    if prefix is None:
        return {
            "home_feature": "/",
            "service": "/schedule",
            "leaflet_issue": "/leaflet",
            "video": "/media",
            "site_contact": "/about#contacts",
        }.get(content_type, "/")
    return f"{prefix}/{quote(slug, safe='-._~')}"


def legacy_index_target(old_path: str) -> str:
    if old_path in {"/o-hrame/novosti-prihoda.html", "/o-hrame/anonsy.html"}:
        return "/news"
    if old_path == "/kontakty.html":
        return "/about#contacts"
    if old_path == "/o-hrame/duhovenstvo.html":
        return "/about#clergy"
    if old_path in {"/o-hrame/nebesnyy-pokrovitel.html", "/o-hrame/svyatyni-hrama.html"}:
        return "/about"
    if "raspisanie-bogosluzheniy" in old_path:
        return "/schedule"
    if old_path.startswith("/voskresnaya-shkola/"):
        return "/school"
    if "prihodskoy-listok" in old_path:
        return "/leaflet"
    if "трансляция" in old_path:
        return "/media"
    if "fotogalereya" in old_path:
        match = re.search(r"fotogalereya/(20\d{2,3})(?:\.html)?/?$", old_path)
        if match:
            year = match.group(1)
            if year == "20241":
                year = "2024"
            if len(year) == 4:
                return f"/gallery?year={year}"
        return "/gallery"
    if old_path.startswith("/zhizn-prihoda/"):
        return "/parish"
    if old_path.startswith("/o-hrame/"):
        return "/about"
    return "/"


def is_legacy_index(old_path: str) -> bool:
    if old_path in LEGACY_INDEX_PATHS:
        return True
    return bool(re.search(r"/o-hrame/fotogalereya/20\d{2,3}(?:\.html)?/?$", old_path))


def legacy_redirect_target(old_path: str, content_type: str, slug: str) -> str:
    return legacy_index_target(old_path) if is_legacy_index(old_path) else content_path(content_type, slug)


def clean_hash_target(new_path: str, *, content_type: str | None = None, slug: str | None = None) -> str:
    if new_path.startswith("/#/content/"):
        if not content_type or not slug:
            raise ValueError(f"Невозможно определить detail-маршрут {new_path}")
        return content_path(content_type, slug)
    if new_path in STATIC_HASH_TARGETS:
        return STATIC_HASH_TARGETS[new_path]
    raise ValueError(f"Неизвестная hash-цель {new_path}")
