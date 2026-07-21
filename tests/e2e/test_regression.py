from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import E2E_ROOT, login


@pytest.mark.e2e
@pytest.mark.smoke
def test_login_and_session_header(page):
    login(page)
    assert page.locator(".cms-user strong").inner_text() == "admin"
    assert page.locator("[data-save-draft]").first.is_visible()
    assert page.locator("[data-logout]").is_visible()
    assert "CMS подключена" in page.locator("[data-save-status]").inner_text()


@pytest.mark.e2e
@pytest.mark.full
def test_all_content_type_buttons(page):
    login(page)
    coverage = json.loads((E2E_ROOT / "coverage.json").read_text(encoding="utf-8"))
    for content_type in coverage["content_types"]:
        button = page.locator(f'[data-content-type="{content_type}"]')
        assert button.count() == 1, content_type
        button.click()
        assert button.get_attribute("class") and "is-active" in button.get_attribute("class")
        page.locator("[data-editor-form]").wait_for()


@pytest.mark.e2e
@pytest.mark.full
def test_all_block_controls(page):
    login(page)
    page.locator('[data-content-type="news"]').click()
    page.locator("[data-create-current]").click()
    coverage = json.loads((E2E_ROOT / "coverage.json").read_text(encoding="utf-8"))
    for block_type in coverage["block_types"]:
        button = page.locator(f'[data-add-block="{block_type}"]')
        assert button.count() == 1, block_type
        assert button.is_visible(), block_type


@pytest.mark.e2e
@pytest.mark.full
def test_editor_navigation_and_preview(page):
    login(page)
    page.locator('[data-content-type="news"]').click()
    page.locator("[data-create-current]").click()
    assert page.locator("[data-content-select]").input_value() == ""
    page.locator('[data-preview-size="mobile"]').click()
    assert "is-active" in (page.locator('[data-preview-size="mobile"]').get_attribute("class") or "")
    page.locator('[data-preview-size="desktop"]').click()
    assert "is-active" in (page.locator('[data-preview-size="desktop"]').get_attribute("class") or "")


@pytest.mark.e2e
@pytest.mark.full
def test_administration_panels(page):
    login(page)
    expected_text = {
        "workflow": "Редакционная очередь",
        "submissions": "Заявки",
        "media": "Медиатека",
        "migration": "Перенесённые материалы",
        "users": "Пользователи",
        "settings": "Контентная схема",
    }
    for panel, text in expected_text.items():
        button = page.locator(f'[data-panel="{panel}"]')
        assert button.count() == 1, panel
        button.click()
        page.locator("[data-cms-panel]").filter(has_text=text).wait_for(state="visible")


@pytest.mark.e2e
@pytest.mark.full
def test_role_contract(browser, live_cms):
    expected = {
        "viewer": {"users": False, "submissions": False},
        "editor": {"users": False, "submissions": False},
        "publisher": {"users": False, "submissions": True},
        "admin": {"users": True, "submissions": True},
    }
    for role, visibility in expected.items():
        context = browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow")
        role_page = context.new_page()
        role_page.goto(f"{live_cms.base_url}/cms.html")
        username = "admin" if role == "admin" else f"e2e_{role}"
        password = "test-password" if role == "admin" else "Strong-Password-2026!"
        login(role_page, username, password)
        assert role_page.locator('[data-panel="users"]').is_visible() is visibility["users"]
        assert role_page.locator('[data-panel="submissions"]').is_visible() is visibility["submissions"]
        context.close()


@pytest.mark.e2e
@pytest.mark.full
def test_responsive_navigation(page):
    login(page)
    for width, height in ((390, 844), (320, 720)):
        page.set_viewport_size({"width": width, "height": height})
        menu = page.locator("[data-cms-menu]")
        assert menu.is_visible()
        menu.click()
        assert page.locator("body").evaluate("node => node.classList.contains('cms-menu-open')")
        page.locator('[data-content-type="news"]').click()
        assert not page.locator("body").evaluate("node => node.classList.contains('cms-menu-open')")
        dimensions = page.evaluate("() => [innerWidth, document.documentElement.scrollWidth]")
        assert dimensions[1] <= dimensions[0]
        assert dimensions[0] == width


@pytest.mark.e2e
@pytest.mark.full
@pytest.mark.smoke
def test_draft_save_and_noop(page):
    login(page)
    page.locator('[data-content-type="news"]').click()
    page.locator("[data-create-current]").click()
    page.locator('[name="title"]').fill("E2E saved draft")
    page.locator("[data-save-draft]").first.click()
    page.locator("[data-save-status]").filter(has_text="Сохранено").wait_for()
    state = page.locator("[data-save-status]").inner_text()
    assert "v1" in state
    page.locator("[data-save-draft]").first.click()
    page.locator("[data-save-status]").filter(has_text="Сохранено").wait_for()
    assert "v1" in page.locator("[data-save-status]").inner_text()


