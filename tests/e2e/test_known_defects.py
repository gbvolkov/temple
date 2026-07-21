from __future__ import annotations

import re
import sqlite3
import subprocess
import sys

import pytest

from .conftest import ROOT, login, observe_open_defect


def test_cms_01_dirty_navigation(page, defect_registry, coverage_steps):
    login(page)
    page.locator('[data-content-type="news"]').click()
    page.locator("[data-create-current]").click()
    title = page.locator('[name="title"]')
    title.fill("UNSAVED E2E")
    page.locator('[data-content-type="page"]').click()
    dialog = page.locator("[data-unsaved-dialog]")
    dialog.wait_for(state="visible")
    preserved = title.input_value() == "UNSAVED E2E"
    coverage_steps.check("stage2.unsaved.dialog", dialog.is_visible())
    dialog.locator("[data-unsaved-stay]").click()
    coverage_steps.check("stage2.unsaved.stay", title.input_value() == "UNSAVED E2E")
    page.locator('[data-content-type="page"]').click()
    dialog.locator("[data-unsaved-discard]").click()
    page.locator('[data-content-type="page"].is-active').wait_for()
    coverage_steps.check("stage2.unsaved.discard", page.locator('[data-content-type="page"].is-active').is_visible())
    page.locator('[name="title"]').fill("SAVED BEFORE NAVIGATION")
    page.locator('[data-content-type="news"]').click()
    with page.expect_response(lambda response: response.url.endswith("/api/admin/contents") and response.request.method == "POST"):
        dialog.locator("[data-unsaved-save]").click()
    page.locator('[data-content-type="news"].is-active').wait_for()
    coverage_steps.check("stage2.unsaved.save", not dialog.is_visible())
    page.locator('[name="title"]').fill("BEFOREUNLOAD E2E")
    beforeunload_prevented = page.evaluate("() => { const event = new Event('beforeunload', {cancelable:true}); window.dispatchEvent(event); return event.defaultPrevented; }")
    coverage_steps.check("stage2.unsaved.beforeunload", beforeunload_prevented)
    observe_open_defect(defect_registry, "CMS-01", bad=not preserved, good=preserved, detail="save/discard/stay dirty navigation")


def test_cms_02_incomplete_block_preview(page, defect_registry, coverage_steps):
    login(page)
    page.locator('[data-content-type="news"]').click()
    page.locator("[data-create-current]").click()
    page.wait_for_timeout(700)
    preview_requests: list[str] = []
    page.on("request", lambda request: preview_requests.append(request.url) if "/content-preview" in request.url else None)
    previous = page.locator("[data-content-preview]").get_attribute("srcdoc")
    page.locator('[data-add-block="image"]').click()
    page.wait_for_timeout(700)
    error = page.locator("[data-block-validation]")
    good = error.is_visible() and not preview_requests and page.locator("[data-content-preview]").get_attribute("srcdoc") == previous
    coverage_steps.check("stage2.preview.inline-error", error.is_visible())
    coverage_steps.check("stage2.preview.no-request", not preview_requests)
    coverage_steps.check("stage2.preview.preserved", page.locator("[data-content-preview]").get_attribute("srcdoc") == previous)
    image = page.locator('[data-block-type="image"]').last
    image.locator('[data-block-value="image"]').evaluate("node => { node.value='/assets/school-maslenitsa.jpg'; node.dispatchEvent(new Event('input',{bubbles:true})); }")
    with page.expect_response(lambda response: "/content-preview" in response.url and response.status == 200):
        image.locator('[data-block-value="alt"]').fill("Праздник Воскресной школы")
    coverage_steps.check("stage2.preview.recovery", not image.locator("[data-block-validation]").is_visible())
    page.once("dialog", lambda dialog: dialog.accept())
    image.locator('[data-block-action="delete"]').click()
    page.wait_for_timeout(700)
    for block_type, assertion_id in (("gallery", "stage2.preview.gallery"), ("video", "stage2.preview.video"), ("file", "stage2.preview.file")):
        preview_requests.clear()
        page.locator(f'[data-add-block="{block_type}"]').click()
        page.wait_for_timeout(700)
        card = page.locator(f'[data-block-type="{block_type}"]').last
        coverage_steps.check(assertion_id, card.locator("[data-block-validation]").is_visible() and not preview_requests)
        page.once("dialog", lambda dialog: dialog.accept())
        card.locator('[data-block-action="delete"]').click()
        page.wait_for_timeout(700)
    observe_open_defect(defect_registry, "CMS-02", bad=not good, good=good, detail="inline validation without preview request")


