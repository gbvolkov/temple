from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from .conftest import api_create_content, api_login, login, open_content


VALID_NEWS = {
    "publication_date": "2026-07-21",
    "category": "Новости прихода",
    "summary": "Полный UI-регрессионный сценарий Playwright.",
    "cover": "assets/school-maslenitsa.jpg",
    "cover_alt": "Праздник Воскресной школы",
}


@pytest.mark.e2e
@pytest.mark.full
def test_editor_composite_fields(page, media_files, coverage_steps):
    login(page)

    page.locator('[data-content-type="news"]').click()
    page.locator("[data-create-current]").click()
    page.locator('[name="title"]').fill("E2E composite fields")
    page.locator('[name="publication_date"]').fill("2026-07-21")
    page.locator('[name="category"]').fill("Новости прихода")
    page.locator('[name="summary"]').fill("Проверка обычных полей редактора")
    page.locator('[name="featured"]').check()
    coverage_steps.check(
        "editor.fields.basic-dirty",
        "несохранённые" in page.locator("[data-workflow-note]").inner_text().lower(),
    )

    relation = page.locator('[data-schema-field="related_content"]')
    relation.locator("[data-relation-search]").fill("E2E page")
    result = relation.locator("[data-relation-add]").first
    result.wait_for()
    result.click()
    coverage_steps.check("editor.relation.add", relation.locator(".relation-chip").count() == 1)
    relation.locator("[data-relation-remove]").click()
    coverage_steps.check("editor.relation.remove", relation.locator(".relation-chip").count() == 0)

    page.locator('[data-content-type="parish_section"]').click()
    page.locator("[data-unsaved-dialog] [data-unsaved-discard]").click()
    page.locator("[data-create-current]").click()
    schedule = page.locator('[data-schema-field="schedule"]')
    schedule.locator("[data-schedule-add]").click()
    row = schedule.locator("[data-schedule-row]")
    row.locator('[data-schedule-value="weekday"]').select_option("7")
    row.locator('[data-schedule-value="time"]').fill("10:00")
    row.locator('[data-schedule-value="title"]').fill("Литургия")
    coverage_steps.check("editor.schedule.add", row.count() == 1)
    row.locator("[data-schedule-remove]").click()
    coverage_steps.check("editor.schedule.remove", schedule.locator("[data-schedule-row]").count() == 0)

    page.locator('[data-content-type="gallery"]').click()
    page.locator("[data-unsaved-dialog] [data-unsaved-discard]").click()
    page.locator("[data-create-current]").click()
    image_list = page.locator('[data-schema-field="photos"]')
    with page.expect_response(lambda response: response.request.method == "POST" and "/api/admin/media" in response.url):
        image_list.locator("[data-image-list-upload]").set_input_files(
            [media_files["image"], media_files["second_image"]]
        )
    cards = image_list.locator("[data-image-id]")
    cards.nth(1).wait_for()
    coverage_steps.check("editor.image-list.upload", cards.count() == 2)
    original_first = cards.nth(0).get_attribute("data-image-id")
    cards.nth(1).get_by_role("button", name="Выше").click()
    coverage_steps.check(
        "editor.image-list.up",
        cards.nth(1).get_attribute("data-image-id") == original_first,
    )
    cards.nth(0).get_by_role("button", name="Ниже").click()
    coverage_steps.check(
        "editor.image-list.down",
        cards.nth(0).get_attribute("data-image-id") == original_first,
    )
    page.once("dialog", lambda dialog: dialog.dismiss())
    cards.nth(1).get_by_role("button", name="Удалить").click()
    coverage_steps.check("editor.image-list.delete-cancel", cards.count() == 2)
    page.once("dialog", lambda dialog: dialog.accept())
    cards.nth(1).get_by_role("button", name="Удалить").click()
    coverage_steps.check("editor.image-list.delete-confirm", cards.count() == 1)


