"""Pure proposals for the editorial policy approved on 24 November 2026."""
from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import unquote, urlsplit


POLICY_VERSION = "2026-11-v2"
PHOTO_COLLECTION_POLICY = "preserve_all"
MONTH_NAMES = (
    r"январ[ьяею]|yanvar(?:ya|a|y)?|january",
    r"феврал[ьяею]|fevral(?:ya|a|y)?|february",
    r"март[аеу]?|mart(?:a)?|march",
    r"апрел[ьяею]|aprel(?:ya|a|y)?|april",
    r"ма[йяею]|maya|may",
    r"июн[ьяею]|iyun(?:ya|a|y)?|june",
    r"июл[ьяею]|iyul(?:ya|a|y)?|july",
    r"август[аеу]?|avgust(?:a)?|august",
    r"сентябр[ьяею]|sentyabr(?:ya|a|y)?|sentabr(?:ya|a|y)?|september",
    r"октябр[ьяею]|oktyabr(?:ya|a|y)?|october",
    r"ноябр[ьяею]|noyabr(?:ya|a|y)?|november",
    r"декабр[ьяею]|dekabr(?:ya|a|y)?|december",
)


def reason(code: str, message: str, field: str = "", *, category: str = "editorial") -> dict[str, Any]:
    return {"code": code, "message": message, "field_path": field, "category": category}


def _calendar(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _evidence(text: str) -> dict[str, Any]:
    """Keep invalid/contradictory dates; never silently guess a calendar day."""
    text = unquote(text).casefold()
    dates: set[date] = set()
    day_month: set[tuple[int, int]] = set()
    invalid = False
    spans = []
    patterns = (
        (r"(?<!\d)((?:19|20)\d{2})([._/-])(\d{1,2})\2(\d{1,2})(?!\d)", "ymd"),
        (r"(?<!\d)(\d{1,2})([._/-])(\d{1,2})\2((?:19|20)\d{2})(?!\d)", "dmy"),
        (r"(?<!\d)(\d{1,2})([._-])(\d{1,2})\2(\d{2})(?!\d)", "short"),
    )
    for pattern, order in patterns:
        for match in re.finditer(pattern, text):
            if any(match.start() < end and match.end() > start for start, end in spans):
                continue
            a, b, c = map(int, (match.group(1), match.group(3), match.group(4)))
            year, month, day = (a, b, c) if order == "ymd" else (c + (2000 if order == "short" else 0), b, a)
            parsed = _calendar(year, month, day)
            if parsed:
                dates.add(parsed)
                day_month.add((month, day))
            else:
                invalid = True
            spans.append(match.span())
    for month, names in enumerate(MONTH_NAMES, 1):
        pattern = rf"(?<!\d)(\d{{1,2}})[\s._-]*(?:{names})(?![a-zа-я])(?:[\s,._-]+((?:19|20)\d{{2}})(?!\d))?"
        for match in re.finditer(pattern, text):
            day = int(match.group(1))
            parsed = _calendar(int(match.group(2)) if match.group(2) else 2000, month, day)
            if parsed:
                day_month.add((month, day))
                if match.group(2):
                    dates.add(parsed)
            else:
                invalid = True
            spans.append(match.span())
    for match in re.finditer(r"(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)", text):
        if any(match.start() < end and match.end() > start for start, end in spans):
            continue
        day, month = map(int, match.groups())
        if _calendar(2000, month, day):
            day_month.add((month, day))
    return {"dates": dates, "day_month": day_month, "invalid": invalid}


def gallery_date_proposal(content: dict[str, Any], *, clean_text: str = "") -> dict[str, Any]:
    data = content["data"]
    before = str(data.get("event_date") or "")
    path = unquote(urlsplit(str(content.get("legacy_url") or "")).path)
    years = {int(value) for value in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", path)}
    evidence = _evidence(path + " " + str(content.get("title") or ""))
    # Body dates are supporting evidence only when the URL/title has no day.
    if not evidence["day_month"] and clean_text:
        evidence = _evidence(clean_text[:4000])
    if not years:
        years = {value.year for value in _evidence(path)["dates"]}
    explicit_years = {value.year for value in evidence["dates"]}
    reasons = []
    after, precision = before, "unknown"
    selected_year = next(iter(years)) if len(years) == 1 else None
    confirmed = data.get("event_date_precision") == "day"
    try:
        current = date.fromisoformat(before)
    except ValueError:
        current = None
    # The historic 2000-01-01 import fallback is not a detected event date.
    usable_saved = bool(current and (before != '2000-01-01' or confirmed))
    if (len(years) > 1 or (not years and len(explicit_years) > 1)):
        reasons.append(reason("gallery_year_conflict", "URL не позволяет однозначно выбрать год фотоальбома", "event_date"))
    elif evidence["invalid"]:
        reasons.append(reason("gallery_date_invalid", "Указанная в источнике дата фотоальбома не существует", "event_date"))
    elif (len(evidence["day_month"]) > 1 or ((confirmed or (not years and usable_saved)) and current and evidence["day_month"]
                                           and (current.month, current.day) not in evidence["day_month"])):
        reasons.append(reason("gallery_day_month_conflict", "Для фотоальбома указаны разные дни или месяцы", "event_date"))
    else:
        if selected_year is None and len(explicit_years) == 1:
            selected_year = next(iter(explicit_years))
        if selected_year is None and usable_saved:
            selected_year = current.year
        if selected_year is None:
            reasons.append(reason("gallery_year_unresolved", "Год фотоальбома нельзя однозначно определить из URL или подтверждённой даты", "event_date"))
        else:
            month_day = next(iter(evidence["day_month"]), None)
            if (month_day is None and current and data.get("event_date_precision") != "year"
                    and (usable_saved or confirmed)):
                month_day = (current.month, current.day)
            if month_day is not None:
                parsed = _calendar(selected_year, *month_day)
                if parsed:
                    after, precision = parsed.isoformat(), "day"
                else:
                    reasons.append(reason("gallery_date_invalid", "День и месяц недопустимы для найденного года", "event_date"))
            else:
                after, precision = date(selected_year, 1, 1).isoformat(), "year"
    if reasons:
        from .migration_explanations import explain_reason
        for item in reasons:
            item['explanation'] = explain_reason(item, content, clean_text=clean_text if not _evidence(path + ' ' + str(content.get('title') or ''))['day_month'] else '')
    return {
        "before": before, "after": after, "changed": before != after, "precision": precision,
        "year": selected_year, "reasons": reasons, "accepted": not reasons,
        "evidence": {"url": content.get("legacy_url"), "url_years": sorted(years),
                     "explicit_dates": sorted(value.isoformat() for value in evidence["dates"])},
    }


def news_archive_proposal(content: dict[str, Any]) -> dict[str, Any]:
    archive = bool(content.get("content_type") == "news"
                   and content.get("migration_review_required", False)
                   and content.get("status", "draft") != "trash")
    return {"archive": archive, "scope": "all_queued_news", "publish": False,
            "policy_version": POLICY_VERSION, "reasons": []}