def test_cms_03_media_chooser_state(page, defect_registry, coverage_steps):
    login(page)
    page.locator('[data-content-type="news"]').click()
    page.locator("[data-create-current]").click()
    chooser = page.locator('[data-media-choose="field"]').first
    chooser.click()
    dialog = page.locator("[data-media-dialog]")
    dialog.wait_for(state="visible")
    dialog.locator("[data-media-search]").fill("stale-query")
    dialog.locator("[data-media-kind]").select_option("document")
    dialog.locator("[data-media-usage]").select_option("used")
    page.locator("[data-media-close]").first.click()
    urls: list[str] = []
    page.on("request", lambda request: urls.append(request.url) if "/api/admin/media?" in request.url else None)
    chooser.click()
    dialog.wait_for(state="visible")
    page.locator("[data-media-count]").wait_for()
    visible_filter = dialog.locator("[data-media-usage]").input_value()
    visible_search = dialog.locator("[data-media-search]").input_value()
    visible_kind = dialog.locator("[data-media-kind]").input_value()
    request_url = urls[-1] if urls else ""
    bad = visible_filter == "" and "usage=used" in request_url
    good = visible_filter == "" and visible_search == "" and visible_kind == "image" and "usage=used" not in request_url and "stale-query" not in request_url
    coverage_steps.check("stage2.media.visible-reset", visible_filter == "" and visible_search == "" and visible_kind == "image", f"usage={visible_filter!r}; q={visible_search!r}; kind={visible_kind!r}")
    coverage_steps.check("stage2.media.request-reset", "usage=used" not in request_url and "stale-query" not in request_url and "kind=image" in request_url)
    observe_open_defect(defect_registry, "CMS-03", bad=bad, good=good, detail=request_url)


@pytest.mark.expected_http_error(401, "/api/admin/contents")
@pytest.mark.expected_http_error(401, "/api/admin/login")
def test_cms_04_revoked_session(page, live_cms, defect_registry, coverage_steps):
    login(page)
    page.locator('[data-content-type="news"]').click()
    page.locator("[data-create-current]").click()
    title = page.locator('[name="title"]')
    title.fill("RECOVERED UNSAVED E2E")
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        connection.execute("DELETE FROM sessions")
    with page.expect_response(lambda response: response.url.endswith("/api/admin/contents") and response.status == 401):
        page.locator("[data-save-draft]").first.click()
    dialog_open = page.locator("[data-login-dialog]").get_attribute("open") is not None
    coverage_steps.check("stage2.session.relogin-dialog", dialog_open)
    coverage_steps.check("stage2.session.dirty-preserved", title.input_value() == "RECOVERED UNSAVED E2E")
    login_dialog = page.locator("[data-login-dialog]")
    login_dialog.locator('[name="password"]').fill("wrong-password")
    login_dialog.get_by_role("button", name="Войти").click()
    login_dialog.locator("[data-login-error]").filter(has_text="Неверное").wait_for()
    coverage_steps.check("stage2.session.failed-login-preserved", title.input_value() == "RECOVERED UNSAVED E2E")
    login_dialog.locator('[name="password"]').fill("test-password")
    login_dialog.get_by_role("button", name="Войти").click()
    login_dialog.wait_for(state="hidden")
    coverage_steps.check("stage2.session.relogin-preserved", title.input_value() == "RECOVERED UNSAVED E2E")
    with page.expect_response(lambda response: response.url.endswith("/api/admin/contents") and response.status == 201):
        page.locator("[data-save-draft]").first.click()
    coverage_steps.check("stage2.session.save-after-relogin", "Сохранено" in page.locator("[data-save-status]").inner_text())
    good = dialog_open and title.input_value() == "RECOVERED UNSAVED E2E"
    observe_open_defect(defect_registry, "CMS-04", bad=not good, good=good, detail="dirty editor survives reauthentication")


