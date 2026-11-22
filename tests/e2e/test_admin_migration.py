from __future__ import annotations

import json
import sqlite3

import pytest
from playwright.sync_api import expect

from server.migration_acceptance import execute_audit_run, queue_audit
from server.migration_preparation import execute_preparation_run, queue_preparation

from .conftest import login, open_content


@pytest.mark.e2e
@pytest.mark.full
def test_cms_39_card_review_ui(page, live_cms, coverage_steps):
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        content_id, raw = connection.execute("SELECT id,data_json FROM contents WHERE content_type='clergy' AND title='E2E clergy'").fetchone()
        data = json.loads(raw)
        data.update(rank="Иерей", position="Служение\u00a0в храме")
        connection.execute("UPDATE contents SET data_json=?,migration_review_required=1 WHERE id=?", (json.dumps(data), content_id))
    run = queue_audit(live_cms.settings.database_path, actor_id=None, scope={"content_id": content_id, "check_external": False})
    execute_audit_run(live_cms.settings.database_path, live_cms.settings.schema_path, live_cms.settings.media_dir, live_cms.settings.site_dir, run["id"], check_external=False)
    login(page)
    open_content(page, "clergy", "E2E clergy")
    review = page.locator("[data-card-review]")
    review.get_by_text("Служение", exact=False).first.wait_for()
    coverage_steps.check("stage7.card.current-warning", "Служение" in review.inner_text())
    review.get_by_role("button", name="Исправить и перепроверить").click()
    review.get_by_text("0 предупреждений", exact=False).wait_for()
    coverage_steps.check("stage7.card.corrected", "0 предупреждений" in review.inner_text())
    page.reload()
    open_content(page, "clergy", "E2E clergy")
    page.locator("[data-card-review]").get_by_text("0 предупреждений", exact=False).wait_for()
    coverage_steps.check("stage7.card.reload", "0 предупреждений" in page.locator("[data-card-review]").inner_text())


@pytest.mark.e2e
@pytest.mark.full
def test_cms_42_followup_batch_ui(page, live_cms, coverage_steps):
    login(page)
    _create_pilot(page, live_cms)
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("[data-batch-cancel]").click()
    page.locator(".workflow-panel__head .state-pill").filter(has_text="Отменена").wait_for()
    page.locator("[data-migration-back]").click()
    page.get_by_role("button", name="Создать следующую партию").click()
    page.get_by_label("Поиск материалов").fill("E2E")
    page.get_by_role("button", name="Найти материалы").click()
    checkbox = page.locator("[data-candidate-select]:not(:disabled)").first
    checkbox.wait_for()
    coverage_steps.check("stage7.batch.lookup", checkbox.is_visible())
    checkbox.check()
    page.get_by_label("Название партии").fill("Следующая партия")
    page.get_by_role("button", name="Создать партию").click()
    page.get_by_role("heading", name="Следующая партия", exact=True).wait_for()
    coverage_steps.check("stage7.batch.create", page.locator("[data-batch-content]").count() == 1)


@pytest.mark.e2e
@pytest.mark.full
def test_cms_43_refresh_batch_ui(page, live_cms, coverage_steps):
    login(page)
    batch = _create_pilot(page, live_cms)
    changed = next(item for item in batch["items"] if item["content_type"] == "clergy")
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        connection.execute("UPDATE contents SET version=version+1 WHERE id=?", (changed["content_id"],))
    run = queue_audit(live_cms.settings.database_path, actor_id=None, scope={"content_id": changed["content_id"], "check_external": False})
    execute_audit_run(live_cms.settings.database_path, live_cms.settings.schema_path, live_cms.settings.media_dir, live_cms.settings.site_dir, run["id"], check_external=False)
    page.locator("[data-migration-back]").click()
    page.locator(f'[data-migration-batch="{batch["id"]}"]').click()
    page.get_by_text("Версии материалов изменились").wait_for()
    coverage_steps.check("stage7.batch-refresh.stale", page.locator("[data-batch-stale]").is_visible())
    page.get_by_role("button", name="Обновить версии партии").click()
    page.get_by_text("Версии материалов изменились").wait_for(state="hidden")
    coverage_steps.check("stage7.batch-refresh.updated", page.locator("[data-batch-stale]").count() == 0)


@pytest.mark.e2e
@pytest.mark.full
def test_cms_44_automatic_acceptance_ui(page, live_cms, coverage_steps):
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        connection.execute("UPDATE contents SET migration_review_required=0")
        data = {"publication_date": "2014-01-01", "category": "Новости прихода", "summary": "Краткое описание",
                "cover": "assets/school-maslenitsa.jpg", "cover_alt": "Событие", "related_content": [],
                "body": [{"id": "p1", "type": "paragraph", "data": {"runs": [{"text": "Текст события.", "marks": []}]}}]}
        connection.execute("UPDATE contents SET migration_review_required=1,data_json=? WHERE content_type='news' AND title='E2E pagination 001'", (json.dumps(data, ensure_ascii=False),))
    _prepare_editorial_gate(live_cms)
    login(page)
    _open_panel(page, "migration", "Перенесённые материалы")
    page.get_by_role("button", name="Принять автоматически готовые").click()
    page.get_by_text("Автоматически принято: 1", exact=False).wait_for()
    coverage_steps.mark("stage7.auto.accepted")
    page.locator('.cms-type[data-content-type="news"]').click()
    picker = page.locator("[data-content-select]")
    option = picker.locator("option").filter(has_text="E2E pagination 001")
    option.wait_for(state="attached")
    picker.select_option(option.get_attribute("value"))
    page.locator("[data-editor-title]").filter(has_text="E2E pagination 001").wait_for()
    coverage_steps.check("stage7.auto.draft", page.locator("[data-migration-warning]").count() == 0 and "Черновик" in page.locator("[data-workflow-state]").inner_text())
    _open_panel(page, "migration", "Перенесённые материалы")
    page.get_by_text("Журнал автоматической приёмки").click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="Откатить пакет").click()
    page.locator("[data-auto-operations]").get_by_text("откачено", exact=False).wait_for()
    coverage_steps.mark("stage7.auto.rollback")


