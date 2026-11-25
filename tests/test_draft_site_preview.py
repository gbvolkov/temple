from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import re
import sqlite3

from fastapi.testclient import TestClient
import pytest

from scripts.run_cms_regression import logical_database_digest
from server.app import create_app
from server.draft_preview import DRAFT_PREVIEW, preview_url, preview_html
from test_stage9_search_seo import settings_for, login, create_content, publish


@pytest.fixture
def preview_site(tmp_path):
    settings = settings_for(tmp_path)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        draft = create_content(client, headers, 'news', 'PrivatePreviewMarker', 'Приватный текст предпросмотра')
        blocked = create_content(client, headers, 'news', 'MigrationPreviewMarker', 'Непроверенный текст')
        old = publish(client, headers, create_content(client, headers, 'news', 'PublishedOriginal', 'Опубликованный исходный текст'))
        updated = client.put('/api/admin/contents/' + old['id'], headers=headers, json={
            'version': old['version'], 'title': 'WorkingRevisionMarker', 'data': {**old['data'], 'summary': 'Новая рабочая аннотация'}, 'slug': old['slug']}).json()
        hidden = []
        for status in ['archived', 'trash']:
            item = create_content(client, headers, 'news', status + 'SecretMarker', 'Скрытый материал')
            hidden.append(item)
            response = client.post('/api/admin/contents/' + item['id'] + '/' + ('archive' if status == 'archived' else 'trash'), headers=headers, json={'version': item['version']})
            assert response.status_code == 200, response.text
        with sqlite3.connect(settings.database_path) as c:
            c.execute('UPDATE contents SET migration_review_required=1 WHERE id=?', (blocked['id'],))
        yield client, settings, draft, blocked, old, updated, hidden


@pytest.mark.parametrize('role', ['viewer', 'editor', 'publisher', 'admin'])
def test_authenticated_roles_see_working_drafts_without_publishing(preview_site, role):
    client, settings, draft, blocked, old, updated, hidden = preview_site
    with sqlite3.connect(settings.database_path) as c:
        c.execute("UPDATE users SET role=? WHERE username='admin'", (role,))
    before = logical_database_digest(settings.database_path)
    page = client.get('/news?preview=drafts')
    assert page.status_code == 200
    assert all(name in page.text for name in ['PrivatePreviewMarker', 'MigrationPreviewMarker', 'WorkingRevisionMarker'])
    assert 'PublishedOriginal' not in page.text
    assert all(item['title'] not in page.text for item in hidden)
    assert 'href="/news/' + draft['slug'] + '?preview=drafts"' in page.text
    assert 'data-draft-preview="true"' in page.text
    assert 'noindex,nofollow' in page.text
    assert page.headers['cache-control'] == 'private, no-store'
    assert 'cookie' in page.headers['vary'].lower()
    assert page.headers['x-robots-tag'] == 'noindex, nofollow'
    assert len(page.content) == int(page.headers['content-length'])
    detail = client.get('/news/' + draft['slug'] + '?preview=drafts')
    assert 'Приватный текст предпросмотра' in detail.text
    assert client.get('/news/' + blocked['slug'] + '?preview=drafts').status_code == 200
    for item in hidden:
        assert client.get('/news/' + item['slug'] + '?preview=drafts').status_code == 404
    normal = client.get('/news')
    assert 'PublishedOriginal' in normal.text and 'WorkingRevisionMarker' not in normal.text
    assert 'PrivatePreviewMarker' not in normal.text
    assert client.get('/news/' + draft['slug']).status_code == 404
    assert logical_database_digest(settings.database_path) == before
    assert not DRAFT_PREVIEW.get()


def test_preview_search_api_feeds_and_forms_are_isolated(preview_site):
    client, settings, draft, *_ = preview_site
    before = logical_database_digest(settings.database_path)
    assert client.get('/api/public/search?q=PrivatePreviewMarker').json()['total'] == 0
    search = client.get('/api/public/search?q=PrivatePreviewMarker&preview=drafts').json()
    assert search['total'] == 1 and search['items'][0]['id'] == draft['id']
    assert search['items'][0]['preview'] is True
    page = client.get('/search?q=PrivatePreviewMarker&preview=drafts')
    assert draft['title'] in page.text and 'name="preview" value="drafts"' in page.text
    api = client.get('/api/public/content/' + draft['slug'] + '?preview=drafts')
    assert api.json()['status'] == 'draft' and api.json()['published_version'] is None
    assert any(x['id'] == draft['id'] for x in client.get('/api/public/content?preview=drafts').json())
    for path in ['/sitemap.xml', '/rss.xml']:
        assert draft['slug'] not in client.get(path + '?preview=drafts').text
    response = client.post('/api/public/prayer-notes?preview=drafts', json={})
    assert response.status_code == 405
    assert logical_database_digest(settings.database_path) == before


@pytest.mark.parametrize('mode', ['anonymous', 'expired', 'inactive'])
def test_preview_rejects_missing_expired_and_inactive_sessions(preview_site, mode):
    client, settings, draft, *_ = preview_site
    if mode == 'anonymous':
        client.cookies.clear()
    else:
        with sqlite3.connect(settings.database_path) as c:
            if mode == 'expired':
                c.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00+00:00'")
            else:
                c.execute("UPDATE users SET is_active=0 WHERE username='admin'")
    response = client.get('/news?preview=drafts')
    assert response.status_code == 401 and draft['title'] not in response.text
    assert response.headers['cache-control'] == 'private, no-store'
    assert client.get('/news').status_code == 200


