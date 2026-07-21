from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from .conftest import api_create_content, api_login, login


@pytest.mark.e2e
@pytest.mark.full
def test_public_forms_and_submission_workflow(page, live_cms, coverage_steps):
    page.goto(f"{live_cms.base_url}/", wait_until="domcontentloaded")
    page.locator("[data-note-open]").last.click()
    note = page.locator("[data-note-dialog]")
    coverage_steps.check("submission.prayer.open", note.is_visible())
    note.locator('[data-note-close][aria-label="Закрыть"]').click()
    coverage_steps.check("submission.prayer.close-x", not note.is_visible())
    page.locator("[data-note-open]").last.click()
    note.get_by_role("button", name="Отмена").click()
    coverage_steps.check("submission.prayer.cancel", not note.is_visible())
    page.locator("[data-note-open]").last.click()
    note.get_by_label("Об упокоении").check()
    note.get_by_role("button", name="Далее").click()
    coverage_steps.mark("submission.prayer.type-next")
    note.get_by_role("button", name="Проверить").click()
    coverage_steps.check(
        "submission.prayer.names-required",
        "Проверьте" in note.locator("[data-note-error]").filter(visible=True).inner_text(),
    )
    note.locator('[name="names"]').fill("\n".join(f"Имя{index}" for index in range(1, 12)))
    note.get_by_role("button", name="Проверить").click()
    coverage_steps.check(
        "submission.prayer.names-limit",
        "Проверьте" in note.locator("[data-note-error]").filter(visible=True).inner_text(),
    )
    note.locator('[name="names"]').fill("Александр\nМария")
    note.get_by_role("button", name="Проверить").click()
    note.get_by_role("button", name="Назад").click()
    coverage_steps.mark("submission.prayer.back")
    note.get_by_role("button", name="Проверить").click()
    with page.expect_response(lambda response: response.url.endswith("/api/public/submissions/prayer-note")):
        note.get_by_role("button", name="Отправить").click()
    note.locator("[data-note-success]").wait_for()
    prayer_reference = note.locator("[data-note-reference]").inner_text().strip()
    coverage_steps.check("submission.prayer.send", bool(prayer_reference))
    note.locator("[data-note-success]").get_by_role("button", name="Закрыть").click()

    page.goto(f"{live_cms.base_url}/school", wait_until="domcontentloaded")
    page.locator("[data-school-open]").first.click()
    school = page.locator("[data-school-dialog]")
    coverage_steps.check("submission.school.open", school.is_visible())
    school.locator('[data-school-close][aria-label="Закрыть"]').click()
    coverage_steps.check("submission.school.close-x", not school.is_visible())
    page.locator("[data-school-open]").first.click()
    school.get_by_role("button", name="Отмена").click()
    coverage_steps.check("submission.school.cancel", not school.is_visible())
    page.locator("[data-school-open]").first.click()
    school.get_by_role("button", name="Отправить заявку").click()
    coverage_steps.check("submission.school.required", school.locator(":invalid").count() >= 5)
    school.locator('[name="parent_name"]').fill("QA Родитель")
    school.locator('[name="contact"]').fill("qa@example.test")
    school.locator('[name="child_name"]').fill("QA Ребёнок")
    school.locator('[name="child_age"]').fill("2")
    school.locator('[name="consent"]').check()
    school.get_by_role("button", name="Отправить заявку").click()
    coverage_steps.check("submission.school.age", school.locator('[name="child_age"]:invalid').count() == 1)
    school.locator('[name="child_age"]').fill("9")
    school.locator('[name="comment"]').fill("Сквозной QA-тест Playwright")
    with page.expect_response(lambda response: response.url.endswith("/api/public/submissions/school-enrollment")):
        school.get_by_role("button", name="Отправить заявку").click()
    school.locator("[data-school-success]").wait_for()
    school_reference = school.locator("[data-school-reference]").inner_text().strip()
    coverage_steps.check("submission.school.send", bool(school_reference))
    school.locator("[data-school-success]").get_by_role("button", name="Закрыть").click()

    # Make next/previous controls reachable without touching product data or rate limits.
    now = datetime.now(UTC)
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        for index in range(55):
            created = (now - timedelta(minutes=index + 1)).isoformat(timespec="seconds")
            connection.execute(
                """INSERT INTO submissions(
                     id,reference_code,submission_type,status,payload_json,ip_hash,payload_fingerprint,
                     version,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid.uuid4()), f"Z-E2E-{index:04d}", "prayer_note", "new",
                    json.dumps({"remembrance_type": "health", "names": [f"Имя {index}"]}, ensure_ascii=False),
                    f"e2e-ip-{index}", f"e2e-fingerprint-{index}", 1, created, created,
                ),
            )

    page.goto(f"{live_cms.base_url}/cms.html", wait_until="domcontentloaded")
    login(page)
    page.locator('[data-panel="submissions"]').click()
    page.get_by_role("heading", name="Заявки", exact=True).wait_for()
    coverage_steps.mark("submission.cms.open")
    search = page.locator("[data-submission-search]")
    with page.expect_response(lambda response: "/api/admin/submissions?" in response.url):
        search.fill(prayer_reference)
    prayer_row = page.locator("[data-submission-open]").filter(has_text=prayer_reference)
    prayer_row.wait_for()
    page.locator("[data-submission-type]").select_option("prayer_note")
    page.locator("[data-submission-status]").select_option("new")
    coverage_steps.check("submission.cms.filters", prayer_row.count() == 1)
    page.locator("[data-submission-type]").select_option("school_enrollment")
    page.locator("[data-submission-list]").filter(has_text="Заявки не найдены").wait_for()
    coverage_steps.mark("submission.cms.empty-filter")
    page.locator("[data-submission-type]").select_option("")
    page.locator("[data-submission-status]").select_option("")
    prayer_row.wait_for()

    prayer_row.click()
    detail = page.locator("[data-submission-dialog]")
    detail.locator('[data-submission-close][aria-label="Закрыть"]').click()
    coverage_steps.check(
        "submission.cms.close-focus",
        prayer_row.evaluate("node => document.activeElement === node"),
    )
    prayer_row.click()
    retry = detail.locator("[data-submission-retry]")
    with page.expect_response(lambda response: "/retry-notification" in response.url):
        retry.click()
    coverage_steps.mark("submission.cms.retry-notification")

    def change_status(value: str, assertion_id: str) -> None:
        with page.expect_response(lambda response: "/status" in response.url and response.request.method == "PATCH"):
            detail.locator(f'[data-submission-status-action="{value}"]').click()
        expected = {"new": "Новая", "in_progress": "В работе", "done": "Завершена", "spam": "Спам"}[value]
        detail.locator(".submission-state").filter(has_text=expected).wait_for()
        coverage_steps.mark(assertion_id)

    change_status("in_progress", "submission.cms.new-to-progress")
    change_status("new", "submission.cms.progress-to-new")
    change_status("done", "submission.cms.new-to-done")
    change_status("in_progress", "submission.cms.done-to-progress")
    change_status("spam", "submission.cms.progress-to-spam")
    change_status("in_progress", "submission.cms.spam-to-progress")
    detail.get_by_role("button", name="Закрыть").last.click()
    coverage_steps.check("submission.cms.close-button", not detail.is_visible())

    with page.expect_response(lambda response: "/api/admin/submissions?" in response.url):
        search.fill(school_reference)
    school_row = page.locator("[data-submission-open]").filter(has_text=school_reference)
    school_row.wait_for()
    school_row.click()
    pii = page.locator("[data-submission-detail]").inner_text()
    coverage_steps.check(
        "submission.cms.school-pii",
        all(value in pii for value in ("QA Родитель", "qa@example.test", "QA Ребёнок")),
    )
    detail.get_by_role("button", name="Закрыть").last.click()

    with page.expect_response(lambda response: "/api/admin/submissions?" in response.url):
        search.fill("")
    next_button = page.locator("[data-submission-next]")
    coverage_steps.check("submission.cms.pagination-enabled", not next_button.is_disabled())
    with page.expect_response(lambda response: "offset=50" in response.url):
        next_button.click()
    coverage_steps.check("submission.cms.pagination-next", page.locator("[data-submission-count]").inner_text().startswith("51"))
    with page.expect_response(lambda response: "offset=0" in response.url):
        page.locator("[data-submission-prev]").click()
    coverage_steps.check("submission.cms.pagination-prev", page.locator("[data-submission-count]").inner_text().startswith("1"))


@pytest.mark.e2e
@pytest.mark.full
def test_media_chooser_workflow(page, media_files, coverage_steps):
    login(page)
    page.locator('[data-content-type="news"]').click()
    page.locator("[data-create-current]").click()
    cover = page.locator('[data-schema-field="cover"]')
    cover.locator('[data-media-choose="field"]').click()
    dialog = page.locator("[data-media-dialog]")
    coverage_steps.check("media.chooser.open", dialog.is_visible())
    dialog.locator('[data-media-close][aria-label="Закрыть"]').click()
    coverage_steps.check("media.chooser.close-x", not dialog.is_visible())
    cover.locator('[data-media-choose="field"]').click()
    dialog.get_by_role("button", name="Отмена").click()
    coverage_steps.check("media.chooser.cancel", not dialog.is_visible())
    cover.locator('[data-media-choose="field"]').click()
    with page.expect_response(lambda response: response.url.endswith("/api/admin/media") and response.request.method == "POST"):
        dialog.locator("[data-library-upload]").set_input_files(media_files["image"])
    dialog.locator("[data-media-select]").first.wait_for()
    coverage_steps.mark("media.chooser.upload")
    dialog.locator("[data-media-search]").fill("e2e-media-primary")
    dialog.locator(".media-card").filter(has_text="e2e-media-primary.png").wait_for()
    coverage_steps.mark("media.chooser.search-found")
    dialog.locator("[data-media-search]").fill("definitely-not-present")
    dialog.locator("[data-media-grid]").filter(has_text="Файлы не найдены").wait_for()
    coverage_steps.mark("media.chooser.search-empty")
    dialog.locator("[data-media-search]").fill("")
    dialog.locator("[data-media-kind]").select_option("document")
    dialog.locator("[data-media-grid]").filter(has_text="Файлы не найдены").wait_for()
    coverage_steps.mark("media.chooser.kind-document")
    dialog.locator("[data-media-kind]").select_option("image")
    dialog.locator("[data-media-usage]").select_option("unused")
    dialog.locator("[data-media-select]").first.wait_for()
    coverage_steps.mark("media.chooser.usage-unused")
    dialog.locator("[data-media-select]").first.click()
    dialog.locator("[data-media-use]").click()
    coverage_steps.check("media.chooser.single-use", bool(cover.locator('input[type="text"]').input_value()))

    page.locator('[data-content-type="gallery"]').click()
    page.locator("[data-unsaved-dialog] [data-unsaved-discard]").click()
    page.locator("[data-create-current]").click()
    photos = page.locator('[data-schema-field="photos"]')
    photos.locator('[data-media-choose="image-list"]').click()
    coverage_steps.check("media.chooser.multiple-open", "несколько" in dialog.locator("[data-media-dialog-title]").inner_text())
    with page.expect_response(lambda response: response.url.endswith("/api/admin/media") and response.request.method == "POST"):
        dialog.locator("[data-library-upload]").set_input_files(
            [media_files["replacement"], media_files["second_image"]]
        )
    dialog.locator("[data-media-use]").click()
    coverage_steps.check("media.chooser.multiple-use", photos.locator("[data-image-id]").count() == 2)


@pytest.mark.e2e
@pytest.mark.full
def test_media_panel_lifecycle(page, live_cms, media_files, coverage_steps):
    login(page)
    page.locator('[data-panel="media"]').click()
    page.get_by_role("heading", name="Медиатека", exact=True).wait_for()
    upload = page.locator("[data-panel-media-upload]")
    with page.expect_response(lambda response: response.url.endswith("/api/admin/media") and response.request.method == "POST"):
        upload.set_input_files([media_files["image"], media_files["document"]])
    page.locator('[role="status"]').filter(has_text="добавлены").wait_for()
    coverage_steps.mark("media.panel.upload")

    search = page.locator("[data-panel-media-search]")
    with page.expect_response(lambda response: "/api/admin/media?" in response.url):
        search.fill("e2e-media-primary.png")
    card = page.locator("[data-panel-media-grid] .media-card").filter(has_text="e2e-media-primary.png")
    card.wait_for()
    coverage_steps.mark("media.panel.search")
    page.locator("[data-panel-media-kind]").select_option("document")
    page.locator("[data-panel-media-grid]").filter(has_text="Файлы не найдены").wait_for()
    coverage_steps.mark("media.panel.kind-filter")
    page.locator("[data-panel-media-kind]").select_option("image")
    page.locator("[data-panel-media-usage]").select_option("unused")
    card.wait_for()
    coverage_steps.mark("media.panel.usage-filter")

    card.locator("[data-media-open]").first.click()
    detail = page.locator("[data-media-detail-dialog]")
    detail.locator('[data-media-detail-close][aria-label="Закрыть"]').click()
    coverage_steps.check("media.panel.detail-close-x", not detail.is_visible())
    card.locator('[data-media-open][aria-label="Подробности"]').click()
    detail.get_by_role("button", name="Закрыть").last.click()
    coverage_steps.check("media.panel.detail-close-button", not detail.is_visible())
    card.locator("[data-media-open]").first.click()
    detail.locator("[data-media-alt]").fill("QA: тестовый файл медиатеки")
    with page.expect_response(lambda response: "/api/admin/media/" in response.url and response.request.method == "PATCH"):
        detail.locator("[data-media-save]").click()
    card.locator("[data-media-open]").first.click()
    coverage_steps.check("media.panel.alt-save", detail.locator("[data-media-alt]").input_value() == "QA: тестовый файл медиатеки")
    permanent_url = detail.locator("a[target='_blank']").first.get_attribute("href")
    with page.expect_popup() as popup_info:
        detail.locator("a[target='_blank']").first.click()
    media_page = popup_info.value
    media_page.wait_for_load_state("domcontentloaded")
    coverage_steps.check("media.panel.permanent-url", "/media/" in media_page.url)
    media_page.close()

    with page.expect_response(lambda response: "/replacement" in response.url and response.request.method == "POST"):
        detail.locator("[data-media-replace]").set_input_files(media_files["replacement"])
    coverage_steps.mark("media.panel.replace")
    with page.expect_response(lambda response: "/api/admin/media?" in response.url):
        search.fill("e2e-media-primary.png")
    card = page.locator("[data-panel-media-grid] .media-card").filter(has_text="e2e-media-primary.png")
    card.wait_for()
    card.locator("[data-media-open]").first.click()
    coverage_steps.check("media.panel.replace-keeps-url", detail.locator("a[target='_blank']").first.get_attribute("href") == permanent_url)
    detail.get_by_role("button", name="Закрыть").last.click()

    headers = api_login(page, live_cms)
    media_result = page.request.get(f"{live_cms.base_url}/api/admin/media?q=e2e-media-primary.png&limit=10").json()
    primary = next(item for item in media_result["items"] if item["original_name"] == "e2e-media-primary.png")
    api_create_content(
        page,
        live_cms,
        headers,
        content_type="news",
        title="E2E media usage",
        data={
            "publication_date": "2026-07-21", "category": "Новости прихода", "summary": "Медиа используется",
            "cover": primary["url"], "cover_alt": "Тестовый файл",
        },
    )
    page.reload(wait_until="domcontentloaded")
    page.locator(".cms-user strong").filter(has_text="admin").wait_for()
    page.locator('[data-panel="media"]').click()
    search = page.locator("[data-panel-media-search]")
    with page.expect_response(lambda response: "/api/admin/media?" in response.url):
        search.fill("e2e-media-primary.png")
    with page.expect_response(lambda response: "/api/admin/media?" in response.url):
        page.locator("[data-panel-media-usage]").select_option("used")
    card = page.locator("[data-panel-media-grid] .media-card").filter(has_text="e2e-media-primary.png")
    card.wait_for()
    card.locator("[data-media-open]").first.click()
    coverage_steps.check("media.panel.used-delete-protected", detail.locator("[data-media-delete]").count() == 0)
    detail.get_by_role("button", name="Закрыть").last.click()

    page.locator("[data-panel-media-usage]").select_option("unused")
    with page.expect_response(lambda response: "/api/admin/media?" in response.url):
        search.fill("e2e-media-replacement.png")
    replacement_card = page.locator("[data-panel-media-grid] .media-card").filter(has_text="e2e-media-replacement.png")
    replacement_card.wait_for()
    replacement_card.locator("[data-media-open]").first.click()
    delete = detail.locator("[data-media-delete]")
    page.once("dialog", lambda dialog: dialog.dismiss())
    delete.click()
    coverage_steps.check("media.panel.delete-cancel", detail.is_visible())
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(lambda response: response.request.method == "DELETE" and "/api/admin/media/" in response.url):
        delete.click()
    detail.wait_for(state="hidden")
    coverage_steps.mark("media.panel.delete-confirm")

    with page.expect_response(lambda response: "/api/admin/media?" in response.url):
        search.fill("")
    page.locator('[data-media-tab="issues"]').click()
    issue = page.locator("[data-panel-media-grid]").filter(has_text="e2e-missing.jpg")
    issue.wait_for()
    with page.expect_response(lambda response: "/api/admin/media-issues/e2e-missing/replacement" in response.url):
        page.locator("[data-issue-upload='e2e-missing']").set_input_files(media_files["second_image"])
    page.locator("[data-panel-media-grid]").filter(has_text="Замена загружена").wait_for()
    coverage_steps.mark("media.panel.missing-replacement")
    page.locator('[data-media-tab="files"]').click()
    coverage_steps.check("media.panel.files-tab", page.locator(".media-panel-toolbar").is_visible())

    # Add icon-only document rows to exercise real pagination without derivative requests.
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        for index in range(55):
            connection.execute(
                """INSERT INTO media(
                     id,original_name,stored_name,mime_type,size_bytes,alt_text,created_at,
                     sha256,kind,source,status,version,updated_at,metadata_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"e2e-page-media-{index}", f"e2e-page-{index:03d}.txt", f"e2e/page-{index:03d}.txt",
                    "text/plain", 10, "", now, f"e2e-sha-{index}", "document", "upload", "ready", 1, now, "{}",
                ),
            )
    search.fill("")
    with page.expect_response(lambda response: "/api/admin/media?" in response.url):
        page.locator("[data-panel-media-kind]").select_option("")
    with page.expect_response(lambda response: "/api/admin/media?" in response.url):
        page.locator("[data-panel-media-usage]").select_option("")
    next_button = page.locator("[data-panel-media-next]")
    coverage_steps.check("media.panel.pagination-enabled", not next_button.is_disabled())
    with page.expect_response(lambda response: "offset=48" in response.url):
        next_button.click()
    coverage_steps.check("media.panel.pagination-next", page.locator("[data-panel-media-count]").inner_text().startswith("49"))
    with page.expect_response(lambda response: "offset=0" in response.url):
        page.locator("[data-panel-media-prev]").click()
    coverage_steps.check("media.panel.pagination-prev", page.locator("[data-panel-media-count]").inner_text().startswith("1"))

    reindex = page.locator("[data-media-reindex]")
    with page.expect_response(lambda response: "/api/admin/media/reindex" in response.url):
        reindex.click()
    page.locator("[data-media-reindex-status]").filter(has_text="Завершено").wait_for(timeout=20_000)
    page.wait_for_function("() => !document.querySelector('[data-media-reindex]').disabled")
    coverage_steps.check("media.panel.reindex", not reindex.is_disabled())