@pytest.mark.e2e
@pytest.mark.full
@pytest.mark.expected_http_error(409, "/api/admin/contents/")
def test_stage7_visible_workflow_to_publication(page, browser, live_cms, coverage_steps):
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        connection.execute("UPDATE contents SET migration_review_required=0")
        original = connection.execute("SELECT id FROM contents WHERE content_type='clergy' AND title='E2E clergy'").fetchone()[0]
        data = {"rank": "", "position": "Настоятель", "biography": [{"id": "bio", "type": "paragraph", "data": {"runs": [{"text": "Биография священника.", "marks": []}]}}]}
        connection.execute("UPDATE contents SET title=?,data_json=?,migration_review_required=1 WHERE id=?", ("Священник E2E", json.dumps(data, ensure_ascii=False), original))
        data.update(rank="Иерей", position="Другой приход")
        connection.execute("""INSERT INTO contents(id,content_type,slug,title,status,data_json,migration_review_required,version,created_at,updated_at)
            VALUES('e2e-clergy-duplicate','clergy','e2e-clergy-duplicate','Священник E2E','draft',?,0,1,'2026-01-01','2026-01-01')""", (json.dumps(data, ensure_ascii=False),))
    _prepare_editorial_gate(live_cms)
    viewer = browser.new_page()
    viewer.goto(f"{live_cms.base_url}/cms.html")
    login(viewer, "e2e_viewer", SEEDED_PASSWORD)
    _open_panel(viewer, "migration", "Перенесённые материалы")
    coverage_steps.check("stage7.e2e.role", viewer.get_by_role("button", name="Создать следующую партию").count() == 0)
    viewer.close()
    login(page)
    _open_panel(page, "migration", "Перенесённые материалы")
    issue = page.locator("[data-migration-issue]").filter(has_text="Священник E2E").first
    issue.get_by_role("button", name="Исправить").click()
    page.locator('[data-schema-field="rank"] input').fill("Иерей")
    page.locator("[data-save-draft]").last.click()
    page.locator("[data-audit-current]").click()
    page.locator("[data-card-review]").get_by_text("0 блокирующих ошибок", exact=False).wait_for()
    coverage_steps.mark("stage7.e2e.correct-audit")
    page.reload()
    page.locator('.cms-type[data-content-type="clergy"]').click()
    picker = page.locator("[data-content-select]")
    option = picker.locator("option").filter(has_text="! Священник E2E")
    option.wait_for(state="attached")
    picker.select_option(option.get_attribute("value"))
    page.locator("[data-card-review]").get_by_text("0 блокирующих ошибок", exact=False).wait_for()
    coverage_steps.mark("stage7.e2e.reload")
    other = browser.new_page()
    other.goto(f"{live_cms.base_url}/cms.html")
    login(other, "e2e_editor", SEEDED_PASSWORD)
    other.locator('.cms-type[data-content-type="clergy"]').click()
    other_picker = other.locator("[data-content-select]")
    other_option = other_picker.locator("option").filter(has_text="! Священник E2E")
    other_option.wait_for(state="attached")
    other_picker.select_option(other_option.get_attribute("value"))
    other.locator('[data-schema-field="position"] input').fill("Второй редактор")
    other.locator("[data-save-draft]").last.click()
    other.get_by_text("Черновик сохранён").wait_for()
    page.locator('[data-schema-field="rank"] input').fill("Протоиерей")
    page.locator("[data-save-draft]").last.click()
    page.get_by_role("button", name="Открыть свежую версию").wait_for()
    assert page.locator('[data-schema-field="rank"] input').input_value() == "Протоиерей"
    page.get_by_role("button", name="Открыть свежую версию").click()
    page.locator("[data-unsaved-discard]").click()
    expect(page.locator('[data-schema-field="position"] input')).to_have_value("Второй редактор")
    other.close()
    page.locator("[data-audit-current]").click()
    page.locator("[data-card-review]").get_by_text("0 блокирующих ошибок", exact=False).wait_for()
    coverage_steps.mark("stage7.e2e.conflict")
    _open_panel(page, "migration", "Перенесённые материалы")
    page.get_by_role("button", name="Запустить аудит", exact=True).click()
    expect(page.locator("[data-migration-pilot-reason]")).to_have_text("Семантический шлюз пройден", timeout=60000)
    page.get_by_role("button", name="Создать следующую партию").click()
    page.get_by_label("Поиск материалов").fill("Священник E2E")
    page.get_by_role("button", name="Найти материалы").click()
    page.locator("[data-candidate-select]:not(:disabled)").first.check()
    page.get_by_label("Название партии").fill("Приёмка священника")
    page.get_by_role("button", name="Создать партию").click()
    page.get_by_role("heading", name="Приёмка священника", exact=True).wait_for()
    coverage_steps.mark("stage7.e2e.batch")
    card = page.locator("[data-batch-content]").first
    card.locator("[data-batch-reviewed]").check()
    card.locator("[data-batch-disposition]").select_option("accept")
    card.locator("[data-batch-note]").fill("Содержимое проверено")
    for field in card.locator("[data-batch-warning]").all():
        field.fill("Дубли проверены")
    card.locator("[data-batch-item-save]").click()
    page.get_by_text("1/1 решений", exact=False).wait_for()
    page.locator("[data-batch-submit]").click()
    page.locator(".workflow-panel__head .state-pill").filter(has_text="На утверждении").wait_for()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("[data-batch-finalize]").click()
    page.locator(".workflow-panel__head .state-pill").filter(has_text="Завершена").wait_for()
    coverage_steps.mark("stage7.e2e.accept")
    page.locator("[data-batch-content]").first.get_by_role("button", name="Редактор и preview").click()
    page.locator('[data-workflow-action="submit-review"]').click()
    page.locator("[data-workflow-state]").filter(has_text="На проверке").wait_for()
    page.locator('[data-workflow-action="publish"]').click()
    publish = page.locator("[data-publish-dialog]")
    publish.locator('input[type="checkbox"]').last.check()
    publish.locator("[data-publish-confirm]").click()
    page.locator("[data-workflow-state]").filter(has_text="На сайте").wait_for()
    coverage_steps.mark("stage7.e2e.publish")
    page.goto(f"{live_cms.base_url}/about")
    page.get_by_role("link", name="Священник E2E", exact=False).wait_for()
    coverage_steps.mark("stage7.e2e.public")


@pytest.mark.e2e
@pytest.mark.full
@pytest.mark.expected_http_error(409, "/api/admin/contents/")
def test_cms_51_conflict_recovery_ui(page, browser, live_cms, coverage_steps):
    login(page)
    open_content(page, "clergy", "E2E clergy")
    second = browser.new_page()
    second.goto(f"{live_cms.base_url}/cms.html")
    login(second)
    open_content(second, "clergy", "E2E clergy")
    second.locator('[data-schema-field="position"] input').fill("Другая правка")
    second.locator("[data-save-draft]").last.click()
    second.get_by_text("Черновик сохранён").wait_for()
    page.locator('[data-schema-field="rank"] input').fill("Моя правка")
    page.locator("[data-save-draft]").last.click()
    page.get_by_role("button", name="Открыть свежую версию").wait_for()
    coverage_steps.check("stage7.conflict.preserved", page.locator('[data-schema-field="rank"] input').input_value() == "Моя правка")
    page.get_by_role("button", name="Открыть свежую версию").click()
    page.locator("[data-unsaved-discard]").click()
    expect(page.locator('[data-schema-field="position"] input')).to_have_value("Другая правка")
    coverage_steps.mark("stage7.conflict.reload")
    second.close()