@pytest.mark.e2e
@pytest.mark.full
def test_block_editor_mutations(page, live_cms, coverage_steps):
    headers = api_login(page, live_cms)
    legacy = api_create_content(
        page,
        live_cms,
        headers,
        content_type="page",
        title="E2E legacy conversion",
        data={"body": ["Первый старый абзац\n\nВторой старый абзац"]},
    )
    assert legacy["id"]
    legacy_data = {
        "body": [
            {
                "id": "e2e-legacy-block",
                "type": "legacy_text",
                "data": {"text": "Первый старый абзац\n\nВторой старый абзац"},
            }
        ]
    }
    with sqlite3.connect(live_cms.settings.database_path) as connection:
        connection.execute(
            "UPDATE contents SET data_json=? WHERE id=?",
            (json.dumps(legacy_data, ensure_ascii=False), legacy["id"]),
        )
    login(page)
    page.locator('[data-content-type="news"]').click()
    page.locator("[data-create-current]").click()

    def add(block_type: str):
        page.locator(f'[data-add-block="{block_type}"]').click()
        card = page.locator(f'[data-block-type="{block_type}"]').last
        card.wait_for()
        coverage_steps.mark(f"editor.block.add-{block_type}")
        return card

    paragraph = add("paragraph")
    paragraph.locator("[data-inline-editor]").fill("Абзац для форматирования")
    heading = add("heading")
    heading.locator("[data-inline-editor]").fill("Заголовок блока")
    listing = add("list")
    listing.locator("[data-inline-editor]").fill("Первый пункт")
    image = add("image")
    image.locator('[data-block-value="image"]').evaluate(
        "(node) => { node.value='/assets/school-maslenitsa.jpg'; node.dispatchEvent(new Event('input',{bubbles:true})); }"
    )
    image.locator('[data-block-value="alt"]').fill("Праздник Воскресной школы")
    gallery = add("gallery")
    gallery.locator("[data-block-gallery]").evaluate(
        """node => { node.innerHTML = `<article class="image-item" data-image-id="e2e-gallery"><div></div><div><input data-image-value="image" value="/assets/school-maslenitsa.jpg"><input data-image-value="alt" value="QA"><input data-image-value="caption" value=""></div></article>`; node.dispatchEvent(new Event('input',{bubbles:true})); }"""
    )
    quote = add("quote")
    quote.locator("[data-inline-editor]").fill("Текст цитаты")
    video = add("video")
    video.locator('[data-block-value="url"]').fill("https://example.org/video")
    file_block = add("file")
    file_block.locator('[data-block-value="url"]').evaluate(
        "(node) => { node.value='/media/e2e-document.txt'; node.dispatchEvent(new Event('input',{bubbles:true})); }"
    )
    file_block.locator('[data-block-value="label"]').fill("Документ")
    callout = add("callout")
    callout.locator('[data-block-value="title"]').fill("Важно")
    callout.locator("[data-inline-editor]").fill("Текст плашки")

    cards = page.locator("[data-block-list] > [data-block-id]")
    first_id = cards.nth(0).get_attribute("data-block-id")
    cards.nth(0).get_by_role("button", name="Переместить вниз").click()
    coverage_steps.check("editor.block.move-down", cards.nth(1).get_attribute("data-block-id") == first_id)
    cards.nth(1).get_by_role("button", name="Переместить вверх").click()
    coverage_steps.check("editor.block.move-up", cards.nth(0).get_attribute("data-block-id") == first_id)
    count_before_copy = cards.count()
    cards.nth(0).get_by_role("button", name="Копировать").click()
    coverage_steps.check("editor.block.copy", cards.count() == count_before_copy + 1)
    copied = cards.nth(1)
    page.once("dialog", lambda dialog: dialog.dismiss())
    copied.get_by_role("button", name="Удалить").click()
    coverage_steps.check("editor.block.delete-cancel", cards.count() == count_before_copy + 1)
    page.once("dialog", lambda dialog: dialog.accept())
    copied.get_by_role("button", name="Удалить").click()
    coverage_steps.check("editor.block.delete-confirm", cards.count() == count_before_copy)

    listing.locator('[data-block-value="style"]').select_option("numbered")
    coverage_steps.check("editor.list.numbered", listing.locator('[data-block-value="style"]').input_value() == "numbered")
    listing.locator("[data-list-add]").click()
    coverage_steps.check("editor.list.add-item", listing.locator("[data-list-item]").count() == 2)
    listing.locator("[data-list-item]").last.get_by_role("button", name="Удалить пункт").click()
    coverage_steps.check("editor.list.remove-item", listing.locator("[data-list-item]").count() == 1)

    inline = paragraph.locator("[data-inline-editor]")
    inline.click()
    page.keyboard.press("Control+A")
    paragraph.get_by_role("button", name="Жирный").click()
    coverage_steps.check("editor.inline.bold", bool(inline.locator("b,strong").count()))
    inline.click()
    page.keyboard.press("Control+A")
    paragraph.get_by_role("button", name="Курсив").click()
    coverage_steps.check("editor.inline.italic", bool(inline.locator("i,em").count()))
    inline.click()
    page.keyboard.press("Control+A")
    paragraph.get_by_role("button", name="Добавить ссылку").click()
    link_dialog = page.locator("[data-link-dialog]")
    link_dialog.locator('[name="href"]').fill("http://unsafe.example")
    link_dialog.get_by_role("button", name="Применить").click()
    coverage_steps.check("editor.inline.link-reject-http", link_dialog.is_visible())
    link_dialog.locator('[name="href"]').fill("/about")
    link_dialog.get_by_role("button", name="Применить").click()
    coverage_steps.check("editor.inline.link-apply", inline.locator('a[href="/about"]').count() == 1)
    inline.evaluate(
        "node => { node.textContent='Текст для очистки форматирования'; node.dispatchEvent(new Event('input',{bubbles:true})); }"
    )
    inline.evaluate(
        "node => { const range=document.createRange(); range.selectNodeContents(node); const selection=getSelection(); selection.removeAllRanges(); selection.addRange(range); }"
    )
    paragraph.get_by_role("button", name="Жирный").click()
    inline.evaluate(
        "node => { const range=document.createRange(); range.selectNodeContents(node); const selection=getSelection(); selection.removeAllRanges(); selection.addRange(range); }"
    )
    paragraph.get_by_role("button", name="Очистить форматирование").click()
    remaining_format_text = "".join(inline.locator("b,strong,i,em,a").all_text_contents()).strip()
    coverage_steps.check("editor.inline.clear", remaining_format_text == "", inline.inner_html())

    open_content(page, "page", "E2E legacy conversion")
    legacy_card = page.locator('[data-block-type="legacy_text"]')
    legacy_card.wait_for()
    page.once("dialog", lambda dialog: dialog.dismiss())
    legacy_card.locator("[data-convert-legacy]").click()
    coverage_steps.check("editor.legacy.convert-cancel", legacy_card.count() == 1)
    page.once("dialog", lambda dialog: dialog.accept())
    legacy_card.locator("[data-convert-legacy]").click()
    coverage_steps.check(
        "editor.legacy.convert-confirm",
        page.locator('[data-block-type="legacy_text"]').count() == 0
        and page.locator('[data-block-type="paragraph"]').count() == 2,
    )