def test_parallel_normal_and_preview_requests_do_not_share_visibility(preview_site):
    client, _, draft, *_ = preview_site
    def request(preview):
        response = client.get('/news' + ('?preview=drafts' if preview else ''))
        return preview, draft['title'] in response.text
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(request, [True, False] * 8))
    assert all(expected == visible for expected, visible in results)


def test_preview_rejects_unknown_modes_and_preserves_redirects(preview_site):
    client, _, *_ = preview_site
    assert client.get('/news?preview=unknown').status_code == 400
    assert client.get('/news?preview=drafts&preview=other').status_code == 400
    response = client.get('/news/?preview=drafts', follow_redirects=False)
    assert response.status_code == 308 and response.headers['location'] == '/news?preview=drafts'
    assert preview_url('/gallery?year=2020#photos') == '/gallery?year=2020&preview=drafts#photos'
    assert preview_url('?page=2') == '?page=2&preview=drafts'
    assert preview_url('https://external.example/page') == 'https://external.example/page'


def test_cms_66_preview_exit_link_leaves_mode_in_either_attribute_order():
    for link in ['<a href="/" data-preview-exit>Выйти</a>', '<a data-preview-exit href="/">Выйти</a>']:
        assert preview_html(link) == link
    assert preview_html('<a href="/news">Новости</a>') == '<a href="/news?preview=drafts">Новости</a>'


def test_cms_69_safe_preview_home_requires_session_and_never_redirects_to_cms(preview_site):
    client, settings, draft, *_ = preview_site
    before = logical_database_digest(settings.database_path)
    response = client.get('/preview?preview=drafts', follow_redirects=False)
    assert response.status_code == 200
    assert 'class="site-header"' in response.text and 'class="cms-topbar"' not in response.text
    assert 'href="/preview?preview=drafts"' in response.text
    assert 'href="/?preview=drafts"' not in response.text
    assert f'href="{settings.public_base_url.rstrip("/")}/" data-preview-exit' in response.text
    assert response.headers['cache-control'] == 'private, no-store'
    assert preview_url('/#content') == '/preview?preview=drafts#content'
    assert client.get('/preview?preview=bad', follow_redirects=False).status_code == 400
    assert client.get('/preview?preview=drafts&preview=bad', follow_redirects=False).status_code == 400
    normal = client.get('/preview', follow_redirects=False)
    assert normal.status_code == 308 and normal.headers['location'] == settings.public_base_url.rstrip('/') + '/'
    assert logical_database_digest(settings.database_path) == before
    client.cookies.clear()
    anonymous = client.get('/preview?preview=drafts', follow_redirects=False)
    assert anonymous.status_code == 401 and draft['title'] not in anonymous.text


def test_cms_67_preview_uses_versioned_assets_to_avoid_cached_public_script(preview_site):
    client, settings, *_ = preview_site
    # Simulate a browser that already loaded the ordinary site's script.
    assert client.get('/app.js').status_code == 200
    body = client.get('/?preview=drafts').text
    script = re.search(r'<script src="(/app\.js\?v=[a-f0-9]{16})"', body)
    css = re.search(r'href="(/styles\.css\?v=[a-f0-9]{16})"', body)
    assert script and css
    assert script.group(1).split('?')[1] == css.group(1).split('?')[1]
    assert client.get(script.group(1)).status_code == 200
    assert client.get(css.group(1)).status_code == 200
    assert 'src="/app.js"' in client.get('/').text


def test_preview_gallery_pagination_years_and_related_working_materials(preview_site):
    client, settings, draft, blocked, *_ = preview_site
    headers = login(client)
    for number in range(25):
        response = client.post('/api/admin/contents', headers=headers, json={
            'content_type': 'gallery', 'title': f'DraftAlbum{number}', 'data': {
                'event_date': '2020-01-01', 'cover': 'assets/school-maslenitsa.jpg',
                'photos': [{'image': 'assets/school-maslenitsa.jpg', 'caption': 'Все фото', 'alt': 'Фотография события', 'order': 1}]}})
        assert response.status_code == 201, response.text
    with sqlite3.connect(settings.database_path) as c:
        data = json.loads(c.execute('SELECT data_json FROM contents WHERE id=?', (draft['id'],)).fetchone()[0])
        data['related_content'] = [blocked['id']]
        c.execute('UPDATE contents SET data_json=? WHERE id=?', (json.dumps(data), draft['id']))
    before = logical_database_digest(settings.database_path)
    page = client.get('/gallery?year=2020&preview=drafts')
    assert page.status_code == 200 and 'preview=drafts' in page.text
    assert client.get('/gallery?year=2020&page=2&preview=drafts').status_code == 200
    assert client.get('/gallery?year=2020&page=3&preview=drafts').status_code == 404
    assert 'MigrationPreviewMarker' in client.get('/news/' + draft['slug'] + '?preview=drafts').text
    assert logical_database_digest(settings.database_path) == before