CREATED_PASSWORD = "Created-Strong-Password-2026!"
SEEDED_PASSWORD = "Strong-Password-2026!"
CHANGED_PASSWORD = "Changed-Password-2026!"


def _open_panel(page, name: str, heading: str) -> None:
    page.locator(f'[data-panel="{name}"]').click()
    page.get_by_role("heading", name=heading, exact=True).wait_for()


def _user_row(page, username: str):
    return page.locator("[data-user-id]").filter(has_text=username)


def _create_user(page, username: str, role: str) -> None:
    page.locator("[data-user-create]").click()
    dialog = page.locator("[data-user-create-dialog]")
    dialog.locator('[name="username"]').fill(username)
    dialog.locator('[name="password"]').fill(CREATED_PASSWORD)
    dialog.locator('[name="role"]').select_option(role)
    with page.expect_response(
        lambda response: response.url.endswith("/api/admin/users")
        and response.request.method == "POST"
    ) as pending:
        dialog.get_by_role("button", name="Создать").click()
    assert pending.value.status == 201
    _user_row(page, username).wait_for()


@pytest.mark.e2e
@pytest.mark.full
@pytest.mark.expected_http_error(409, "/api/admin/users")
def test_user_lifecycle_and_password(page, browser, live_cms, coverage_steps):
    login(page)
    _open_panel(page, "users", "Пользователи")

    page.locator("[data-user-create]").click()
    create_dialog = page.locator("[data-user-create-dialog]")
    coverage_steps.check("users.create-dialog-open", create_dialog.is_visible())
    create_dialog.locator('[data-user-create-close][aria-label="Закрыть"]').click()
    coverage_steps.check("users.create-dialog-close-x", not create_dialog.is_visible())
    page.locator("[data-user-create]").click()
    create_dialog.get_by_role("button", name="Отмена").click()
    coverage_steps.check("users.create-dialog-cancel", not create_dialog.is_visible())

    page.locator("[data-user-create]").click()
    create_form = create_dialog.locator("[data-user-create-form]")
    create_form.locator('[name="username"]').fill("ui_invalid")
    password_input = create_form.locator('[name="password"]')
    password_input.fill("short")
    coverage_steps.check(
        "users.password-html-validation",
        password_input.evaluate("node => node.minLength === 12 && node.validity.tooShort")
        and create_dialog.is_visible(),
    )
    create_dialog.locator('[data-user-create-close][aria-label="Закрыть"]').click()

    for username, role, assertion_id in (
        ("ui_viewer", "viewer", "users.create-viewer"),
        ("ui_editor", "editor", "users.create-editor"),
        ("ui_publisher", "publisher", "users.create-publisher"),
    ):
        _create_user(page, username, role)
        coverage_steps.check(
            assertion_id,
            _user_row(page, username).locator("[data-user-role]").input_value() == role,
        )

    page.locator("[data-user-create]").click()
    create_dialog.locator('[name="username"]').fill("ui_viewer")
    create_dialog.locator('[name="password"]').fill(CREATED_PASSWORD)
    create_dialog.locator('[name="role"]').select_option("viewer")
    with page.expect_response(
        lambda response: response.url.endswith("/api/admin/users")
        and response.request.method == "POST"
    ) as duplicate:
        create_dialog.get_by_role("button", name="Создать").click()
    create_dialog.locator("[data-user-create-error]").filter(
        has_text="уже существует"
    ).wait_for()
    coverage_steps.check("users.duplicate-rejected", duplicate.value.status == 409)
    create_dialog.locator('[data-user-create-close][aria-label="Закрыть"]').click()

    publisher_row = _user_row(page, "ui_publisher")
    publisher_row.locator("[data-user-role]").select_option("editor")
    with page.expect_response(
        lambda response: "/api/admin/users/" in response.url
        and response.request.method == "PATCH"
    ):
        publisher_row.locator("[data-user-save]").click()
    publisher_row = _user_row(page, "ui_publisher")
    publisher_row.locator("[data-user-role]").wait_for()
    coverage_steps.check(
        "users.role-update",
        publisher_row.locator("[data-user-role]").input_value() == "editor",
    )

    viewer_row = _user_row(page, "ui_viewer")
    viewer_row.locator("[data-user-active]").uncheck()
    with page.expect_response(
        lambda response: "/api/admin/users/" in response.url
        and response.request.method == "PATCH"
    ):
        viewer_row.locator("[data-user-save]").click()
    viewer_row = _user_row(page, "ui_viewer")
    viewer_row.locator("[data-user-active]").wait_for()
    coverage_steps.check("users.disable", not viewer_row.locator("[data-user-active]").is_checked())
    viewer_row.locator("[data-user-active]").check()
    with page.expect_response(
        lambda response: "/api/admin/users/" in response.url
        and response.request.method == "PATCH"
    ):
        viewer_row.locator("[data-user-save]").click()
    viewer_row = _user_row(page, "ui_viewer")
    viewer_row.locator("[data-user-active]").wait_for()
    coverage_steps.check("users.enable", viewer_row.locator("[data-user-active]").is_checked())

    page.locator("[data-open-profile]").click()
    password_dialog = page.locator("[data-password-dialog]")
    coverage_steps.check("users.password-dialog-open", password_dialog.is_visible())
    password_dialog.locator('[data-password-close][aria-label="Закрыть"]').click()
    coverage_steps.check("users.password-dialog-close-x", not password_dialog.is_visible())
    page.locator("[data-open-profile]").click()
    password_dialog.get_by_role("button", name="Отмена").click()
    coverage_steps.check("users.password-dialog-cancel", not password_dialog.is_visible())

    viewer_context = browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="ru-RU",
        timezone_id="Europe/Moscow",
    )
    viewer_page = viewer_context.new_page()
    viewer_page.set_default_timeout(10_000)
    try:
        viewer_page.goto(f"{live_cms.base_url}/cms.html", wait_until="domcontentloaded")
        login(viewer_page, "e2e_viewer", SEEDED_PASSWORD)
        viewer_page.locator("[data-open-profile]").click()
        viewer_password = viewer_page.locator("[data-password-dialog]")
        viewer_password.locator('[name="current_password"]').fill(SEEDED_PASSWORD)
        viewer_password.locator('[name="new_password"]').fill(CHANGED_PASSWORD)
        viewer_password.locator('[name="confirm_password"]').fill("Different-Password-2026!")
        viewer_password.get_by_role("button", name="Сменить пароль").click()
        viewer_password.locator("[data-password-error]").filter(
            has_text="не совпадают"
        ).wait_for()
        coverage_steps.mark("users.password-mismatch")

        viewer_password.locator('[name="current_password"]').fill("Wrong-Current-Password-2026!")
        viewer_password.locator('[name="new_password"]').fill(CHANGED_PASSWORD)
        viewer_password.locator('[name="confirm_password"]').fill(CHANGED_PASSWORD)
        with viewer_page.expect_response(
            lambda response: response.url.endswith("/api/admin/change-password")
        ) as wrong_current:
            viewer_password.get_by_role("button", name="Сменить пароль").click()
        viewer_password.locator("[data-password-error]").filter(
            has_text="Текущий пароль указан неверно"
        ).wait_for()
        coverage_steps.check("users.password-current-rejected", wrong_current.value.status == 401)

        viewer_password.locator('[name="current_password"]').fill(SEEDED_PASSWORD)
        with viewer_page.expect_response(
            lambda response: response.url.endswith("/api/admin/change-password")
        ) as changed:
            viewer_password.get_by_role("button", name="Сменить пароль").click()
        viewer_page.locator("[data-login-dialog]").wait_for(state="visible")
        coverage_steps.check("users.password-change", changed.value.status == 200)

        login_dialog = viewer_page.locator("[data-login-dialog]")
        login_dialog.locator('[name="username"]').fill("e2e_viewer")
        login_dialog.locator('[name="password"]').fill(SEEDED_PASSWORD)
        with viewer_page.expect_response(
            lambda response: response.url.endswith("/api/admin/login")
        ) as old_login:
            login_dialog.get_by_role("button", name="Войти").click()
        login_dialog.locator("[data-login-error]").filter(has_text="Неверное").wait_for()
        coverage_steps.check("users.password-old-rejected", old_login.value.status == 401)
        login(viewer_page, "e2e_viewer", CHANGED_PASSWORD)
        coverage_steps.check(
            "users.password-new-login",
            viewer_page.locator(".cms-user strong").inner_text() == "e2e_viewer",
        )

        _open_panel(page, "users", "Пользователи")
        seeded_viewer = _user_row(page, "e2e_viewer")
        seeded_viewer.locator("[data-user-terminate]").wait_for()
        coverage_steps.check(
            "users.session-visible",
            int(seeded_viewer.locator(".user-row__sessions b").inner_text()) >= 1,
        )
        page.once("dialog", lambda dialog: dialog.dismiss())
        seeded_viewer.locator("[data-user-terminate]").click()
        coverage_steps.check(
            "users.terminate-cancel",
            int(seeded_viewer.locator(".user-row__sessions b").inner_text()) >= 1,
        )
        page.once("dialog", lambda dialog: dialog.accept())
        with page.expect_response(
            lambda response: "/terminate-sessions" in response.url
            and response.request.method == "POST"
        ):
            seeded_viewer.locator("[data-user-terminate]").click()
        seeded_viewer = _user_row(page, "e2e_viewer")
        seeded_viewer.locator(".user-row__sessions b").filter(has_text="0").wait_for()
        coverage_steps.mark("users.terminate-confirm")

        session = viewer_page.request.get(f"{live_cms.base_url}/api/admin/session")
        viewer_page.reload(wait_until="domcontentloaded")
        viewer_page.locator("[data-login-dialog]").wait_for(state="visible")
        coverage_steps.check(
            "users.session-revoked",
            session.ok and session.json() == {"authenticated": False},
        )
        coverage_steps.check(
            "users.events-visible",
            all(
                label in page.locator(".user-events").inner_text()
                for label in ("Пользователь создан", "Роль или состояние изменены", "Сессии завершены")
            ),
        )
    finally:
        viewer_context.close()