def test_cms_05_mobile_account_controls(page, defect_registry, coverage_steps):
    login(page)
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("[data-cms-menu]").click()
    mobile = page.locator("[data-mobile-account]")
    visible = mobile.locator("[data-mobile-open-profile]").is_visible() and mobile.locator("[data-mobile-logout]").is_visible()
    coverage_steps.check("stage2.mobile.password", mobile.locator("[data-mobile-open-profile]").is_visible())
    coverage_steps.check("stage2.mobile.logout", mobile.locator("[data-mobile-logout]").is_visible())
    mobile.locator("[data-mobile-open-profile]").click()
    page.locator("[data-password-dialog]").wait_for(state="visible")
    page.locator("[data-password-close]").first.click()
    page.set_viewport_size({"width": 320, "height": 720})
    coverage_steps.check("stage2.mobile.320", mobile.locator("[data-mobile-open-profile]").is_visible() and mobile.locator("[data-mobile-logout]").is_visible())
    coverage_steps.check("stage2.mobile.no-overflow", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
    observe_open_defect(defect_registry, "CMS-05", bad=not visible, good=visible, detail="mobile account controls")


@pytest.mark.expected_http_error(409, "/migration/batches/pilot")
def test_cms_06_pilot_before_audit(page, live_cms, defect_registry, coverage_steps):
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        connection.execute("DELETE FROM migration_audit_runs")
    login(page)
    page.locator('[data-panel="migration"]').click()
    page.locator("[data-migration-pilot]").wait_for()
    disabled = page.locator("[data-migration-pilot]").is_disabled()
    reason = page.locator("[data-migration-pilot-reason]").inner_text()
    coverage_steps.check("stage2.migration.disabled", disabled)
    coverage_steps.check("stage2.migration.reason", "Сначала" in reason)
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        connection.execute(
            "INSERT INTO migration_audit_runs(id,rules_version,scope_json,status,counts_json,created_at,started_at,finished_at) VALUES ('stage2-audit','1.0.0','{}','completed','{}',datetime('now'),datetime('now'),datetime('now'))"
        )
    page.locator('[data-panel="settings"]').click()
    page.locator('[data-panel="migration"]').click()
    page.locator("[data-migration-pilot]:enabled").wait_for()
    coverage_steps.check("stage2.migration.completed-enables", not page.locator("[data-migration-pilot]").is_disabled())
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        connection.execute("DELETE FROM migration_audit_runs")
    with page.expect_response(lambda response: "/migration/batches/pilot" in response.url and response.status == 409):
        page.locator("[data-migration-pilot]").click()
    page.locator("[data-toast]").filter(has_text="аудит").wait_for()
    page.locator("[data-migration-pilot]:disabled").wait_for()
    coverage_steps.check("stage2.migration.race-handled", page.locator("[data-migration-pilot]").is_disabled())
    observe_open_defect(
        defect_registry, "CMS-06", bad=not disabled, good=disabled and "Сначала" in reason,
        detail=f"disabled={disabled}; reason={reason}",
    )


def test_cms_07_new_material_picker_state(page, defect_registry, coverage_steps):
    login(page)
    page.locator('[data-content-type="news"]').click()
    search = page.locator("[data-content-search]")
    page.locator("[data-content-select] option").nth(1).wait_for(state="attached")
    page.locator("[data-content-select]").select_option(index=1)
    search.fill("E2E")
    page.locator("[data-review-only]").check()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url) if "/api/admin/content-index?" in request.url else None)
    page.locator("[data-create-current]").click()
    page.wait_for_timeout(500)
    clean = search.input_value() == "" and not page.locator("[data-review-only]").is_checked() and page.locator("[data-content-select]").input_value() == ""
    coverage_steps.check("stage3.picker.visible-reset", clean)
    coverage_steps.check("stage3.picker.request-reset", bool(requests) and all("q=E2E" not in url and "review_required=true" not in url for url in requests[-2:]), requests[-2:] if requests else "no list request")
    observe_open_defect(defect_registry, "CMS-07", bad=not clean, good=clean, detail="new material picker")


