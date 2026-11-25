from server.migration_policy import gallery_date_proposal


def test_cms_73_date_conflict_explains_sources_and_values():
    content = {'title': '08 июня 2025 г. День Святой Троицы', 'content_type': 'gallery',
               'legacy_url': '/fotogalereya/2025/23-июня-2024-г.html',
               'data': {'event_date': '2025-06-08'}}
    result = gallery_date_proposal(content)['reasons'][0]['explanation']
    assert '2025' in str(result) and '2024' in str(result)
    assert '08.06.2025' in str(result) and '23-июня-2024' in str(result)
    assert result['action'] and result['facts']


def test_explanation_does_not_invent_missing_or_historical_evidence():
    from server.migration_explanations import explain_reason
    result = explain_reason({'code': 'date_requires_editor', 'source_code': 'gallery_year_unresolved'},
                            {'title': 'Событие', 'content_type': 'gallery', 'data': {}}, url_is_current=False)
    assert any('не сохранился' in row['value'] for row in result['facts'])
    assert result['action']


def test_existing_preparation_explains_old_date_without_changing_material(tmp_path):
    import json
    import sqlite3
    from server.db import init_database
    from server.migration_acceptance import execute_audit_run, queue_audit
    from server.migration_preparation import execute_preparation_run, list_preparation_items, queue_preparation
    from test_migration_analysis import insert, settings_for

    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, 'conflict', 'gallery', '/2025/23-июня-2024.html', '2025-06-08', title='08 июня 2025')
    audit = queue_audit(settings.database_path, actor_id=None, scope={'check_external': False})
    execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir, settings.site_dir, audit['id'], check_external=False)
    run = queue_preparation(settings.database_path, actor_id=None)
    execute_preparation_run(settings, run['id'])
    with sqlite3.connect(settings.database_path) as c:
        # Simulate an old record created before explanations were recorded.
        old = json.loads(c.execute('SELECT reasons_json FROM migration_preparation_items WHERE run_id=?', (run['id'],)).fetchone()[0])
        for reason in old:
            reason.pop('explanation', None)
        c.execute('UPDATE migration_preparation_items SET reasons_json=? WHERE run_id=?', (json.dumps(old, ensure_ascii=False), run['id']))
        before = c.execute('SELECT data_json,version FROM contents WHERE id=?', ('conflict',)).fetchone()
    item = list_preparation_items(settings.database_path, run['id'])['items'][0]
    text = str(item['reasons'])
    assert '08.06.2025' in text and '23-июня-2024' in text and '2024, 2025' in text
    with sqlite3.connect(settings.database_path) as c:
        assert c.execute('SELECT data_json,version FROM contents WHERE id=?', ('conflict',)).fetchone() == before
        c.execute("UPDATE contents SET version=version+1,legacy_url='/new/2030/' WHERE id='conflict'")
    item = list_preparation_items(settings.database_path, run['id'])['items'][0]
    assert 'Адрес для этой версии не сохранился' in str(item['reasons'])
    assert '/new/2030/' not in str(item['reasons'])


def test_migration_audit_does_not_require_alt_on_image_blocks(tmp_path):
    import json
    import sqlite3
    from server.db import init_database
    from server.migration_acceptance import execute_audit_run, queue_audit
    from test_migration_analysis import insert, settings_for

    settings = settings_for(tmp_path)
    init_database(settings.database_path)
    insert(settings, 'no-alt', 'news', '/news/2024/', '2024-05-12')
    with sqlite3.connect(settings.database_path) as c:
        data = json.loads(c.execute('SELECT data_json FROM contents').fetchone()[0])
        data['body'] = [{'type': 'image', 'data': {'image': 'assets/school-maslenitsa.jpg', 'alt': ''}},
                        {'type': 'gallery', 'data': {'items': [{'image': 'assets/school-maslenitsa.jpg', 'alt': ''}]}}]
        data.pop('cover_alt', None)
        c.execute('UPDATE contents SET data_json=?', (json.dumps(data, ensure_ascii=False),))
    audit = queue_audit(settings.database_path, actor_id=None, scope={'check_external': False})
    execute_audit_run(settings.database_path, settings.schema_path, settings.media_dir, settings.site_dir, audit['id'], check_external=False)
    with sqlite3.connect(settings.database_path) as c:
        codes = {r[0] for r in c.execute("SELECT code FROM migration_review_issues WHERE content_id='no-alt' AND status='open'")}
    assert 'image_alt_missing' not in codes
    assert 'required_field_missing' not in codes