@pytest.mark.e2e
@pytest.mark.full
@pytest.mark.expected_http_error(422, "/schedule")
def test_ui_material_workflow(page, live_cms, coverage_steps):
    headers = api_login(page, live_cms)
    item = api_create_content(
        page,
        live_cms,
        headers,
        content_type="news",
        title="E2E UI lifecycle",
        data=VALID_NEWS,
    )
    login(page)
    open_content(page, "news", "E2E UI lifecycle")
    state = page.locator("[data-workflow-state]")
    coverage_steps.check("workflow.initial-draft", state.inner_text().startswith("Черновик"))

    page.locator('[data-workflow-action="submit-review"]').click()
    state.filter(has_text="На проверке").wait_for()
    coverage_steps.mark("workflow.submit-review")
    page.locator('[data-workflow-action="return-to-draft"]').click()
    state.filter(has_text="Черновик").wait_for()
    coverage_steps.mark("workflow.return-to-draft")
    page.locator('[data-workflow-action="submit-review"]').click()
    state.filter(has_text="На проверке").wait_for()

    page.locator('[data-workflow-action="schedule"]').click()
    schedule_dialog = page.locator("[data-schedule-dialog]")
    schedule_dialog.locator('[data-schedule-close][aria-label="Закрыть"]').click()
    coverage_steps.check("workflow.schedule-close", not schedule_dialog.is_visible() and state.inner_text().startswith("На проверке"))
    page.locator('[data-workflow-action="schedule"]').click()
    schedule_dialog.get_by_role("button", name="Отмена").click()
    coverage_steps.check("workflow.schedule-cancel", not schedule_dialog.is_visible() and state.inner_text().startswith("На проверке"))
    page.locator('[data-workflow-action="schedule"]').click()
    schedule_dialog.locator('[name="scheduled_at"]').fill("2020-01-01T10:00")
    schedule_dialog.get_by_role("button", name="Запланировать").click()
    page.locator('[role="status"]').filter(has_text="будущем").wait_for()
    coverage_steps.check("workflow.schedule-past-rejected", state.inner_text().startswith("На проверке"))
    schedule_dialog.locator('[name="scheduled_at"]').fill("2099-12-31T23:00")
    schedule_dialog.get_by_role("button", name="Запланировать").click()
    state.filter(has_text="Запланирован").wait_for()
    coverage_steps.mark("workflow.schedule-future")
    page.locator('[data-workflow-action="return-to-draft"]').click()
    state.filter(has_text="Черновик").wait_for()
    coverage_steps.mark("workflow.schedule-cancel-action")

    page.locator('[data-workflow-action="submit-review"]').click()
    state.filter(has_text="На проверке").wait_for()
    page.locator('[data-workflow-action="publish"]').click()
    publish = page.locator("[data-publish-dialog]")
    publish.locator('input[type="checkbox"]').last.uncheck()
    publish.locator("[data-publish-confirm]").click()
    coverage_steps.check("workflow.publish-incomplete", publish.is_visible() and state.inner_text().startswith("На проверке"))
    checks = publish.locator('input[type="checkbox"]')
    for index in range(checks.count()):
        checks.nth(index).check()
    publish.locator("[data-publish-confirm]").click()
    state.filter(has_text="На сайте").wait_for()
    coverage_steps.mark("workflow.publish-complete")

    record = page.request.get(f"{live_cms.base_url}/api/admin/contents/{item['id']}").json()
    public_url = f"{live_cms.base_url}/news/{record['published_slug']}"
    public = page.request.get(public_url)
    coverage_steps.check("workflow.public-visible", public.ok and "E2E UI lifecycle" in public.text())

    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('[data-workflow-action="archive"]').click()
    coverage_steps.check("workflow.archive-cancel", state.inner_text().startswith("На сайте"))
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('[data-workflow-action="archive"]').click()
    state.filter(has_text="В архиве").wait_for()
    coverage_steps.mark("workflow.archive-confirm")
    page.locator('[data-workflow-action="restore"]').click()
    state.filter(has_text="Черновик").wait_for()
    coverage_steps.mark("workflow.restore-archive")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('[data-workflow-action="trash"]').click()
    coverage_steps.check("workflow.trash-cancel", state.inner_text().startswith("Черновик"))
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('[data-workflow-action="trash"]').click()
    state.filter(has_text="В корзине").wait_for()
    coverage_steps.mark("workflow.trash-confirm")
    page.locator('[data-workflow-action="restore"]').click()
    state.filter(has_text="Черновик").wait_for()
    coverage_steps.mark("workflow.restore-trash")

    page.locator('[data-workflow-action="history"]').click()
    history = page.locator("[data-history-dialog]")
    history.locator("[data-revision-list] .history-item").first.wait_for()
    coverage_steps.check("workflow.history-revisions", history.locator("[data-revision-list] .history-item").count() >= 1)
    coverage_steps.check("workflow.history-audit", history.locator("[data-audit-list] .history-item").count() >= 1)
    history.locator('[data-history-close][aria-label="Закрыть"]').click()
    coverage_steps.check("workflow.history-close", not history.is_visible())


