from __future__ import annotations

import json
import sqlite3

import pytest

from .conftest import login


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
        },
        "editor": {
            "create": True, "submissions": False, "users": False,
            "bulk": False, "upload": True, "audit": False, "reindex": False,
        },
        "publisher": {
            "create": True, "submissions": True, "users": False,
            "bulk": True, "upload": True, "audit": True, "reindex": False,
        },
        "admin": {
            "create": True, "submissions": True, "users": True,
            "bulk": True, "upload": True, "audit": True, "reindex": True,
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


def _create_pilot(page):
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
    batch = _create_pilot(page)
    batch_id = batch["id"]
    coverage_steps.check(
        "migration.batch-pilot-create",
        batch["status"] == "draft" and len(batch["items"]) == 4,
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

    _open_panel(page, "migration", "Перенесённые материалы")
    with page.expect_response(lambda response: response.url.endswith("/api/admin/migration/batches/pilot")) as resumed:
        page.locator("[data-migration-pilot]").click()
    coverage_steps.check("migration.batch-pilot-resume", resumed.value.json()["id"] == batch_id)
    page.locator("[data-migration-back]").click()
    page.get_by_role("heading", name="Перенесённые материалы", exact=True).wait_for()
    coverage_steps.mark("migration.batch-dashboard-back")
    with page.expect_response(lambda response: response.url.endswith(f"/api/admin/migration/batches/{batch_id}")):
        page.locator(f'[data-migration-batch="{batch_id}"]').click()
    page.get_by_role("heading", name="Пилот: ключевые разделы", exact=True).wait_for()
    coverage_steps.mark("migration.batch-reopen")

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
    batch_ack = page.locator('[data-batch-ack="e2e_common_warning"]')
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
            """SELECT c.status,c.migration_review_required,
                      EXISTS(SELECT 1 FROM migration_review_issues i
                             WHERE i.content_id=c.id AND i.code='e2e_pilot_blocker') AS had_blocker
               FROM contents c JOIN migration_review_batch_items bi ON bi.content_id=c.id
               WHERE bi.batch_id=?""",
            (batch_id,),
        ).fetchall()
    coverage_steps.check(
        "migration.batch-atomic-result",
        len(rows) == 4
        and all(row[1] == 0 for row in rows)
        and all(row[0] == ("archived" if row[2] else "draft") for row in rows),
    )


@pytest.mark.e2e
@pytest.mark.full
def test_migration_batch_cancel(page, coverage_steps):
    login(page)
    batch = _create_pilot(page)
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
