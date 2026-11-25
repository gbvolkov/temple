from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from .conftest import login


pytestmark = pytest.mark.mobile_safari
MENU_OPEN = re.compile(r"\bcms-menu-open\b")
PANELS = ("migration", "workflow", "submissions", "media", "users", "settings")


def open_menu(page):
    page.locator("[data-cms-menu]").tap()
    expect(page.locator("body")).to_have_class(MENU_OPEN)
    expect(page.locator(".cms-sidebar")).to_have_css("transform", "none")


def expect_menu_closed(page):
    expect(page.locator("body")).not_to_have_class(MENU_OPEN)
    # Wait for the CSS slide transition too: the selected page must be unobstructed.
    expect(page.locator(".cms-sidebar")).not_to_be_in_viewport()


def test_cms_74_mobile_draft_site_entry(page, live_cms, coverage_steps, artifacts_dir):
    for role in ("admin", "viewer", "editor", "publisher"):
        for width, height in ((390, 844), (320, 720)):
            page.context.clear_cookies()
            page.set_viewport_size({"width": width, "height": height})
            page.goto(live_cms.base_url + "/cms.html")
            login(page, "admin" if role == "admin" else f"e2e_{role}",
                  "test-password" if role == "admin" else "Strong-Password-2026!")
            open_menu(page)
            entry = page.get_by_role("link", name="Сайт с черновиками", exact=True)
            expect(entry).to_be_visible()
            expect(entry).to_be_in_viewport(ratio=1)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if role == "admin":
                page.screenshot(path=artifacts_dir / f"mobile-preview-menu-{width}.png")
            tabs_before = len(page.context.pages)
            entry.tap()
            page.wait_for_url("**/preview?preview=drafts")
            expect(page.locator(".draft-preview-banner")).to_be_visible()
            expect(page.locator(".site-header")).to_be_visible()
            assert len(page.context.pages) == tabs_before
            coverage_steps.mark(f"mobile-preview.entry.{role}.{width}")
            page.get_by_role("link", name="В CMS", exact=True).tap()
            page.wait_for_url("**/cms.html")
            expect_menu_closed(page)
    page.set_viewport_size({"width": 1440, "height": 1000})
    expect(page.locator("[data-site-draft-preview]")).to_be_visible()
    expect(page.locator("[data-mobile-site-draft-preview]")).to_be_hidden()
    coverage_steps.mark("mobile-preview.desktop-entry")


def test_mobile_draft_site_entry_preserves_unsaved_decisions(page, live_cms, coverage_steps):
    login(page)
    title = page.locator('[name="title"]')
    title.fill("Mobile preview saved marker")
    open_menu(page)
    entry = page.get_by_role("link", name="Сайт с черновиками", exact=True)
    entry.tap()
    dialog = page.locator("[data-unsaved-dialog]")
    expect(dialog).to_be_visible()
    dialog.locator("[data-unsaved-stay]").tap()
    expect(title).to_have_value("Mobile preview saved marker")
    expect(page.locator("body")).to_have_class(MENU_OPEN)
    assert page.url.endswith("/cms.html")
    coverage_steps.mark("mobile-preview.unsaved-stay")

    entry.tap()
    dialog.locator("[data-unsaved-save]").tap()
    page.wait_for_url("**/preview?preview=drafts")
    page.goto(live_cms.base_url + "/news?preview=drafts")
    expect(page.locator("main")).to_contain_text("Mobile preview saved marker")
    coverage_steps.mark("mobile-preview.unsaved-save")

    page.get_by_role("link", name="В CMS", exact=True).tap()
    title.fill("Mobile preview discarded marker")
    open_menu(page)
    entry.tap()
    dialog.locator("[data-unsaved-discard]").tap()
    page.wait_for_url("**/preview?preview=drafts")
    page.goto(live_cms.base_url + "/news?preview=drafts")
    expect(page.locator("main")).not_to_contain_text("Mobile preview discarded marker")
    coverage_steps.mark("mobile-preview.unsaved-discard")


def test_mobile_draft_site_entry_requires_session(page, coverage_steps):
    entry = page.locator("[data-mobile-site-draft-preview]")
    expect(entry).to_have_count(1)
    expect(entry).to_be_hidden()
    coverage_steps.mark("mobile-preview.anonymous-hidden")
    login(page)
    # Let the initial editor preview finish before ending its session.
    expect(page.locator("[data-content-preview]")).to_have_attribute("srcdoc", re.compile(r".+"))
    open_menu(page)
    expect(entry).to_be_visible()
    page.locator("[data-mobile-logout]").tap()
    expect(page.locator("[data-login-dialog]")).to_be_visible()
    expect(entry).to_be_hidden()
    expect(page.locator("[data-site-draft-preview]")).to_be_hidden()
    coverage_steps.mark("mobile-preview.logout-hidden")