@pytest.mark.e2e
@pytest.mark.full
def test_bulk_workflow(page, live_cms, coverage_steps):
    headers = api_login(page, live_cms)
    item = api_create_content(
        page,
        live_cms,
        headers,
        content_type="news",
        title="E2E bulk lifecycle",
        data=VALID_NEWS,
    )
    review = page.request.post(
        f"{live_cms.base_url}/api/admin/contents/{item['id']}/submit-review",
        headers=headers,
        data={"version": item["version"]},
    )
    assert review.ok, review.text()
    login(page)
    page.locator('[data-panel="workflow"]').click()
    search = page.locator("[data-bulk-search]")
    with page.expect_response(lambda response: "/api/admin/content-index?" in response.url):
        search.fill("E2E bulk lifecycle")
    row = page.locator("[data-bulk-open]").filter(has_text="E2E bulk lifecycle")
    row.wait_for()
    coverage_steps.mark("bulk.search")
    row.click()
    page.locator("[data-editor-title]").filter(has_text="E2E bulk lifecycle").wait_for()
    coverage_steps.mark("bulk.open")

    page.locator('[data-panel="workflow"]').click()
    with page.expect_response(lambda response: "/api/admin/content-index?" in response.url):
        page.locator("[data-bulk-search]").fill("E2E bulk lifecycle")
    with page.expect_response(lambda response: "/api/admin/content-index?" in response.url):
        page.locator("[data-bulk-select-all]").click()
    page.wait_for_function("() => !document.querySelector('[data-bulk-apply]').disabled")
    coverage_steps.check("bulk.select-page", page.locator("[data-bulk-check]:checked").count() == 1)
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("[data-bulk-apply]").click()
    coverage_steps.check("bulk.publish-cancel", page.locator("[data-bulk-check]:checked").count() == 1)
    # Re-render the panel so the cancelled async handler is fully settled before the confirm branch.
    with page.expect_response(lambda response: "/api/admin/content-index?" in response.url):
        page.locator('[data-panel="workflow"]').click()
    with page.expect_response(lambda response: "/api/admin/content-index?" in response.url):
        page.locator("[data-bulk-select-all]").click()
    page.wait_for_function("() => !document.querySelector('[data-bulk-apply]').disabled")
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(lambda response: "/api/admin/content-bulk" in response.url and response.request.method == "POST"):
        page.locator("[data-bulk-apply]").click()
    page.locator('[role="status"]').filter(has_text="1").wait_for()
    coverage_steps.mark("bulk.publish-confirm")

    page.locator('[data-bulk-action="archive"]').click()
    with page.expect_response(lambda response: "/api/admin/content-index?" in response.url):
        page.locator("[data-bulk-search]").fill("E2E bulk lifecycle")
    with page.expect_response(lambda response: "/api/admin/content-index?" in response.url):
        page.locator("[data-bulk-select-all]").click()
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(lambda response: "/api/admin/content-bulk" in response.url and response.request.method == "POST"):
        page.locator("[data-bulk-apply]").click()
    page.locator('[role="status"]').filter(has_text="1").wait_for()
    coverage_steps.mark("bulk.archive-confirm")