def test_cms_08_reopen_login(page, defect_registry, coverage_steps):
    dialog = page.locator("[data-login-dialog]")
    dialog.get_by_role("button", name="Отмена").click()
    dialog.wait_for(state="hidden")
    login_button = page.locator("[data-login-open]")
    coverage_steps.check("stage3.login.entry-visible", login_button.is_visible())
    login_button.click()
    dialog.wait_for(state="visible")
    focused = dialog.locator('[name="username"]').evaluate("node => document.activeElement === node")
    coverage_steps.check("stage3.login.reopened", dialog.is_visible())
    coverage_steps.check("stage3.login.focus", focused)
    observe_open_defect(defect_registry, "CMS-08", bad=not login_button.is_visible(), good=focused, detail="persistent login entry point")


def test_cms_09_username_pattern(page, defect_registry, coverage_steps):
    login(page)
    page.locator('[data-panel="users"]').click()
    page.locator("[data-user-create]").click()
    username = page.locator('[data-user-create-dialog] [name="username"]')
    username.fill("a!b")
    valid = username.evaluate("node => node.checkValidity()")
    coverage_steps.check("stage3.username.invalid-rejected", not valid)
    username.fill("a-b")
    accepted = username.evaluate("node => node.checkValidity()")
    coverage_steps.check("stage3.username.valid-accepted", accepted)
    observe_open_defect(defect_registry, "CMS-09", bad=valid, good=not valid and accepted, detail=f"invalid={valid}; valid={accepted}")


def test_cms_10_skip_link_focus(page, defect_registry, coverage_steps):
    page.locator("[data-login-dialog]").get_by_role("button", name="Отмена").click()
    skip_link = page.locator(".skip-link")
    assert skip_link.get_attribute("href") == "#editor"
    skip_link.focus()
    page.keyboard.press("Enter")
    focused = page.evaluate("document.activeElement && document.activeElement.id === 'editor'")
    coverage_steps.check("stage3.skip.target", skip_link.get_attribute("href") == "#editor")
    coverage_steps.check("stage3.skip.focus", focused)
    observe_open_defect(defect_registry, "CMS-10", bad=not focused, good=focused, detail="active element")


def test_cms_11_issue_code_query(page, live_cms, defect_registry, coverage_steps):
    login(page)
    response = page.request.get(
        f"{live_cms.base_url}/api/admin/migration/issues?q=required_field_missing"
    )
    assert response.ok
    result = response.json()
    total = result["total"]
    coverage_steps.check("stage4.migration.code-query", total >= 1)
    coverage_steps.check("stage4.migration.code-results", {item["code"] for item in result["items"]} == {"required_field_missing"})
    observe_open_defect(defect_registry, "CMS-11", bad=total == 0, good=total >= 1, detail=f"total={total}")


