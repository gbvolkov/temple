"""Authenticated, request-local view of working copies; never publishes data."""
from __future__ import annotations

from contextvars import ContextVar
from html import escape, unescape
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders, QueryParams
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse


DRAFT_PREVIEW: ContextVar[bool] = ContextVar('site_draft_preview', default=False)
EXCLUDED_PATHS = {'/cms.html', '/cms.js', '/cms.css', '/cms-schema.json', '/app.js', '/styles.css',
                  '/sitemap.xml', '/robots.txt', '/rss.xml', '/api/health'}


def preview_path(path: str) -> bool:
    if path in EXCLUDED_PATHS:
        return False
    if path.startswith('/api/'):
        return path.startswith('/api/public/')
    return not path.startswith(('/assets/', '/media/', '/media-derivatives/', '/social-preview/'))


def preview_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or value.startswith('#') or (parsed.path and not preview_path(parsed.path)):
        return value
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != 'preview']
    query.append(('preview', 'drafts'))
    # The CMS host's reverse proxy reserves its exact root for an editor redirect.
    # Keep the host-only CMS session and render the same homepage at a safe path.
    path = '/preview' if parsed.path == '/' else parsed.path
    return urlunsplit(('', '', path, urlencode(query), parsed.fragment))


def preview_html(value: str) -> str:
    def anchor(match):
        tag = match.group(0)
        if re.search(r'\bdata-preview-exit(?:\s|=|>)', tag, flags=re.I):
            return tag
        def href(attribute):
            return attribute.group(1) + attribute.group(2) + escape(preview_url(unescape(attribute.group(3))), quote=True) + attribute.group(2)
        return re.sub(r'(\bhref\s*=\s*)([\"\'])(.*?)\2', href, tag, flags=re.I)

    value = re.sub(r'<a\b[^>]*>', anchor, value, flags=re.I)

    def form(match):
        tag = match.group(0)
        method = re.search(r'\bmethod\s*=\s*[\"\']?([^\"\'\s>]+)', tag, flags=re.I)
        if method and method.group(1).lower() != 'get':
            return tag
        return tag + '<input type="hidden" name="preview" value="drafts">'

    return re.sub(r'<form\b[^>]*>', form, value, flags=re.I)


class DraftPreviewMiddleware:
    def __init__(self, app, authenticate):
        self.app = app
        self.authenticate = authenticate

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not preview_path(scope['path']):
            return await self.app(scope, receive, send)
        params = QueryParams(scope.get('query_string', b'').decode('latin-1'))
        if 'preview' not in params:
            return await self.app(scope, receive, send)
        private = {'Cache-Control': 'private, no-store', 'X-Robots-Tag': 'noindex, nofollow', 'Vary': 'Cookie'}
        if params.getlist('preview') != ['drafts']:
            return await JSONResponse({'detail': 'Неизвестный режим предпросмотра'}, status_code=400, headers=private)(scope, receive, send)
        try:
            await run_in_threadpool(self.authenticate, Request(scope))
        except HTTPException as error:
            response = HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Вход в CMS</title>'
                                    '<h1>Для предпросмотра войдите в CMS</h1><a href="/cms.html">Открыть CMS</a></html>',
                                    status_code=error.status_code, headers=private)
            return await response(scope, receive, send)
        if scope['method'] not in {'GET', 'HEAD'}:
            return await JSONResponse({'detail': 'В предпросмотре доступен только просмотр'}, status_code=405, headers=private)(scope, receive, send)
        token = DRAFT_PREVIEW.set(True)

        async def private_send(message):
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                headers['Cache-Control'] = private['Cache-Control']
                headers['X-Robots-Tag'] = private['X-Robots-Tag']
                vary = [v.strip() for v in headers.get('Vary', '').split(',') if v.strip()]
                if 'cookie' not in {v.lower() for v in vary}:
                    vary.append('Cookie')
                headers['Vary'] = ', '.join(vary)
                if 'location' in headers:
                    headers['location'] = preview_url(headers['location'])
            await send(message)

        try:
            await self.app(scope, receive, private_send)
        finally:
            DRAFT_PREVIEW.reset(token)