@pytest.mark.e2e
@pytest.mark.full
def test_conditional_media_and_migration_controls(page):
    login(page)
    page.locator('[data-panel="media"]').click()
    page.locator('[data-media-tab="issues"]').click()
    page.locator("[data-panel-media-grid]").filter(has_text="e2e-missing.jpg").wait_for()
    assert page.locator("[data-issue-upload='e2e-missing']").count() == 1
    page.locator('[data-panel="migration"]').click()
    page.locator("[data-migration-query]").fill("required_field_missing")
    page.locator("[data-migration-filter]").click()
    count = page.locator("[data-acceptance-issue-count]")
    count.wait_for()
    assert "проблем" in count.inner_text()


@pytest.mark.e2e
@pytest.mark.full
@pytest.mark.smoke
def test_public_navigation_and_logout(page, live_cms):
    response = page.goto(f"{live_cms.base_url}/", wait_until="domcontentloaded")
    assert response and response.ok
    page.goto(f"{live_cms.base_url}/cms.html", wait_until="domcontentloaded")
    login(page)
    page.locator("[data-logout]").click()
    page.locator("[data-login-dialog]").wait_for(state="visible")
    login(page)


@pytest.mark.e2e
@pytest.mark.full
def test_material_lifecycle(page, live_cms):
    auth = page.request.post(
        f"{live_cms.base_url}/api/admin/login",
        data={"username": "admin", "password": "test-password"},
    )
    assert auth.ok
    headers = {"X-CSRF-Token": auth.json()["csrf_token"]}
    created_response = page.request.post(
        f"{live_cms.base_url}/api/admin/contents",
        headers=headers,
        data={
            "content_type": "news",
            "title": "E2E lifecycle",
            "data": {
                "publication_date": "2026-07-21",
                "category": "Новости прихода",
                "summary": "Сквозная проверка жизненного цикла.",
                "cover": "assets/school-maslenitsa.jpg",
                "cover_alt": "Праздник Воскресной школы"
            },
        },
    )
    assert created_response.status == 201
    item = created_response.json()
    assert item["status"] == "draft"

    def transition(action: str, current: dict) -> dict:
        response = page.request.post(
            f"{live_cms.base_url}/api/admin/contents/{current['id']}/{action}",
            headers=headers,
            data={"version": current["version"]},
        )
        assert response.ok, response.text()
        return response.json()

    item = transition("submit-review", item)
    assert item["status"] == "in_review"
    item = transition("publish", item)
    assert item["status"] == "published"
    public_url = f"{live_cms.base_url}/news/{item['published_slug']}"
    public = page.request.get(public_url)
    assert public.ok and "E2E lifecycle" in public.text()
    item = transition("archive", item)
    assert item["status"] == "archived"
    assert page.request.get(public_url).status == 404
    item = transition("restore", item)
    assert item["status"] == "draft"
    item = transition("trash", item)
    assert item["status"] == "trash"
    item = transition("restore", item)
    assert item["status"] == "draft"


def test_coverage_contract():
    coverage = json.loads((E2E_ROOT / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["format_version"] == 3
    assertions = [assertion for suite in coverage["suites"] for assertion in suite["assertions"]]
    assert assertions
    assert len(assertions) == len(set(assertions))
    assert len(coverage["content_types"]) == len(set(coverage["content_types"])) == 10
    assert len(coverage["block_types"]) == len(set(coverage["block_types"])) == 9
    assert set(coverage["roles"]) == {"admin", "publisher", "editor", "viewer"}
    assert coverage["viewports"] == ["1440x1000", "390x844", "320x720"]
    assert len(coverage["profiles"]["smoke"]) == len(set(coverage["profiles"]["smoke"])) == 3
    assert coverage["known_gaps"] == []
    gap_ids = [gap["id"] for gap in coverage["known_gaps"]]
    assert len(gap_ids) == len(set(gap_ids))
    assert all(gap["report_sections"] and gap["required"] for gap in coverage["known_gaps"])
    for suite in coverage["suites"]:
        relative_path, function_name = suite["nodeid"].split("::", 1)
        source = (E2E_ROOT.parents[1] / relative_path).read_text(encoding="utf-8")
        assert f"def {function_name}(" in source, suite
        if suite.get("runtime"):
            assert suite.get("report_sections"), suite
            assert "coverage_steps" in source, suite