def test_cms_12_refresh_race_guard(page, defect_registry, coverage_steps):
    login(page)
    page.locator('[data-panel="migration"]').click()
    query = page.locator("[data-migration-query]")
    apply = page.locator("[data-migration-filter]")
    with page.expect_request(lambda request: "/api/admin/migration/issues" in request.url and "q=e2e-slow" in request.url):
        query.fill("e2e-slow")
        apply.evaluate("node => node.dispatchEvent(new MouseEvent('click', {bubbles:true}))")
    coverage_steps.check("stage4.migration.busy", page.locator(".acceptance-panel").get_attribute("aria-busy") == "true")
    with page.expect_response(lambda response: "/api/admin/migration/issues" in response.url and "q=required_field_missing" in response.url):
        query.fill("required_field_missing")
        apply.evaluate("node => node.dispatchEvent(new MouseEvent('click', {bubbles:true}))")
    page.wait_for_function("() => !document.querySelector('.acceptance-panel').hasAttribute('aria-busy')")
    page.wait_for_timeout(650)
    count_text = page.locator("[data-acceptance-issue-count]").inner_text()
    good = count_text.startswith("1 ")
    coverage_steps.check("stage4.migration.latest-only", good, count_text)
    coverage_steps.check("stage4.migration.filter-restored", not apply.is_disabled())
    observe_open_defect(defect_registry, "CMS-12", bad=count_text.startswith("0 "), good=good, detail=count_text)


def test_cms_13_required_field_explanation(page, defect_registry, coverage_steps):
    login(page)
    page.locator('[data-content-type="news"]').click()
    page.locator("[data-create-current]").click()
    text = page.locator("[data-editor-pane]").inner_text().lower()
    coverage_steps.check("stage3.readiness.copy", "черновик можно сохранить" in text and "для отправки" in text)
    with page.expect_response(lambda response: response.url.endswith("/api/admin/contents") and response.request.method == "POST"):
        page.locator("[data-save-draft]").first.click()
    workflow_requests: list[str] = []
    page.on("request", lambda request: workflow_requests.append(request.url) if "/submit-review" in request.url else None)
    page.locator('[data-workflow-action="submit-review"]').click()
    summary = page.locator("[data-readiness-errors]")
    summary.wait_for(state="visible")
    coverage_steps.check("stage3.readiness.summary", "заполните" in summary.inner_text().lower())
    coverage_steps.check("stage3.readiness.no-request", not workflow_requests)
    coverage_steps.check("stage3.readiness.aria-invalid", page.locator('[aria-invalid="true"]').count() > 0)
    focused_invalid = page.evaluate("() => document.activeElement?.getAttribute('aria-invalid') === 'true'")
    coverage_steps.check("stage3.readiness.focus", focused_invalid)
    good = "черновик можно сохранить" in text and not workflow_requests and focused_invalid
    observe_open_defect(defect_registry, "CMS-13", bad=not good, good=good, detail="required-field copy")


def test_cms_14_settings_are_diagnostic(page, defect_registry, coverage_steps):
    login(page)
    with page.expect_response(lambda response: response.url.endswith("/api/admin/diagnostics") and response.status == 200):
        page.locator('[data-panel="settings"]').click()
    page.locator("[data-diagnostics-grid] .diagnostic-card").first.wait_for()
    text = page.locator("[data-cms-panel]").inner_text().lower()
    coverage_steps.check("stage3.diagnostics.application", "приложение" in text and "контентная схема" in text)
    coverage_steps.check("stage3.diagnostics.database", "база данных" in text and "quick check" in text)
    coverage_steps.check("stage3.diagnostics.search", "поисковый индекс" in text)
    coverage_steps.check("stage3.diagnostics.storage", "медиа-хранилище" in text)
    coverage_steps.check("stage3.diagnostics.migration", "миграция" in text)
    with page.expect_response(lambda response: response.url.endswith("/api/admin/diagnostics") and response.status == 200):
        page.locator("[data-diagnostics-refresh]").click()
    good = all(term in text for term in ("база", "медиа", "миграц"))
    coverage_steps.check("stage3.diagnostics.refresh", good)
    observe_open_defect(defect_registry, "CMS-14", bad=not good, good=good, detail="settings diagnostics")