@pytest.mark.e2e
@pytest.mark.full
def test_full_role_capabilities(browser, live_cms, coverage_steps):
    expected = {
        "viewer": {
            "create": False, "submissions": False, "users": False,
            "bulk": False, "upload": False, "audit": False, "reindex": False,
            "prepare": False, "apply_prepare": False,
        },
        "editor": {
            "create": True, "submissions": False, "users": False,
            "bulk": False, "upload": True, "audit": False, "reindex": False,
            "prepare": False, "apply_prepare": False,
        },
        "publisher": {
            "create": True, "submissions": True, "users": False,
            "bulk": True, "upload": True, "audit": True, "reindex": False,
            "prepare": True, "apply_prepare": False,
        },
        "admin": {
            "create": True, "submissions": True, "users": True,
            "bulk": True, "upload": True, "audit": True, "reindex": True,
            "prepare": True, "apply_prepare": True,
        },
    }
    for role, permissions in expected.items():
        context = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow")
        role_page = context.new_page()
        role_page.set_default_timeout(10_000)
        try:
            role_page.goto(f"{live_cms.base_url}/cms.html", wait_until="domcontentloaded")
            username = "admin" if role == "admin" else f"e2e_{role}"
            password = "test-password" if role == "admin" else SEEDED_PASSWORD
            login(role_page, username, password)
            actual = {
                "create": not role_page.locator("[data-create-current]").is_disabled(),
                "submissions": role_page.locator('[data-panel="submissions"]').is_visible(),
                "users": role_page.locator('[data-panel="users"]').is_visible(),
            }
            _open_panel(role_page, "workflow", "Массовые действия")
            actual["bulk"] = role_page.locator("[data-bulk-apply]").is_visible()
            _open_panel(role_page, "media", "Медиатека")
            actual["upload"] = role_page.locator("[data-panel-media-upload]").count() == 1
            actual["reindex"] = role_page.locator("[data-media-reindex]").count() == 1
            _open_panel(role_page, "migration", "Перенесённые материалы")
            actual["audit"] = (
                role_page.locator("[data-migration-run]").count() == 1
                and role_page.locator("[data-migration-pilot]").count() == 1
            )
            actual["prepare"] = role_page.locator("[data-preparation-preview]").count() == 1
            actual["apply_prepare"] = role_page.locator("[data-preparation-apply]").count() == 1
            coverage_steps.check(
                f"roles.{role}-capabilities",
                actual == permissions,
                f"{role}: expected {permissions}, got {actual}",
            )
        finally:
            context.close()


