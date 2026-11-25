"""Human-readable explanations of migration findings, without changing verdicts."""
from __future__ import annotations

from datetime import date
import re
from urllib.parse import unquote, urlsplit
from typing import Any
import sqlite3


LABELS = {
    'title': 'Название', 'event_date': 'Дата события', 'publication_date': 'Дата публикации',
    'body': 'Текст', 'summary': 'Краткое описание', 'email': 'Электронная почта',
    'legal_details': 'Реквизиты', 'map_coordinates': 'Координаты на карте',
    'opening_hours': 'Часы работы', 'cover': 'Обложка', 'photos': 'Фотографии',
}


def explain_reason(reason: dict[str, Any], content: dict[str, Any], *, url_is_current: bool = True,
                   clean_text: str = '') -> dict[str, Any]:
    code = reason.get('source_code') or reason.get('code') or ''
    field = str(reason.get('field_path') or '')
    key = field.removeprefix('content.').split('.')[0].split('[')[0]
    data = content.get('data') or {}
    explanation: dict[str, Any] = {'facts': [], 'action': ''}
    facts = explanation['facts']
    if code in {'gallery_year_conflict', 'gallery_year_unresolved', 'gallery_day_month_conflict',
                'gallery_date_invalid', 'date_requires_editor', 'fallback_date_2000'}:
        value = str(data.get('event_date') or data.get('publication_date') or '')
        try:
            value = date.fromisoformat(value).strftime('%d.%m.%Y')
        except ValueError:
            pass
        facts.append({'label': 'Дата в карточке', 'value': value or 'Не указана'})
        facts.append({'label': 'Название материала', 'value': str(content.get('title') or 'Не указано')})
        from .migration_policy import _evidence
        title_dates = _evidence(str(content.get('title') or ''))
        if title_dates['dates']:
            facts.append({'label': 'Даты, найденные в названии', 'value': ', '.join(d.strftime('%d.%m.%Y') for d in sorted(title_dates['dates']))})
        url = str(content.get('legacy_url') or '') if url_is_current else ''
        if url:
            path = unquote(urlsplit(url).path)
            facts.append({'label': 'Адрес на старом сайте', 'value': path})
            years = sorted(set(re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', path)))
            facts.append({'label': 'Годы, найденные в адресе', 'value': ', '.join(years) or 'Не найдены'})
            url_dates = _evidence(path)
            if url_dates['dates']:
                facts.append({'label': 'Даты, найденные в адресе', 'value': ', '.join(d.strftime('%d.%m.%Y') for d in sorted(url_dates['dates']))})
        else:
            facts.append({'label': 'Адрес на старом сайте', 'value': 'Не указан' if url_is_current else 'Адрес для этой версии не сохранился'})
        if clean_text:
            facts.append({'label': 'Проверенный текст старой страницы', 'value': clean_text[:4000]})
        explanation['action'] = ('Сравните сведения и установите дату события по надёжному источнику. '
                                 'Если известен только год, можно указать 1 января этого года. '
                                 'Если выяснить дату не удалось, оставьте материал на проверке.')
    elif code in {'duplicate_title', 'duplicate_content'}:
        facts.append({'label': 'Название этого материала', 'value': str(content.get('title') or 'Не указано')})
        explanation['action'] = 'Откройте совпадающие материалы и проверьте, относятся ли они к одному событию. Сохраните нужные материалы.'
    elif code in {'required_field_requires_editor', 'required_field_missing'}:
        facts.append({'label': 'Что не заполнено', 'value': LABELS.get(key, field or 'Поле не указано')})
        explanation['action'] = 'Заполните это поле достоверными сведениями и сохраните материал.'
    elif code in {'legacy_text', 'text_extraction_ambiguous', 'legacy_navigation_text'}:
        source_text = data.get('body_text')
        if isinstance(source_text, str) and source_text:
            facts.append({'label': 'Исходный текст материала', 'value': source_text})
        explanation['action'] = 'Откройте материал и старую страницу, выделите нужный текст и сохраните его в карточке.'
    elif code == 'missing_legacy_media':
        explanation['action'] = 'Проверьте перечисленные файлы. Если файл нужен, загрузите его заново или укажите замену.'
    elif code == 'treby_details_require_editor':
        explanation['action'] = 'Проверьте виды треб, суммы пожертвований, сроки и реквизиты, затем сохраните актуальные сведения.'
    else:
        if field:
            facts.append({'label': 'Где обнаружена проблема', 'value': LABELS.get(key, field)})
            value = data.get(key)
            if isinstance(value, (str, int, float)) and value != '':
                facts.append({'label': 'Значение в карточке', 'value': str(value)})
        explanation['action'] = 'Проверьте указанные сведения в карточке материала. Если информации недостаточно, обратитесь к администратору сайта.'
    details = reason.get('details') or reason.get('evidence')
    if isinstance(details, dict):
        for name, value in details.items():
            if name in {'content_ids', 'issue_ids'}:
                continue
            if value not in (None, '', [], {}):
                labels = {'url': 'Адрес', 'urls': 'Адреса', 'count': 'Количество', 'field': 'Поле'}
                facts.append({'label': labels.get(name, name), 'value': ', '.join(map(str, value)) if isinstance(value, list) else str(value)})
    return explanation


def add_related_facts(connection: sqlite3.Connection, explanation: dict[str, Any],
                      details: dict[str, Any], content_id: str) -> None:
    """Resolve audit identifiers to readable current names; missing rows stay explicit."""
    for detail_key, table, label, columns in (
        ('content_ids', 'contents', 'Совпадающий материал сейчас', 'id,title,slug'),
        ('issue_ids', 'missing_media_issues', 'Отсутствующий файл', 'id,source_url,error'),
    ):
        identifiers = details.get(detail_key, [])
        if not isinstance(identifiers, list) or not identifiers:
            continue
        rows = connection.execute(f'SELECT {columns} FROM {table} WHERE id IN (' + ','.join('?' for _ in identifiers) + ')', identifiers).fetchall()
        found = {row['id']: row for row in rows}
        for identifier in identifiers:
            if detail_key == 'content_ids' and identifier == content_id:
                continue
            row = found.get(identifier)
            if row is None:
                value = 'Сведения о записи больше недоступны'
            elif detail_key == 'content_ids':
                value = f"{row['title']} (адрес страницы: {row['slug']})"
            else:
                value = f"{row['source_url']} — {row['error']}"
            explanation['facts'].append({'label': label, 'value': value})