def test_cms_72_mobile_menu_closes_after_navigation(page, coverage_steps):
    login(page)
    for width in (390, 320):
        page.set_viewport_size({"width": width, "height": 844})
        for name in PANELS:
            open_menu(page)
            button = page.locator(f'[data-panel="{name}"]')
            # Tapping the icon exercises delegated events from a nested element.
            button.locator("span").tap()
            expect(button).to_have_class(re.compile(r"\bis-active\b"))
            expect(page.locator("[data-cms-panel]")).to_be_visible()
            expect_menu_closed(page)
        coverage_steps.mark(f"mobile-menu.panels.{width}")
        types = page.locator("[data-content-nav] [data-content-type]").evaluate_all("buttons => buttons.map(button => button.dataset.contentType)")
        for name in types:
            open_menu(page)
            page.locator(f'[data-content-nav] [data-content-type="{name}"]').tap()
            expect(page.locator("[data-editor-pane]")).to_be_visible()
            expect_menu_closed(page)
        coverage_steps.mark(f"mobile-menu.content-types.{width}")
    # Desktop navigation must retain its persistent sidebar.
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator('[data-panel="settings"]').click()
    expect(page.locator("[data-cms-panel]")).to_be_visible()
    expect(page.locator(".cms-sidebar")).to_be_in_viewport()
    coverage_steps.mark("mobile-menu.desktop-sidebar")


def test_mobile_menu_preserves_unsaved_navigation_decision(page, coverage_steps):
    login(page)
    title = page.locator('[name="title"]')
    title.fill("Mobile unsaved work")
    open_menu(page)
    page.locator('[data-panel="migration"]').tap()
    dialog = page.locator("[data-unsaved-dialog]")
    expect(dialog).to_be_visible()
    dialog.locator("[data-unsaved-stay]").tap()
    expect(title).to_have_value("Mobile unsaved work")
    expect(page.locator("body")).to_have_class(MENU_OPEN)
    expect(page.locator("[data-editor-pane]")).to_be_visible()
    coverage_steps.mark("mobile-menu.unsaved-stay")

    page.locator('[data-panel="migration"]').tap()
    dialog.locator("[data-unsaved-discard]").tap()
    expect(page.locator('[data-panel="migration"]')).to_have_class(re.compile(r"\bis-active\b"))
    expect_menu_closed(page)
    coverage_steps.mark("mobile-menu.unsaved-discard")

    open_menu(page)
    page.locator('[data-content-nav] [data-content-type="news"]').tap()
    title.fill("Mobile saved before navigation")
    open_menu(page)
    page.locator('[data-panel="settings"]').tap()
    with page.expect_response(lambda response: response.url.endswith("/api/admin/contents") and response.request.method == "POST") as saved:
        dialog.locator("[data-unsaved-save]").tap()
    assert saved.value.status == 201
    assert saved.value.json()["title"] == "Mobile saved before navigation"
    expect(page.locator('[data-panel="settings"]')).to_have_class(re.compile(r"\bis-active\b"))
    expect_menu_closed(page)
    coverage_steps.mark("mobile-menu.unsaved-save")


def test_mobile_menu_navigation_respects_roles(page, coverage_steps):
    for role in ("viewer", "editor", "publisher"):
        login(page, f"e2e_{role}", "Strong-Password-2026!")
        open_menu(page)
        expect(page.locator('[data-panel="users"]')).to_be_hidden()
        if role == "publisher":
            expect(page.locator('[data-panel="submissions"]')).to_be_visible()
        else:
            expect(page.locator('[data-panel="submissions"]')).to_be_hidden()
        page.locator('[data-panel="migration"]').tap()
        expect(page.locator('[data-panel="migration"]')).to_have_class(re.compile(r"\bis-active\b"))
        expect_menu_closed(page)
        open_menu(page)
        page.locator('[data-content-nav] [data-content-type="page"]').tap()
        expect_menu_closed(page)
        if role == "viewer":
            expect(page.locator('[name="title"]')).to_be_disabled()
        else:
            expect(page.locator('[name="title"]')).to_be_enabled()
        coverage_steps.mark(f"mobile-menu.roles.{role}")
        open_menu(page)
        page.locator("[data-mobile-logout]").tap()
        expect(page.locator("[data-login-dialog]")).to_be_visible()
        page.reload()