@pytest.mark.e2e
@pytest.mark.full
def test_migration_dashboard_and_audit(page, live_cms, coverage_steps):
    now = "2026-07-21T00:00:00+00:00"
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        content_id, version, raw_data = connection.execute(
            "SELECT id,version,data_json FROM contents WHERE content_type='news' ORDER BY title LIMIT 1"
        ).fetchone()
        data = json.loads(raw_data or "{}")
        data["publication_date"] = "2010-01-01"
        connection.execute(
            "UPDATE contents SET data_json=? WHERE id=?",
            (json.dumps(data, ensure_ascii=False), content_id),
        )
        for index in range(55):
            connection.execute(
                """INSERT INTO migration_review_issues(
                     id,audit_run_id,content_id,content_version,code,severity,field_path,fingerprint,
                     status,message,details_json,detected_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"e2e-pagination-issue-{index:03d}", "e2e-audit", content_id, version,
                    f"e2e_issue_{index:03d}", "info", "title", f"e2e-pagination-fingerprint-{index:03d}",
                    "open", f"Тестовая проблема пагинации {index:03d}", "{}", now,
                ),
            )

    login(page)
    _open_panel(page, "migration", "Перенесённые материалы")
    page.locator("[data-acceptance-issue-count]").wait_for()
    coverage_steps.check(
        "migration.dashboard-metrics",
        page.locator("[data-acceptance-metrics] .metric-card").count() == 4
        and page.locator("[data-acceptance-breakdown] section").count() == 2,
    )

    severity = page.locator("[data-migration-severity]")
    year = page.locator("[data-migration-year]")
    content_type = page.locator("[data-migration-type]")
    query = page.locator("[data-migration-query]")
    apply_filter = page.locator("[data-migration-filter]")

    severity.select_option("blocker")
    with page.expect_response(lambda response: "/migration/issues?" in response.url and "severity=blocker" in response.url) as filtered:
        apply_filter.click()
    blocker_result = filtered.value.json()
    coverage_steps.check(
        "migration.filter-severity",
        blocker_result["total"] > 0 and all(item["severity"] == "blocker" for item in blocker_result["items"]),
    )
    severity.select_option("")

    year.fill("2010")
    with page.expect_response(lambda response: "/migration/issues?" in response.url and "year=2010" in response.url) as filtered:
        apply_filter.click()
    year_result = filtered.value.json()
    coverage_steps.check("migration.filter-year", year_result["total"] >= 56)
    year.fill("")

    content_type.select_option("news")
    with page.expect_response(lambda response: "/migration/issues?" in response.url and "content_type=news" in response.url) as filtered:
        apply_filter.click()
    type_result = filtered.value.json()
    coverage_steps.check(
        "migration.filter-type",
        type_result["total"] >= 56 and all(item["content_type"] == "news" for item in type_result["items"]),
    )
    content_type.select_option("")

    query.fill("пагинации 010")
    with page.expect_response(lambda response: "/migration/issues?" in response.url and "q=" in response.url) as filtered:
        apply_filter.click()
    query_result = filtered.value.json()
    coverage_steps.check(
        "migration.filter-query",
        query_result["total"] == 1 and query_result["items"][0]["code"] == "e2e_issue_010",
    )
    query.fill("")
    with page.expect_response(lambda response: "/migration/issues?" in response.url and "offset=0" in response.url) as reset:
        apply_filter.click()
    coverage_steps.check("migration.filter-reset", reset.value.json()["total"] >= 59)

    next_button = page.locator("[data-migration-issues-next]")
    page.wait_for_function("() => !document.querySelector('[data-migration-issues-next]').disabled")
    coverage_steps.check("migration.pagination-enabled", not next_button.is_disabled())
    with page.expect_response(lambda response: "/migration/issues?" in response.url and "offset=50" in response.url):
        next_button.click()
    page.wait_for_function("() => !document.querySelector('[data-migration-issues-prev]').disabled")
    coverage_steps.check("migration.pagination-next", not page.locator("[data-migration-issues-prev]").is_disabled())
    with page.expect_response(lambda response: "/migration/issues?" in response.url and "offset=0" in response.url):
        page.locator("[data-migration-issues-prev]").click()
    page.wait_for_function("() => document.querySelector('[data-migration-issues-prev]').disabled")
    coverage_steps.check("migration.pagination-prev", page.locator("[data-migration-issues-prev]").is_disabled())

    query.fill("Не заполнена обложка")
    with page.expect_response(lambda response: "/migration/issues?" in response.url and "q=" in response.url):
        apply_filter.click()
    issue = page.locator(".acceptance-issue").filter(has_text="required_field_missing")
    issue.wait_for()
    issue.locator("[data-migration-open-content]").click()
    page.locator("[data-editor-pane]").wait_for(state="visible")
    coverage_steps.check(
        "migration.issue-open-editor",
        page.locator("[data-editor-title]").inner_text().startswith("E2E"),
    )

    _open_panel(page, "migration", "Перенесённые материалы")
    with page.expect_response(
        lambda response: response.url.endswith("/api/admin/migration/audits")
        and response.request.method == "POST"
    ) as queued:
        page.locator("[data-migration-run]").click()
    queued_run = queued.value.json()
    coverage_steps.check(
        "migration.audit-queued",
        queued.value.status == 202 and queued_run["status"] == "queued",
    )
    completed = page.evaluate(
        """async runId => {
          const deadline = Date.now() + 45000;
          while (Date.now() < deadline) {
            const response = await fetch(`/api/admin/migration/audits/${runId}`);
            if (!response.ok) throw new Error(`Audit status HTTP ${response.status}`);
            const run = await response.json();
            if (["completed", "failed"].includes(run.status)) return run;
            await new Promise(resolve => setTimeout(resolve, 250));
          }
          throw new Error("Migration audit did not finish within 45 seconds");
        }""",
        queued_run["id"],
    )
    coverage_steps.check("migration.audit-completed", completed["status"] == "completed", str(completed))
    _open_panel(page, "migration", "Перенесённые материалы")
    completed_runs = page.locator(".acceptance-run").filter(has_text="completed")
    completed_runs.first.wait_for()
    coverage_steps.check("migration.audit-visible", completed_runs.count() >= 2)


@pytest.mark.e2e
@pytest.mark.full
def test_stage6_automatic_preparation_workflow(page, live_cms, coverage_steps):
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        connection.execute(
            """UPDATE contents SET migration_review_required=1
               WHERE content_type='news' AND title LIKE 'E2E pagination %'"""
        )
        sample_id, sample_version = connection.execute(
            """SELECT id,version FROM contents
               WHERE content_type='page' AND migration_review_required=1
               ORDER BY title LIMIT 1"""
        ).fetchone()
        gallery_id = connection.execute(
            "SELECT id FROM contents WHERE content_type='gallery' ORDER BY title LIMIT 1"
        ).fetchone()[0]
        gallery_data = {
            "event_date": "2026-07-30",
            "category": "Жизнь прихода",
            "summary": "Альбом без ложного дубля.",
            "cover": "assets/school-maslenitsa.jpg",
            "photos": [{
                "image": "assets/school-maslenitsa.jpg",
                "alt": "Приходской праздник",
                "order": 1,
            }],
        }
        connection.execute(
            "UPDATE contents SET data_json=?,migration_review_required=1 WHERE id=?",
            (json.dumps(gallery_data, ensure_ascii=False), gallery_id),
        )
    audit = queue_audit(
        live_cms.settings.database_path,
        actor_id=None,
        scope={"check_external": False},
    )
    execute_audit_run(
        live_cms.settings.database_path,
        live_cms.settings.schema_path,
        live_cms.settings.media_dir,
        live_cms.settings.site_dir,
        audit["id"],
        check_external=False,
    )
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        false_duplicates = connection.execute(
            """SELECT COUNT(*) FROM migration_review_issues
               WHERE content_id=? AND code='duplicate_media_reference' AND status='open'""",
            (gallery_id,),
        ).fetchone()[0]
    coverage_steps.check(
        "stage6.preparation.cover-photo-not-duplicate",
        false_duplicates == 0,
    )

    login(page)
    _open_panel(page, "migration", "Перенесённые материалы")
    preview_button = page.locator("[data-preparation-preview]")
    page.wait_for_function("() => !document.querySelector('[data-preparation-preview]').disabled")
    with page.expect_response(
        lambda response: response.url.endswith("/api/admin/migration/preparations")
        and response.request.method == "POST"
    ) as queued:
        preview_button.click()
    preview_run = queued.value.json()
    coverage_steps.check(
        "stage6.preparation.preview-queued",
        queued.value.status == 202 and preview_run["mode"] == "preview" and preview_run["status"] == "queued",
    )
    completed_preview = page.evaluate(
        """async runId => {
          const deadline = Date.now() + 45000;
          while (Date.now() < deadline) {
            const response = await fetch(`/api/admin/migration/preparations/${runId}`);
            const run = await response.json();
            if (["completed", "failed"].includes(run.status)) return run;
            await new Promise(resolve => setTimeout(resolve, 250));
          }
          throw new Error("Preparation preview did not finish within 45 seconds");
        }""",
        preview_run["id"],
    )
    coverage_steps.check(
        "stage6.preparation.preview-completed",
        completed_preview["status"] == "completed" and completed_preview["counts"]["contents"] >= 59,
        str(completed_preview),
    )
    page.wait_for_function(
        "() => document.querySelector('[data-preparation-status]')?.textContent.includes('preview · completed')"
    )
    coverage_steps.check(
        "stage6.preparation.metrics",
        page.locator("[data-preparation-metrics] .metric-card").count() == 4
        and int(page.locator("[data-preparation-count]").inner_text().split()[0]) >= 50,
    )

    page.locator("[data-preparation-classification]").select_option("editorial_review")
    page.locator("[data-preparation-filter]").click()
    page.wait_for_function(
        "() => document.querySelectorAll('[data-preparation-item]').length > 0"
    )
    dossier_button = page.locator("[data-preparation-item]").first
    dossier_button.click()
    page.locator(".preparation-dossier").wait_for()
    dossier_checks = {
        "diff": page.locator(".preparation-diff article").count() == 2,
        "recommendation": page.get_by_role("heading", name="Рекомендация", exact=True).is_visible(),
        "reasons": page.locator(".dossier-reasons article").count() > 0,
        "reason_code": page.locator(".dossier-reasons article").first.locator("b").inner_text().strip() != "",
        "reason_field": "Поле:" in page.locator(".dossier-reasons article").first.inner_text(),
        "rules": (
            page.locator(".dossier-rules article").count() > 0
            or "Данные уже соответствуют правилам" in page.locator(".dossier-rules").inner_text()
        ),
        "options": page.locator(".preparation-dossier li p").count() > 0,
        "focus": page.locator("#preparation-dossier-title").evaluate(
            "node => document.activeElement === node"
        ),
    }
    coverage_steps.check(
        "stage6.preparation.dossier",
        all(dossier_checks.values()),
        str(dossier_checks),
    )
    page.locator("[data-migration-back]").click()
    page.get_by_role("heading", name="Перенесённые материалы", exact=True).wait_for()
    coverage_steps.check(
        "stage6.preparation.dashboard-back",
        page.locator("[data-preparation-item]").first.evaluate(
            "node => document.activeElement === node"
        ),
    )

    page.locator("[data-preparation-classification]").select_option("editorial_review")
    with page.expect_response(
        lambda response: "/items?" in response.url and "classification=editorial_review" in response.url
    ) as filtered:
        page.locator("[data-preparation-filter]").click()
    coverage_steps.check(
        "stage6.preparation.filter",
        all(item["classification"] == "editorial_review" for item in filtered.value.json()["items"]),
    )
    page.wait_for_function("() => !document.querySelector('[data-preparation-next]').disabled")
    with page.expect_response(lambda response: "/items?" in response.url and "offset=50" in response.url):
        page.locator("[data-preparation-next]").click()
    coverage_steps.check(
        "stage6.preparation.pagination-next",
        not page.locator("[data-preparation-prev]").is_disabled(),
    )
    with page.expect_response(lambda response: "/items?" in response.url and "offset=0" in response.url):
        page.locator("[data-preparation-prev]").click()
    coverage_steps.check(
        "stage6.preparation.pagination-prev",
        page.locator("[data-preparation-prev]").is_disabled(),
    )

    apply_button = page.locator("[data-preparation-apply]")
    page.once("dialog", lambda dialog: dialog.dismiss())
    apply_button.click()
    coverage_steps.check("stage6.preparation.apply-dismiss", apply_button.is_enabled())
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        lambda response: response.url.endswith("/api/admin/migration/preparations")
        and response.request.method == "POST"
    ) as apply_queued:
        apply_button.click()
    apply_run = apply_queued.value.json()
    completed_apply = page.evaluate(
        """async runId => {
          const deadline = Date.now() + 45000;
          while (Date.now() < deadline) {
            const response = await fetch(`/api/admin/migration/preparations/${runId}`);
            const run = await response.json();
            if (["completed", "failed"].includes(run.status)) return run;
            await new Promise(resolve => setTimeout(resolve, 250));
          }
          throw new Error("Preparation apply did not finish within 45 seconds");
        }""",
        apply_run["id"],
    )
    coverage_steps.check(
        "stage6.preparation.apply-completed",
        completed_apply["status"] == "completed" and completed_apply["counts"].get("technical_blocker", 0) == 0,
        str(completed_apply),
    )
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        after_version, review_required = connection.execute(
            "SELECT version,migration_review_required FROM contents WHERE id=?",
            (sample_id,),
        ).fetchone()
    coverage_steps.check(
        "stage6.preparation.flags-preserved",
        after_version > sample_version and review_required == 1,
    )
    page.wait_for_function(
        "() => !document.querySelector('[data-preparation-rollback]')?.hidden"
    )
    rollback = page.locator("[data-preparation-rollback]")
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/admin/migration/preparations/{apply_run['id']}/rollback")
    ) as rolled_back:
        rollback.click()
    coverage_steps.check(
        "stage6.preparation.rollback",
        rolled_back.value.status == 200 and rolled_back.value.json()["status"] == "rolled_back",
    )
    page.get_by_role("heading", name="Перенесённые материалы", exact=True).wait_for()
    page.set_viewport_size({"width": 390, "height": 844})
    coverage_steps.check(
        "stage6.preparation.mobile",
        page.locator(".preparation-section").is_visible()
        and page.locator("[data-preparation-preview]").is_visible()
        and page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"),
    )


def _prepare_editorial_gate(live_cms):
    settings = live_cms.settings
    audit = queue_audit(
        settings.database_path, actor_id=None, scope={"check_external": False}
    )
    execute_audit_run(
        settings.database_path, settings.schema_path, settings.media_dir,
        settings.site_dir, audit["id"], check_external=False,
    )
    preview = queue_preparation(settings.database_path, actor_id=None)
    preview = execute_preparation_run(settings, preview["id"])
    apply_run = queue_preparation(
        settings.database_path,
        actor_id=None,
        mode="apply",
        preview_run_id=preview["id"],
    )
    execute_preparation_run(settings, apply_run["id"])
    post_audit = queue_audit(
        settings.database_path, actor_id=None, scope={"check_external": False}
    )
    execute_audit_run(
        settings.database_path, settings.schema_path, settings.media_dir,
        settings.site_dir, post_audit["id"], check_external=False,
    )


def _seed_stage7_test_issues(live_cms):
    """Add current-version editorial fixtures without weakening the full gate."""
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        apply_id = connection.execute(
            """SELECT id FROM migration_preparation_runs
               WHERE mode='apply' AND status='completed'
               ORDER BY finished_at DESC,id DESC LIMIT 1"""
        ).fetchone()[0]
        rows = connection.execute(
            """SELECT id,version,content_type FROM contents
               WHERE content_type IN ('site_contact','page','clergy','parish_section')
                 AND (
                   content_type IN ('site_contact','clergy','parish_section')
                   OR COALESCE(legacy_url,'') LIKE '/o-hrame/%'
                 )
               ORDER BY content_type,title,id LIMIT 4"""
        ).fetchall()
        connection.execute(
            """INSERT INTO migration_audit_runs(
                 id,rules_version,scope_json,status,counts_json,created_at,started_at,finished_at,
                 identity_hash,recovered
               ) VALUES(
                 'e2e-stage7-audit','1.1.0','{"content_id":"e2e-stage7"}',
                 'completed','{}','2099-01-01T00:00:00+00:00',
                 '2099-01-01T00:00:00+00:00','2099-01-01T00:00:00+00:00',
                 'e2e-stage7-audit',0
               )"""
        )
        for index, (content_id, version, _) in enumerate(rows):
            blocker_count = int(index == 0)
            warning_count = int(index in {1, 2})
            connection.execute(
                """UPDATE migration_preparation_items
                   SET classification='editorial_review'
                   WHERE run_id=? AND content_id=?""",
                (apply_id, content_id),
            )
            existing = connection.execute("""SELECT id,assessment_json FROM migration_editorial_assessments
                WHERE content_id=? AND content_version=? ORDER BY rowid DESC LIMIT 1""", (content_id, version)).fetchone()
            if existing:
                assessment = json.loads(existing[1])
                assessment["classification"] = "editorial_review"
                extra_reason = ("required_field_requires_editor" if index == 0 else
                                "duplicate_content" if index == 1 else
                                "text_extraction_ambiguous" if index == 2 else None)
                if extra_reason:
                    assessment.setdefault("reasons", []).append({"code": extra_reason, "message": "Тестовая редакционная причина"})
                connection.execute("UPDATE migration_editorial_assessments SET assessment_json=? WHERE id=?", (json.dumps(assessment, ensure_ascii=False), existing[0]))
            connection.execute(
                """INSERT INTO migration_audit_items(
                     run_id,content_id,content_version,blocker_count,warning_count,
                     info_count,scanned_at
                   ) VALUES('e2e-stage7-audit',?,?,?,?,0,'2099-01-01T00:00:00+00:00')""",
                (content_id, version, blocker_count, warning_count),
            )
            if index not in {0, 1, 2}:
                continue
            code = (
                "required_field_missing" if index == 0
                else "duplicate_content" if index == 1
                else "legacy_text"
            )
            severity = "blocker" if index == 0 else "warning"
            connection.execute(
                """INSERT INTO migration_review_issues(
                     id,audit_run_id,content_id,content_version,code,severity,
                     field_path,fingerprint,status,message,details_json,detected_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"e2e-stage7-issue-{index}", "e2e-stage7-audit", content_id,
                    version, code, severity, "title", f"e2e-stage7-{index}",
                    "open", f"Тестовая проблема этапа 7: {code}", "{}",
                    "2099-01-01T00:00:00+00:00",
                ),
            )