def test_cms_15_reindex_progress(page, defect_registry, coverage_steps):
    login(page)
    page.locator('[data-panel="media"]').click()
    button = page.locator("[data-media-reindex]")
    with page.expect_response(lambda response: response.url.endswith("/api/admin/media/reindex?dry_run=false") and response.status == 202):
        button.click()
    progress = page.locator("[data-media-reindex-status]")
    progress.wait_for(state="visible")
    text = progress.inner_text().lower()
    coverage_steps.check("stage4.reindex.numeric-progress", bool(re.search(r"\d+\s*(?:%|из)", text)), text)
    coverage_steps.check("stage4.reindex.single-active", button.is_disabled())
    progress.filter(has_text="Завершено").wait_for(timeout=20_000)
    coverage_steps.check("stage4.reindex.completed", "100%" in progress.inner_text())
    page.wait_for_function("() => !document.querySelector('[data-media-reindex]').disabled")
    page.reload(wait_until="domcontentloaded")
    page.locator(".cms-user strong").filter(has_text="admin").wait_for()
    page.locator('[data-panel="media"]').click()
    page.locator("[data-media-reindex-status]").filter(has_text="Завершено").wait_for()
    coverage_steps.check("stage4.reindex.reload-result", "100%" in page.locator("[data-media-reindex-status]").inner_text())
    good = "100%" in page.locator("[data-media-reindex-status]").inner_text()
    observe_open_defect(defect_registry, "CMS-15", bad=not good, good=good, detail="reindex progress UI")


def test_cms_16_selector_limit_hint(page, defect_registry, coverage_steps):
    login(page)
    page.locator('[data-content-type="news"]').click()
    page.locator("[data-content-select] option").nth(100).wait_for(state="attached")
    options = page.locator("[data-content-select] option").count() - 1
    count_text = page.locator("[data-content-count]").inner_text()
    total_match = re.search(r"из\s+(\d+)", count_text)
    total = int(total_match.group(1)) if total_match else options
    panel_text = page.locator("[data-content-picker]").inner_text()
    coverage_steps.check("stage4.picker.first-page", options == 100 and page.locator("[data-content-next]").is_enabled())
    coverage_steps.check("stage4.picker.limit-hint", page.locator("[data-content-limit-hint]").is_visible())
    with page.expect_response(lambda response: "/api/admin/content-index?" in response.url and "offset=100" in response.url):
        page.locator("[data-content-next]").click()
    coverage_steps.check("stage4.picker.next-page", page.locator("[data-content-count]").inner_text().startswith("101–"))
    coverage_steps.check("stage4.picker.last-page", page.locator("[data-content-next]").is_disabled())
    with page.expect_response(lambda response: "/api/admin/content-index?" in response.url and "offset=0" in response.url):
        page.locator("[data-content-prev]").click()
    coverage_steps.check("stage4.picker.previous-page", page.locator("[data-content-count]").inner_text().startswith("1–100"))
    with page.expect_response(lambda response: "/api/admin/content-index?" in response.url and "q=E2E" in response.url and "offset=0" in response.url):
        page.locator("[data-content-search]").fill("E2E pagination 104")
    coverage_steps.check("stage4.picker.search-reset", page.locator("[data-content-count]").inner_text().startswith("1–1"))
    good = options == 100 and "текущая страница" in panel_text.lower() and page.locator("[data-content-next]").count() > 0
    observe_open_defect(defect_registry, "CMS-16", bad=not good, good=good, detail="selector limit explanation")


def test_cms_17_testclient_deprecation(defect_registry, coverage_steps):
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "tests/test_cms_api.py", "-q",
            "-W", "error::starlette.exceptions.StarletteDeprecationWarning",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    output = result.stdout + result.stderr
    bad = result.returncode != 0 and "StarletteDeprecationWarning" in output
    coverage_steps.check("stage4.testclient.warning-free", result.returncode == 0 and "StarletteDeprecationWarning" not in output, output[-1000:])
    observe_open_defect(defect_registry, "CMS-17", bad=bad, good=result.returncode == 0, detail=output[-1000:])