def _create_pilot(page, live_cms):
    _prepare_editorial_gate(live_cms)
    _seed_stage7_test_issues(live_cms)
    _open_panel(page, "migration", "Перенесённые материалы")
    with page.expect_response(
        lambda response: response.url.endswith("/api/admin/migration/batches/pilot")
        and response.request.method == "POST"
    ) as created:
        page.locator("[data-migration-pilot]").click()
    batch = created.value.json()
    page.get_by_role("heading", name="Пилот: ключевые разделы", exact=True).wait_for()
    return batch


@pytest.mark.e2e
@pytest.mark.full
def test_migration_batch_finalize(page, live_cms, coverage_steps):
    login(page)
    batch = _create_pilot(page, live_cms)
    batch_id = batch["id"]
    coverage_steps.check(
        "migration.batch-pilot-create",
        batch["status"] == "draft" and 0 < len(batch["items"]) <= 50,
        str(batch),
    )

    source_card = page.locator("[data-batch-content]:has(details)")
    source_card.locator("summary").click()
    coverage_steps.check("migration.batch-source-open", source_card.locator("details").evaluate("node => node.open"))
    source_card.locator("summary").click()
    coverage_steps.check("migration.batch-source-close", not source_card.locator("details").evaluate("node => node.open"))
    source_card.locator("[data-migration-open-content]").click()
    page.locator("[data-editor-pane]").wait_for(state="visible")
    coverage_steps.check(
        "migration.batch-editor-open",
        page.locator("[data-editor-title]").inner_text() == "E2E page",
    )

    with page.expect_response(lambda response: response.url.endswith(f"/api/admin/migration/batches/{batch_id}")):
        page.locator("[data-editor-return]").click()
    page.get_by_role("heading", name="Пилот: ключевые разделы", exact=True).wait_for()
    coverage_steps.check(
        "migration.batch-context-return",
        page.locator(f'[data-batch-content="{source_card.get_attribute("data-batch-content")}"] [data-migration-open-content]').evaluate(
            "node => document.activeElement === node"
        ),
    )
    page.locator("[data-migration-back]").click()
    page.get_by_role("heading", name="Перенесённые материалы", exact=True).wait_for()
    coverage_steps.mark("migration.batch-dashboard-back")
    with page.expect_response(lambda response: response.url.endswith(f"/api/admin/migration/batches/{batch_id}")):
        page.locator(f'[data-migration-batch="{batch_id}"]').click()
    page.get_by_role("heading", name="Пилот: ключевые разделы", exact=True).wait_for()
    coverage_steps.mark("migration.batch-reopen")
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        initial_statuses = dict(connection.execute(
            """SELECT c.id,c.status
               FROM contents c JOIN migration_review_batch_items bi ON bi.content_id=c.id
               WHERE bi.batch_id=?""",
            (batch_id,),
        ).fetchall())

    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("[data-batch-cancel]").click()
    coverage_steps.check(
        "migration.batch-cancel-dismiss",
        page.locator(".workflow-panel__head .state-pill").inner_text() == "Черновик",
    )

    blocker_recorded = False
    individual_warning_recorded = False
    while True:
        cards = page.locator("[data-batch-content]")
        pending_card = None
        for index in range(cards.count()):
            candidate = cards.nth(index)
            if candidate.locator("[data-batch-disposition]").input_value() == "pending":
                pending_card = candidate
                break
        if pending_card is None:
            break
        content_id = pending_card.get_attribute("data-batch-content")
        old_version = int(pending_card.get_attribute("data-item-version"))
        is_blocker = pending_card.locator(".acceptance-chip--blocker").count() > 0
        warning_inputs = pending_card.locator("[data-batch-warning]")
        pending_card.locator("[data-batch-reviewed]").check()
        pending_card.locator("[data-batch-disposition]").select_option("archive" if is_blocker else "accept")
        pending_card.locator("[data-batch-note]").fill(
            "Архивировано автоматизированной приёмкой из-за blocker" if is_blocker
            else "Проверено автоматизированным UI-сценарием"
        )
        for warning_index in range(warning_inputs.count()):
            warning_inputs.nth(warning_index).fill("Индивидуальное предупреждение проверено")
        with page.expect_response(
            lambda response, item_id=content_id: response.url.endswith(
                f"/api/admin/migration/batches/{batch_id}/items/{item_id}"
            ) and response.request.method == "PATCH"
        ):
            pending_card.locator("[data-batch-item-save]").click()
        page.locator(
            f'[data-batch-content="{content_id}"][data-item-version="{old_version + 1}"]'
        ).wait_for()
        if is_blocker:
            blocker_recorded = True
        if warning_inputs.count() > 0:
            individual_warning_recorded = True

    coverage_steps.check("migration.batch-blocker-archive", blocker_recorded)
    coverage_steps.check("migration.batch-individual-warning-ack", individual_warning_recorded)
    coverage_steps.check(
        "migration.batch-all-decided",
        all(
            page.locator("[data-batch-disposition]").nth(index).input_value() != "pending"
            for index in range(page.locator("[data-batch-disposition]").count())
        ),
    )

    with page.expect_response(
        lambda response: response.url.endswith(f"/api/admin/migration/batches/{batch_id}/submit")
        and response.request.method == "POST"
    ):
        page.locator("[data-batch-submit]").click()
    page.locator(".workflow-panel__head .state-pill").filter(has_text="На утверждении").wait_for()
    coverage_steps.mark("migration.batch-submit")

    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("[data-batch-finalize]").click()
    coverage_steps.check(
        "migration.batch-finalize-dismiss",
        page.locator(".workflow-panel__head .state-pill").inner_text() == "На утверждении",
    )
    batch_ack = page.locator('[data-batch-ack="legacy_text"]')
    batch_ack.fill("Общее предупреждение партии проверено выпускающим")
    coverage_steps.check(
        "migration.batch-common-warning-ack",
        batch_ack.input_value().startswith("Общее предупреждение"),
    )
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/admin/migration/batches/{batch_id}/finalize")
        and response.request.method == "POST"
    ):
        page.locator("[data-batch-finalize]").click()
    page.locator(".workflow-panel__head .state-pill").filter(has_text="Завершена").wait_for()
    coverage_steps.mark("migration.batch-finalize-confirm")
    coverage_steps.check(
        "migration.batch-read-only",
        page.locator("[data-batch-item-save]").count() == 0
        and page.locator("[data-batch-finalize]").count() == 0
        and all(
            page.locator("[data-batch-disposition]").nth(index).is_disabled()
            for index in range(page.locator("[data-batch-disposition]").count())
        ),
    )

    with sqlite3.connect(live_cms.settings.database_path) as connection:
        rows = connection.execute(
            """SELECT c.id,c.status,c.migration_review_required,bi.disposition,
                      EXISTS(SELECT 1 FROM migration_review_issues i
                                 WHERE i.content_id=c.id AND i.id='e2e-stage7-issue-0') AS had_blocker
               FROM contents c JOIN migration_review_batch_items bi ON bi.content_id=c.id
               WHERE bi.batch_id=?""",
            (batch_id,),
        ).fetchall()
    coverage_steps.check(
        "migration.batch-atomic-result",
        len(rows) == 4
        and all(row[2] == 0 for row in rows)
        and all(
            row[1] == "archived" if row[3] == "archive" else row[1] == initial_statuses[row[0]]
            for row in rows
        ),
        str(rows),
    )


@pytest.mark.e2e
@pytest.mark.full
def test_migration_batch_cancel(page, live_cms, coverage_steps):
    login(page)
    batch = _create_pilot(page, live_cms)
    batch_id = batch["id"]
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("[data-batch-cancel]").click()
    coverage_steps.check(
        "migration.cancel-dismiss",
        page.locator(".workflow-panel__head .state-pill").inner_text() == "Черновик",
    )
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/admin/migration/batches/{batch_id}/cancel")
        and response.request.method == "POST"
    ):
        page.locator("[data-batch-cancel]").click()
    page.locator(".workflow-panel__head .state-pill").filter(has_text="Отменена").wait_for()
    coverage_steps.mark("migration.cancel-confirm")
    coverage_steps.check(
        "migration.cancel-read-only",
        page.locator("[data-batch-item-save]").count() == 0
        and page.locator("[data-batch-cancel]").count() == 0,
    )
    page.locator("[data-migration-back]").click()
    page.get_by_role("heading", name="Перенесённые материалы", exact=True).wait_for()
    with page.expect_response(lambda response: response.url.endswith(f"/api/admin/migration/batches/{batch_id}")):
        page.locator(f'[data-migration-batch="{batch_id}"]').click()
    page.locator(".workflow-panel__head .state-pill").filter(has_text="Отменена").wait_for()
    coverage_steps.mark("migration.cancel-reopen")
