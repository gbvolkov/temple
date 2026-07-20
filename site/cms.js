(() => {
  "use strict";

  const editorForm = document.querySelector("[data-editor-form]");
  const previewFrame = document.querySelector("[data-content-preview]");
  const panel = document.querySelector("[data-cms-panel]");
  const roleLevel = { viewer: 0, editor: 1, publisher: 2, admin: 3 };
  const roleLabels = { viewer: "Наблюдатель", editor: "Редактор", publisher: "Выпускающий", admin: "Администратор" };
  const statusLabels = { draft: "Черновик", in_review: "На проверке", scheduled: "Запланирован", published: "Опубликован", archived: "В архиве", trash: "В корзине" };
  const auditLabels = { create: "Материал создан", update: "Содержимое сохранено", import_create: "Материал импортирован", import_update: "Импортированный материал обновлён", migration_review: "Импортированный материал проверен", migration_accept: "Принят после миграции", migration_archive: "Архивирован при приёмке", migration_trash: "Перемещён в корзину при приёмке", submit_review: "Отправлен на проверку", return_to_draft: "Возвращён в черновики", publish: "Опубликован", schedule: "Публикация запланирована", scheduled_publish: "Опубликован по расписанию", archive: "Перемещён в архив", trash: "Перемещён в корзину", restore: "Восстановлен как черновик", restore_revision: "Восстановлена историческая версия" };
  const userEventLabels = { login: "Вход в CMS", logout: "Выход из CMS", password_change: "Пароль изменён", user_create: "Пользователь создан", user_update: "Роль или состояние изменены", sessions_terminated: "Сессии завершены" };
  const state = { schema: null, currentType: "news", current: null, list: [], user: null, csrf: "", dirty: false, previewTimer: null, previewAbort: null, previewSize: "desktop", linkRange: null, linkEditor: null, media: { offset: 0, total: 0, q: "", kind: "", usage: "", selected: new Set(), items: new Map(), chooser: null, panelTab: "files" }, bulk: { action: "publish", q: "", offset: 0, total: 0, items: [], selected: new Set() }, migration: { issuesOffset: 0, currentBatch: null }, submissions: { offset: 0, total: 0, q: "", type: "", status: "", items: [], current: null }, users: [] };

  const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  const uuid = () => globalThis.crypto?.randomUUID?.() || `block-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
  const can = role => Boolean(state.user) && roleLevel[state.user.role] >= roleLevel[role];
  const definition = () => state.schema.content_types[state.currentType];

  function updateSessionUi() {
    const user = state.user;
    document.querySelector(".cms-user strong").textContent = user?.username || "Не выполнен вход";
    document.querySelector(".cms-user small").textContent = user ? roleLabels[user.role] || user.role : "";
    document.querySelector(".cms-user__avatar").textContent = (user?.username || "?").slice(0, 1).toUpperCase();
    document.querySelectorAll("[data-admin-only]").forEach(element => { element.hidden = !can("admin"); });
    document.querySelectorAll("[data-publisher-only]").forEach(element => { element.hidden = !can("publisher"); });
    document.querySelectorAll("[data-open-profile],[data-logout]").forEach(element => { element.hidden = !user; });
  }

  async function apiRequest(path, options = {}, responseType = "json") {
    const headers = { ...(options.headers || {}) };
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    if (state.csrf && !["GET", "HEAD"].includes(options.method || "GET")) headers["X-CSRF-Token"] = state.csrf;
    const response = await fetch(path, { credentials: "same-origin", ...options, headers });
    if (responseType === "text") {
      const text = await response.text();
      if (!response.ok) throw new Error(text || `Ошибка CMS (${response.status})`);
      return text;
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = body.detail;
      const message = typeof detail === "string" ? detail : detail?.message || `Ошибка CMS (${response.status})`;
      const fields = detail?.fields?.length ? `: ${detail.fields.join(", ")}` : "";
      throw new Error(message + fields);
    }
    return body;
  }

  function toast(message) {
    const element = document.querySelector("[data-toast]");
    element.textContent = message;
    element.classList.add("is-visible");
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => element.classList.remove("is-visible"), 3000);
  }

  function formatDate(value, withTime = true) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString("ru-RU", withTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" });
  }

  function inputDateTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return String(value).slice(0, 16);
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 16);
  }

  function renderNavigation() {
    const nav = document.querySelector("[data-content-nav]");
    nav.innerHTML = '<div class="cms-sidebar__label">Содержание</div>' + state.schema.ui.content_order.map(type => {
      const item = state.schema.content_types[type];
      return `<button class="cms-type${type === state.currentType ? " is-active" : ""}" type="button" data-content-type="${escapeHtml(type)}"><span>${escapeHtml(state.schema.ui.icons[type] || "•")}</span>${escapeHtml(item.label)}</button>`;
    }).join("");
  }

  function markDirty(wrapper = null) {
    if (wrapper) wrapper._dirty = true;
    state.dirty = true;
    document.querySelector("[data-save-status]").textContent = "Есть несохранённые изменения";
    renderWorkflow();
    schedulePreview();
  }

  function fieldValue(record, name, field) {
    if (name === "title") return record?.title ?? field.default ?? "";
    const data = record?.data || {};
    if (Object.prototype.hasOwnProperty.call(data, name)) return clone(data[name]);
    if (field.type === "blocks" && data.body_text) return undefined;
    return clone(field.default ?? (["boolean", "checkbox"].includes(field.type) ? false : ["relation_list", "relation-list", "image_list", "schedule", "social_links"].includes(field.type) ? [] : ""));
  }

  function fieldShell(name, field, body) {
    const required = field.required ? " <span aria-hidden=\"true\">*</span>" : "";
    const help = field.help ? `<small class="field-help">${escapeHtml(field.help)}</small>` : "";
    return `<div class="field schema-field" data-schema-field="${escapeHtml(name)}" data-field-type="${escapeHtml(field.type)}"><span class="field-label">${escapeHtml(field.label)}${required}</span>${body}${help}</div>`;
  }

  function optionMarkup(option, selected) {
    const value = typeof option === "object" ? option.value : option;
    const label = typeof option === "object" ? option.label : option;
    return `<option value="${escapeHtml(value)}"${String(value) === String(selected) ? " selected" : ""}>${escapeHtml(label)}</option>`;
  }

  const fieldRenderers = {
    string: (name, field, value) => fieldShell(name, field, `<input name="${escapeHtml(name)}" type="text" value="${escapeHtml(value)}"${field.required ? " required" : ""}>`),
    phone: (name, field, value) => fieldShell(name, field, `<input name="${escapeHtml(name)}" type="tel" value="${escapeHtml(value)}"${field.required ? " required" : ""}>`),
    email: (name, field, value) => fieldShell(name, field, `<input name="${escapeHtml(name)}" type="email" value="${escapeHtml(value)}"${field.required ? " required" : ""}>`),
    url: (name, field, value) => fieldShell(name, field, `<input name="${escapeHtml(name)}" type="url" value="${escapeHtml(value)}"${field.required ? " required" : ""}>`),
    text: (name, field, value) => fieldShell(name, field, `<textarea name="${escapeHtml(name)}" rows="5"${field.required ? " required" : ""}>${escapeHtml(value)}</textarea>`),
    integer: (name, field, value) => fieldShell(name, field, `<input name="${escapeHtml(name)}" type="number" value="${escapeHtml(value)}"${field.required ? " required" : ""}>`),
    number: (name, field, value) => fieldShell(name, field, `<input name="${escapeHtml(name)}" type="number" step="${escapeHtml(field.step || "any")}" value="${escapeHtml(value)}"${field.required ? " required" : ""}>`),
    date: (name, field, value) => fieldShell(name, field, `<input name="${escapeHtml(name)}" type="date" value="${escapeHtml(String(value || "").slice(0, 10))}"${field.required ? " required" : ""}>`),
    datetime: (name, field, value) => fieldShell(name, field, `<input name="${escapeHtml(name)}" type="datetime-local" value="${escapeHtml(inputDateTime(value))}"${field.required ? " required" : ""}>`),
    boolean: (name, field, value) => `<label class="choice schema-field" data-schema-field="${escapeHtml(name)}" data-field-type="boolean"><input name="${escapeHtml(name)}" type="checkbox"${value ? " checked" : ""}><span>${escapeHtml(field.label)}</span></label>`,
    checkbox: (name, field, value) => `<label class="choice schema-field" data-schema-field="${escapeHtml(name)}" data-field-type="checkbox"><input name="${escapeHtml(name)}" type="checkbox"${value ? " checked" : ""}><span>${escapeHtml(field.label)}</span></label>`,
    enum: (name, field, value) => fieldShell(name, field, `<select name="${escapeHtml(name)}">${(field.values || []).map(option => optionMarkup(option, value)).join("")}</select>`),
    combobox: (name, field, value) => {
      const listId = `list-${name}-${Math.random().toString(16).slice(2)}`;
      return fieldShell(name, field, `<input name="${escapeHtml(name)}" list="${listId}" value="${escapeHtml(value)}"><datalist id="${listId}">${(field.values || []).map(option => optionMarkup(option, "")).join("")}</datalist>`);
    },
    image: (name, field, value) => mediaField(name, field, value, "image", ".jpg,.jpeg,.png,.webp"),
    file: (name, field, value) => mediaField(name, field, value, "document", ".pdf,.docx,.xlsx,.pptx,.csv,.txt,.doc,.xls,.ppt"),
    media: (name, field, value) => mediaField(name, field, value, "", field.accept || ".jpg,.jpeg,.png,.webp,.mp4,.pdf,.docx,.xlsx,.pptx,.csv,.txt,.doc,.xls,.ppt"),
    schedule: (name, field, value) => fieldShell(name, field, `<div class="schedule-editor" data-schedule-editor><div class="schedule-editor__rows" data-schedule-rows></div><button class="button button--ghost button--compact" type="button" data-schedule-add>Добавить строку</button></div>`),
    blocks: (name, field) => fieldShell(name, field, `<div class="block-editor" data-block-editor><div class="block-list" data-block-list></div><div class="block-palette" data-block-palette></div></div>`),
    relation_list: (name, field) => relationField(name, field, false),
    "relation-list": (name, field) => relationField(name, field, false),
    relation: (name, field) => relationField(name, field, true),
    image_list: (name, field) => fieldShell(name, field, `<div class="image-list-editor" data-image-list><div class="image-list-editor__items" data-image-list-items></div><div class="media-field__actions"><button class="button button--ghost button--compact" type="button" data-media-choose="image-list">Выбрать из медиатеки</button><label class="button button--ghost button--compact">Загрузить фотографии<input class="cms-file-input" type="file" accept=".jpg,.jpeg,.png,.webp" multiple data-image-list-upload></label></div></div>`),
    social_links: (name, field, value) => fieldShell(name, field, `<textarea name="${escapeHtml(name)}" rows="5" placeholder="Одна HTTPS-ссылка на строку">${escapeHtml((value || []).map(item => item.url || item).join("\n"))}</textarea>`),
  };

  function mediaField(name, field, value, kind, accept) {
    return fieldShell(name, field, `<div class="media-field"><input name="${escapeHtml(name)}" type="text" value="${escapeHtml(value || "")}" readonly><div class="media-field__actions"><button class="button button--ghost button--compact" type="button" data-media-choose="field" data-media-kind="${escapeHtml(kind)}">Выбрать</button><label class="button button--ghost button--compact">Загрузить<input class="cms-file-input" type="file" accept="${escapeHtml(accept)}" data-media-upload></label></div></div>`);
  }

  function relationField(name, field, single) {
    return fieldShell(name, field, `<div class="relation-editor" data-relation-editor data-single="${single ? "true" : "false"}" data-targets="${escapeHtml((field.targets || []).join(","))}"><div class="relation-editor__selected" data-relation-selected></div><div class="relation-editor__search"><input type="search" placeholder="Найти материал" data-relation-search><div class="relation-editor__results" data-relation-results></div></div></div>`);
  }

  function normalizeRuns(value) {
    if (typeof value === "string") return value ? [{ text: value, marks: [] }] : [];
    return Array.isArray(value) ? value.map(run => ({ text: String(run?.text || ""), marks: Array.isArray(run?.marks) ? run.marks.filter(mark => ["bold", "italic"].includes(mark)) : [], ...(run?.href ? { href: run.href } : {}) })) : [];
  }

  function runsHtml(runs) {
    return normalizeRuns(runs).map(run => {
      let content = escapeHtml(run.text).replace(/\n/g, "<br>");
      if (run.marks.includes("italic")) content = `<em>${content}</em>`;
      if (run.marks.includes("bold")) content = `<strong>${content}</strong>`;
      if (run.href) content = `<a href="${escapeHtml(run.href)}">${content}</a>`;
      return content;
    }).join("");
  }

  function inlineEditor(runs, label = "Текст") {
    return `<div class="inline-field"><div class="inline-toolbar" role="toolbar" aria-label="Форматирование"><button type="button" data-inline-command="bold" aria-label="Жирный"><b>Ж</b></button><button type="button" data-inline-command="italic" aria-label="Курсив"><i>К</i></button><button type="button" data-inline-command="link" aria-label="Добавить ссылку">↗</button><button type="button" data-inline-command="removeFormat" aria-label="Очистить форматирование">×</button></div><div class="inline-editor" contenteditable="true" role="textbox" aria-multiline="true" aria-label="${escapeHtml(label)}" data-inline-editor>${runsHtml(runs)}</div></div>`;
  }

  function blockForEditor(raw) {
    if (typeof raw === "string") return { id: uuid(), type: "legacy_text", data: { text: raw } };
    const block = raw && typeof raw === "object" ? clone(raw) : {};
    const type = block.type || "paragraph";
    const data = block.data && typeof block.data === "object" ? block.data : {};
    if (type === "legacy_text") return { id: block.id || uuid(), type, data: { text: block.text || data.text || "" } };
    if (type === "paragraph") return { id: block.id || uuid(), type, data: { runs: normalizeRuns(data.runs ?? data.text ?? block.text ?? "") } };
    if (type === "heading") return { id: block.id || uuid(), type, data: { level: Number(data.level || 2), runs: normalizeRuns(data.runs ?? data.text ?? block.text ?? "") } };
    if (type === "list") return { id: block.id || uuid(), type, data: { style: data.style || "bulleted", items: (data.items || []).map(item => ({ runs: normalizeRuns(item?.runs ?? item) })) } };
    if (type === "image") return { id: block.id || uuid(), type, data: { image: data.image || data.url || "", alt: data.alt || "", caption: data.caption || "" } };
    if (type === "gallery") return { id: block.id || uuid(), type, data: { items: normalizeImages(data.items || []) } };
    if (type === "quote") return { id: block.id || uuid(), type, data: { runs: normalizeRuns(data.runs ?? data.text ?? ""), author: data.author || "", source: data.source || "" } };
    if (type === "video") return { id: block.id || uuid(), type, data: { url: data.url || "", caption: data.caption || "" } };
    if (type === "file") return { id: block.id || uuid(), type, data: { url: data.url || "", label: data.label || "", description: data.description || "" } };
    if (type === "callout") return { id: block.id || uuid(), type, data: { tone: data.tone || "info", title: data.title || "", runs: normalizeRuns(data.runs ?? data.text ?? "") } };
    return { id: block.id || uuid(), type: "legacy_text", data: { text: JSON.stringify(raw, null, 2) } };
  }

  function emptyBlock(type) {
    const base = { id: uuid(), type, data: {} };
    if (["paragraph", "heading", "quote", "callout"].includes(type)) base.data.runs = [];
    if (type === "heading") base.data.level = 2;
    if (type === "list") base.data = { style: "bulleted", items: [{ runs: [] }] };
    if (type === "image") base.data = { image: "", alt: "", caption: "" };
    if (type === "gallery") base.data = { items: [] };
    if (type === "quote") base.data = { runs: [], author: "", source: "" };
    if (type === "video") base.data = { url: "", caption: "" };
    if (type === "file") base.data = { url: "", label: "", description: "" };
    if (type === "callout") base.data = { tone: "info", title: "", runs: [] };
    return base;
  }

  function blockCard(block) {
    const blockInfo = state.schema.ui.block_types[block.type] || { label: block.type, icon: "•" };
    let body = "";
    if (block.type === "legacy_text") body = `<div class="legacy-block"><p>Импортированный текст сохранён без изменений.</p><textarea readonly rows="12">${escapeHtml(block.data.text || "")}</textarea><button class="button button--primary button--compact" type="button" data-convert-legacy>Преобразовать в абзацы</button></div>`;
    else if (block.type === "paragraph") body = inlineEditor(block.data.runs, "Абзац");
    else if (block.type === "heading") body = `<label class="compact-field">Уровень<select data-block-value="level"><option value="2"${block.data.level === 2 ? " selected" : ""}>H2</option><option value="3"${block.data.level === 3 ? " selected" : ""}>H3</option></select></label>${inlineEditor(block.data.runs, "Заголовок")}`;
    else if (block.type === "list") body = `<label class="compact-field">Вид<select data-block-value="style"><option value="bulleted"${block.data.style === "bulleted" ? " selected" : ""}>Маркированный</option><option value="numbered"${block.data.style === "numbered" ? " selected" : ""}>Нумерованный</option></select></label><div data-list-items>${block.data.items.map(listItemMarkup).join("")}</div><button class="button button--ghost button--compact" type="button" data-list-add>Добавить пункт</button>`;
    else if (block.type === "image") body = mediaBlockMarkup(block.data, "image");
    else if (block.type === "gallery") body = `<div class="block-gallery" data-block-gallery>${imageCards(block.data.items)}</div><div class="media-field__actions"><button class="button button--ghost button--compact" type="button" data-media-choose="block-gallery">Выбрать из медиатеки</button><label class="button button--ghost button--compact">Загрузить фотографии<input class="cms-file-input" type="file" accept=".jpg,.jpeg,.png,.webp" multiple data-block-upload="gallery"></label></div>`;
    else if (block.type === "quote") body = `${inlineEditor(block.data.runs, "Текст цитаты")}<div class="field-row"><label class="field">Автор<input data-block-value="author" value="${escapeHtml(block.data.author)}"></label><label class="field">Источник<input data-block-value="source" value="${escapeHtml(block.data.source)}"></label></div>`;
    else if (block.type === "video") body = `<label class="field">HTTPS-ссылка<input type="url" data-block-value="url" value="${escapeHtml(block.data.url)}"></label><label class="field">Подпись<input data-block-value="caption" value="${escapeHtml(block.data.caption)}"></label>`;
    else if (block.type === "file") body = `${mediaBlockMarkup(block.data, "file")}<label class="field">Название файла<input data-block-value="label" value="${escapeHtml(block.data.label)}"></label><label class="field">Описание<textarea data-block-value="description">${escapeHtml(block.data.description)}</textarea></label>`;
    else if (block.type === "callout") body = `<label class="compact-field">Вид<select data-block-value="tone"><option value="info"${block.data.tone === "info" ? " selected" : ""}>Информация</option><option value="important"${block.data.tone === "important" ? " selected" : ""}>Важно</option></select></label><label class="field">Заголовок<input data-block-value="title" value="${escapeHtml(block.data.title)}"></label>${inlineEditor(block.data.runs, "Текст плашки")}`;
    return `<article class="block-card" draggable="true" data-block-id="${escapeHtml(block.id)}" data-block-type="${escapeHtml(block.type)}"><header class="block-card__head"><span class="block-drag" aria-hidden="true">⋮⋮</span><strong><span>${escapeHtml(blockInfo.icon)}</span>${escapeHtml(blockInfo.label)}</strong><div class="block-actions"><button type="button" data-block-action="up" aria-label="Переместить вверх">↑</button><button type="button" data-block-action="down" aria-label="Переместить вниз">↓</button><button type="button" data-block-action="copy" aria-label="Копировать">⧉</button><button type="button" data-block-action="delete" aria-label="Удалить">×</button></div></header><div class="block-card__body">${body}</div></article>`;
  }

  function listItemMarkup(item) {
    return `<div class="list-item" data-list-item>${inlineEditor(item.runs, "Пункт списка")}<button type="button" data-list-remove aria-label="Удалить пункт">×</button></div>`;
  }

  function mediaBlockMarkup(data, kind) {
    const key = kind === "image" ? "image" : "url";
    const accept = kind === "image" ? ".jpg,.jpeg,.png,.webp" : ".pdf,.docx,.xlsx,.pptx,.csv,.txt,.doc,.xls,.ppt,.mp4";
    const mediaKind = kind === "image" ? "image" : "document";
    return `<div class="media-field"><input type="text" data-block-value="${key}" value="${escapeHtml(data[key] || "")}" readonly><div class="media-field__actions"><button class="button button--ghost button--compact" type="button" data-media-choose="block-${kind}" data-media-kind="${mediaKind}">Выбрать</button><label class="button button--ghost button--compact">Загрузить<input class="cms-file-input" type="file" accept="${accept}" data-block-upload="${kind}"></label></div></div>${kind === "image" ? `<label class="field">Alt-текст<input data-block-value="alt" value="${escapeHtml(data.alt || "")}"></label><label class="field">Подпись<input data-block-value="caption" value="${escapeHtml(data.caption || "")}"></label>` : ""}`;
  }

  function normalizeImages(images) {
    return Array.isArray(images) ? images.map((item, index) => ({ id: item.id || uuid(), image: item.image || item.url || "", alt: item.alt || "", caption: item.caption || "", order: index + 1 })) : [];
  }

  function imageCards(images) {
    return normalizeImages(images).map(item => `<article class="image-item" data-image-id="${escapeHtml(item.id)}"><div class="image-item__preview">${item.image ? `<img src="${escapeHtml(item.image.startsWith("assets/") ? `/${item.image}` : item.image)}" alt="">` : ""}</div><div><label class="field">Файл<input data-image-value="image" value="${escapeHtml(item.image)}" readonly></label><label class="field">Alt-текст<input data-image-value="alt" value="${escapeHtml(item.alt)}"></label><label class="field">Подпись<input data-image-value="caption" value="${escapeHtml(item.caption)}"></label></div><div class="image-item__actions"><button type="button" data-image-action="up" aria-label="Выше">↑</button><button type="button" data-image-action="down" aria-label="Ниже">↓</button><button type="button" data-image-action="delete" aria-label="Удалить">×</button></div></article>`).join("");
  }

  function initializeFields(record) {
    document.querySelectorAll("[data-schema-field]").forEach(wrapper => {
      const name = wrapper.dataset.schemaField;
      const field = definition().fields[name];
      const value = fieldValue(record, name, field);
      wrapper._originalValue = clone(value);
      wrapper._dirty = !record;
      if (field.type === "schedule") renderSchedule(wrapper, value || []);
      if (field.type === "blocks") {
        let blocks = value;
        if (blocks === undefined && record?.data?.body_text) blocks = [{ type: "legacy_text", text: record.data.body_text }];
        renderBlocks(wrapper, Array.isArray(blocks) ? blocks : blocks ? [blocks] : []);
      }
      if (field.type === "image_list") renderImageList(wrapper, value || []);
      if (["relation", "relation_list", "relation-list"].includes(field.type)) initializeRelation(wrapper, value || [], record);
    });
  }

  function renderBlocks(wrapper, blocks) {
    const editor = wrapper.querySelector("[data-block-editor]");
    editor.querySelector("[data-block-list]").innerHTML = blocks.map(block => blockCard(blockForEditor(block))).join("");
    const allowed = definition().fields[wrapper.dataset.schemaField].allowed || Object.keys(state.schema.ui.block_types);
    editor.querySelector("[data-block-palette]").innerHTML = `<span>Добавить блок</span>${allowed.map(type => { const info = state.schema.ui.block_types[type]; return `<button type="button" data-add-block="${type}"><b>${escapeHtml(info.icon)}</b>${escapeHtml(info.label)}</button>`; }).join("")}`;
  }

  function renderImageList(wrapper, items) {
    wrapper.querySelector("[data-image-list-items]").innerHTML = imageCards(items);
  }

  function renderSchedule(wrapper, rows) {
    wrapper.querySelector("[data-schedule-rows]").innerHTML = (Array.isArray(rows) ? rows : []).map(row => `<div class="schedule-editor__row" data-schedule-row><label>День<select data-schedule-value="weekday"><option value="">По дате</option>${["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"].map((label, index) => `<option value="${index + 1}"${String(row.weekday || "") === String(index + 1) ? " selected" : ""}>${label}</option>`).join("")}</select></label><label>Время<input type="time" data-schedule-value="time" value="${escapeHtml(row.time || "")}"></label><label>Название<input data-schedule-value="title" value="${escapeHtml(row.title || "")}"></label><label>Примечание<input data-schedule-value="note" value="${escapeHtml(row.note || "")}"></label><button class="icon-button" type="button" data-schedule-remove aria-label="Удалить строку">×</button></div>`).join("");
  }

  function relationIds(value, record) {
    const values = Array.isArray(value) ? [...value] : value ? [value] : [];
    if (!values.length && record?.data?.related_section) values.push(record.data.related_section);
    return [...new Set(values.filter(Boolean))];
  }

  async function initializeRelation(wrapper, value, record) {
    const editor = wrapper.querySelector("[data-relation-editor]");
    editor._selected = relationIds(value, record);
    editor._labels = new Map();
    await Promise.all(editor._selected.map(async id => {
      try { const item = await apiRequest(`/api/admin/contents/${encodeURIComponent(id)}`); editor._labels.set(id, item); } catch (_) { editor._labels.set(id, { id, title: id, content_type: "" }); }
    }));
    renderRelationSelected(editor);
  }

  function renderRelationSelected(editor) {
    editor.querySelector("[data-relation-selected]").innerHTML = editor._selected.length ? editor._selected.map(id => { const item = editor._labels.get(id) || { title: id, content_type: "" }; return `<span class="relation-chip"><small>${escapeHtml(state.schema.content_types[item.content_type]?.label || item.content_type || "Материал")}</small>${escapeHtml(item.title)}<button type="button" data-relation-remove="${escapeHtml(id)}" aria-label="Удалить связь">×</button></span>`; }).join("") : '<span class="relation-empty">Связи не выбраны</span>';
  }

  async function searchRelations(editor) {
    const query = editor.querySelector("[data-relation-search]").value.trim();
    const params = new URLSearchParams({ types: editor.dataset.targets, q: query, limit: "20" });
    if (state.current?.id) params.set("exclude_id", state.current.id);
    const result = await apiRequest(`/api/admin/content-options?${params}`);
    editor.querySelector("[data-relation-results]").innerHTML = result.items.filter(item => !editor._selected.includes(item.id)).map(item => `<button type="button" data-relation-add="${escapeHtml(item.id)}"><small>${escapeHtml(state.schema.content_types[item.content_type]?.label || item.content_type)} · ${escapeHtml(statusLabels[item.status] || item.status)}</small>${escapeHtml(item.title)}</button>`).join("") || '<span>Ничего не найдено</span>';
    result.items.forEach(item => editor._labels.set(item.id, item));
  }

  function renderEditor(type = state.currentType, record = state.current) {
    state.currentType = type;
    const item = definition();
    renderNavigation();
    document.querySelector("[data-mobile-title]").textContent = item.label;
    document.querySelector("[data-editor-title]").textContent = record?.title || item.title;
    document.querySelector("[data-editor-help]").textContent = item.help || "";
    document.querySelector(".editor-head .eyebrow").textContent = record ? "Редактирование материала" : "Новый материал";
    document.querySelector(".editor-head__status span").textContent = record ? statusLabels[record.status] || record.status : "Черновик";
    document.querySelector("[data-migration-warning]")?.remove();

    const groups = new Map();
    for (const [name, field] of Object.entries(item.fields)) {
      const group = field.group || "main";
      if (!groups.has(group)) groups.set(group, []);
      const value = fieldValue(record, name, field);
      const renderer = fieldRenderers[field.type] || fieldRenderers.string;
      groups.get(group).push(renderer(name, field, value));
    }
    editorForm.innerHTML = [...groups.entries()].map(([group, fields]) => `<section class="field-card"><h2>${escapeHtml(state.schema.ui.groups[group] || group)}</h2><div class="schema-fields">${fields.join("")}</div></section>`).join("") + `<div class="form-footer"><span class="field-help">Сохранение создаёт новую рабочую версию только при изменении содержимого.</span><button class="button button--primary" type="button" data-save-draft>Сохранить</button></div>`;
    initializeFields(record);
    state.dirty = !record;
    renderMigrationWarning(record);
    renderWorkflow();
    updateEditableState();
    schedulePreview();
  }

  function renderMigrationWarning(record) {
    if (!record?.migration_review_required) return;
    const legacyLink = record.legacy_url ? `<a class="text-link" href="https://www.sv-innokenty.ru${escapeHtml(record.legacy_url)}" target="_blank" rel="noopener">Сравнить со старой страницей ↗</a>` : "";
    const button = can("editor") ? '<button class="button button--primary button--compact" type="button" data-audit-current>Повторить автоматический аудит</button>' : "";
    document.querySelector(".editor-head").insertAdjacentHTML("afterend", `<div class="migration-warning" data-migration-warning><strong>Черновик перенесён со старого сайта</strong><span>Флаг снимается только при атомарной финализации редакционной партии. Сохранение материала само по себе не означает приёмку.</span><div class="migration-warning__actions">${button}${legacyLink}</div></div>`);
  }

  function workflowState(record) {
    if (!record) return { state: "Новый материал", note: "Сохраните материал, чтобы начать согласование." };
    if (record.is_public && record.has_unpublished_changes) return { state: `На сайте v${record.published_version} · редактируется v${record.version}`, note: "Посетители видят прежнюю опубликованную версию." };
    if (record.is_public) return { state: `На сайте v${record.published_version}`, note: "Рабочая и опубликованная версии совпадают." };
    return { state: `${statusLabels[record.status] || record.status} v${record.version}`, note: record.status === "scheduled" ? `Автопубликация: ${formatDate(record.scheduled_at)}.` : "Материал пока не виден посетителям." };
  }

  function workflowButton(label, action, variant = "ghost") { return `<button class="button button--${variant} button--compact" type="button" data-workflow-action="${action}">${label}</button>`; }

  function renderWorkflow() {
    const card = document.querySelector("[data-workflow-card]");
    card.hidden = false;
    const info = workflowState(state.current);
    document.querySelector("[data-workflow-state]").textContent = info.state;
    document.querySelector("[data-workflow-note]").textContent = state.dirty ? "Есть несохранённые изменения. Публичные действия временно недоступны." : info.note;
    const actions = [];
    if (state.current) actions.push(workflowButton("История", "history"));
    if (state.current?.status === "draft" && !state.current.migration_review_required && can("editor")) actions.push(workflowButton("Отправить на проверку", "submit-review", "primary"));
    if (state.current?.status === "in_review" && can("publisher")) { actions.push(workflowButton("Опубликовать", "publish", "primary")); actions.push(workflowButton("Запланировать", "schedule")); actions.push(workflowButton("Вернуть редактору", "return-to-draft")); }
    if (state.current?.status === "scheduled" && can("publisher")) actions.push(workflowButton("Отменить расписание", "return-to-draft"));
    if (state.current && ["draft", "in_review", "scheduled", "published"].includes(state.current.status) && can("publisher")) { actions.push(workflowButton("В архив", "archive")); actions.push(workflowButton("В корзину", "trash", "danger")); }
    if (state.current?.status === "archived" && can("publisher")) { actions.push(workflowButton("Восстановить", "restore", "primary")); actions.push(workflowButton("В корзину", "trash", "danger")); }
    if (state.current?.status === "trash" && can("publisher")) actions.push(workflowButton("Восстановить", "restore", "primary"));
    if (!can("editor")) actions.push('<span class="read-only-note">Режим просмотра</span>');
    document.querySelector("[data-workflow-actions]").innerHTML = actions.join("");
    if (state.dirty) document.querySelectorAll('[data-workflow-action]:not([data-workflow-action="history"]):not([data-workflow-action="submit-review"])').forEach(button => { button.disabled = true; });
  }

  function updateEditableState() {
    const editable = can("editor") && (!state.current || ["draft", "in_review", "scheduled", "published"].includes(state.current.status));
    editorForm.querySelectorAll("input,textarea,select,button,[contenteditable]").forEach(element => {
      if (element.matches("[contenteditable]")) element.contentEditable = editable ? "true" : "false";
      else element.disabled = !editable;
    });
    document.querySelectorAll("[data-save-draft]").forEach(button => { button.hidden = !editable; button.disabled = !editable; });
    document.querySelector("[data-create-current]").disabled = !can("editor");
  }

  function inlineRuns(editor) {
    const runs = [];
    function walk(node, marks = [], href = "") {
      if (node.nodeType === Node.TEXT_NODE) {
        if (node.textContent) runs.push({ text: node.textContent, marks: [...new Set(marks)], ...(href ? { href } : {}) });
        return;
      }
      if (node.nodeName === "BR") { runs.push({ text: "\n", marks: [...new Set(marks)], ...(href ? { href } : {}) }); return; }
      const nextMarks = [...marks];
      if (["STRONG", "B"].includes(node.nodeName)) nextMarks.push("bold");
      if (["EM", "I"].includes(node.nodeName)) nextMarks.push("italic");
      const nextHref = node.nodeName === "A" ? node.getAttribute("href") || "" : href;
      node.childNodes.forEach(child => walk(child, nextMarks, nextHref));
    }
    editor.childNodes.forEach(node => walk(node));
    return runs.reduce((merged, run) => {
      const previous = merged.at(-1);
      if (previous && JSON.stringify(previous.marks) === JSON.stringify(run.marks) && (previous.href || "") === (run.href || "")) previous.text += run.text;
      else merged.push(run);
      return merged;
    }, []);
  }

  function serializeBlock(card) {
    const type = card.dataset.blockType;
    const data = {};
    card.querySelectorAll("[data-block-value]").forEach(input => data[input.dataset.blockValue] = input.value);
    const inline = [...card.querySelectorAll("[data-inline-editor]")];
    if (["paragraph", "heading", "quote", "callout"].includes(type)) data.runs = inlineRuns(inline[0]);
    if (type === "heading") data.level = Number(data.level || 2);
    if (type === "list") data.items = [...card.querySelectorAll("[data-list-item]")].map(item => ({ runs: inlineRuns(item.querySelector("[data-inline-editor]")) }));
    if (type === "gallery") data.items = serializeImages(card.querySelector("[data-block-gallery]"));
    if (type === "legacy_text") data.text = card.querySelector("textarea").value;
    return { id: card.dataset.blockId, type, data };
  }

  function serializeImages(container) {
    return [...container.querySelectorAll("[data-image-id]")].map((card, index) => ({ id: card.dataset.imageId, image: card.querySelector('[data-image-value="image"]').value, alt: card.querySelector('[data-image-value="alt"]').value, caption: card.querySelector('[data-image-value="caption"]').value, order: index + 1 }));
  }

  function serializeField(wrapper, field) {
    if (!wrapper._dirty && state.current) return clone(wrapper._originalValue);
    const name = wrapper.dataset.schemaField;
    if (["boolean", "checkbox"].includes(field.type)) return wrapper.querySelector("input").checked;
    if (field.type === "integer") return Number(wrapper.querySelector("input").value || 0);
    if (field.type === "datetime") { const value = wrapper.querySelector("input").value; return value ? new Date(value).toISOString() : ""; }
    if (field.type === "blocks") return [...wrapper.querySelectorAll("[data-block-list] > [data-block-id]")].map(serializeBlock);
    if (field.type === "schedule") return [...wrapper.querySelectorAll("[data-schedule-row]")].map(row => Object.fromEntries([...row.querySelectorAll("[data-schedule-value]")].map(input => [input.dataset.scheduleValue, input.value])));
    if (field.type === "image_list") return serializeImages(wrapper.querySelector("[data-image-list-items]"));
    if (["relation", "relation_list", "relation-list"].includes(field.type)) { const values = wrapper.querySelector("[data-relation-editor]")._selected || []; return field.type === "relation" ? values[0] || null : values; }
    if (field.type === "social_links") return wrapper.querySelector("textarea").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean).map(url => ({ network: url.includes("t.me") ? "telegram" : url.includes("vk.com") || url.includes("vkvideo.ru") ? "vk" : url.includes("youtu") ? "youtube" : "other", url, enabled: true }));
    return wrapper.querySelector(`[name="${CSS.escape(name)}"]`)?.value ?? "";
  }

  function editorPayload() {
    const item = definition();
    const existing = clone(state.current?.data || {});
    const data = existing || {};
    let title = state.current?.title || item.title;
    for (const [name, field] of Object.entries(item.fields)) {
      const wrapper = editorForm.querySelector(`[data-schema-field="${CSS.escape(name)}"]`);
      const value = serializeField(wrapper, field);
      if (name === "title") title = String(value || "").trim();
      else if (value === undefined) delete data[name];
      else data[name] = value;
    }
    return { content_id: state.current?.id || null, content_type: state.currentType, title: title || item.title, slug: state.current?.slug || null, data };
  }

  async function saveDraft() {
    if (!can("editor")) throw new Error("Недостаточно прав для сохранения");
    const payload = editorPayload();
    const options = state.current ? { method: "PUT", body: JSON.stringify({ title: payload.title, slug: state.current.slug, data: payload.data, version: state.current.version }) } : { method: "POST", body: JSON.stringify({ content_type: state.currentType, title: payload.title, data: payload.data }) };
    state.current = await apiRequest(state.current ? `/api/admin/contents/${state.current.id}` : "/api/admin/contents", options);
    state.dirty = false;
    document.querySelector("[data-save-status]").textContent = `Сохранено · v${state.current.version}`;
    renderEditor(state.currentType, state.current);
    await loadContentList();
    toast("Черновик сохранён");
    return state.current;
  }

  async function loadContentList() {
    if (!state.user) return;
    const query = document.querySelector("[data-content-search]").value.trim();
    const review = document.querySelector("[data-review-only]").checked ? "&review_required=true" : "";
    const result = await apiRequest(`/api/admin/content-index?content_type=${encodeURIComponent(state.currentType)}&limit=100&q=${encodeURIComponent(query)}${review}`);
    state.list = result.items;
    const select = document.querySelector("[data-content-select]");
    select.innerHTML = `<option value="">Новый материал</option>${state.list.map(item => `<option value="${escapeHtml(item.id)}">${item.is_public ? "●" : item.migration_review_required ? "!" : "○"} ${escapeHtml(item.title)} · ${escapeHtml(statusLabels[item.status] || item.status)} v${item.version}</option>`).join("")}`;
    if (state.current) select.value = state.current.id;
    document.querySelector("[data-content-picker]").hidden = false;
    document.querySelector("[data-content-count]").textContent = `${state.list.length} из ${result.total}`;
  }

  async function openRecord(id) {
    if (!id) { state.current = null; renderEditor(state.currentType, null); return; }
    state.current = await apiRequest(`/api/admin/contents/${encodeURIComponent(id)}`);
    state.currentType = state.current.content_type;
    state.dirty = false;
    renderNavigation();
    renderEditor(state.currentType, state.current);
  }

  function schedulePreview() {
    clearTimeout(state.previewTimer);
    state.previewTimer = setTimeout(updatePreview, 450);
  }

  function applyPreviewSize(size = state.previewSize) {
    const stage = previewFrame.closest(".preview-stage");
    if (!stage) return;
    state.previewSize = size;
    const width = size === "mobile" ? 390 : 1440;
    const height = size === "mobile" ? 1600 : 1900;
    const available = Math.max(220, stage.clientWidth - 48);
    const scale = Math.min(1, available / width);
    previewFrame.style.width = `${width}px`;
    previewFrame.style.height = `${height}px`;
    previewFrame.style.transform = `scale(${scale})`;
    stage.style.height = `${Math.ceil(height * scale + 48)}px`;
    previewFrame.classList.toggle("is-mobile", size === "mobile");
    document.querySelectorAll("[data-preview-size]").forEach(button => {
      const active = button.dataset.previewSize === size;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  }

  async function updatePreview() {
    if (!state.user || !state.schema) return;
    state.previewAbort?.abort();
    state.previewAbort = new AbortController();
    const payload = editorPayload();
    previewFrame.classList.add("is-loading");
    try {
      const html = await apiRequest("/api/admin/content-preview", { method: "POST", body: JSON.stringify(payload), signal: state.previewAbort.signal }, "text");
      previewFrame.srcdoc = html;
    } catch (error) {
      if (error.name !== "AbortError") previewFrame.srcdoc = `<div style="font:16px sans-serif;padding:30px;color:#8b2f22"><b>Предпросмотр недоступен</b><p>${escapeHtml(error.message)}</p></div>`;
    } finally { previewFrame.classList.remove("is-loading"); }
  }

  function uploadFile(file, alt = "", path = "/api/admin/media") {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      const form = new FormData(); form.append("file", file); form.append("alt_text", alt);
      request.open("POST", path); request.withCredentials = true;
      if (state.csrf) request.setRequestHeader("X-CSRF-Token", state.csrf);
      request.upload.addEventListener("progress", event => {
        if (!event.lengthComputable) return;
        document.querySelector("[data-save-status]").textContent = `Загрузка ${file.name}: ${Math.round(event.loaded / event.total * 100)}%`;
      });
      request.addEventListener("load", () => {
        const body = (() => { try { return JSON.parse(request.responseText || "{}"); } catch { return {}; } })();
        if (request.status >= 200 && request.status < 300) { resolve(body); return; }
        const detail = body.detail;
        reject(new Error(typeof detail === "string" ? detail : detail?.message || `Ошибка загрузки (${request.status})`));
      });
      request.addEventListener("error", () => reject(new Error("Сеть прервала загрузку файла")));
      request.send(form);
    });
  }

  async function uploadFiles(files, alt = "", path = "/api/admin/media") {
    const uploaded = [];
    for (const file of files) uploaded.push(await uploadFile(file, alt, path));
    document.querySelector("[data-save-status]").textContent = "Файлы загружены в медиатеку";
    return uploaded;
  }

  function humanSize(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} Б`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} КБ`;
    return `${(value / 1024 / 1024).toFixed(1)} МБ`;
  }

  function mediaIcon(item) {
    const extension = String(item.original_name || item.stored_name || "").split(".").pop().toUpperCase();
    return `<span class="media-file-icon" aria-hidden="true">${escapeHtml(extension.slice(0, 5) || "FILE")}</span>`;
  }

  function mediaCard(item, selectable = false) {
    state.media.items.set(item.id, item);
    const selected = state.media.selected.has(item.id);
    const preview = item.thumbnail_url ? `<img src="${escapeHtml(item.thumbnail_url)}" alt="">` : mediaIcon(item);
    const source = item.source === "legacy" ? "Архив" : "Загрузка";
    return `<article class="media-card${selected ? " is-selected" : ""}" data-media-id="${escapeHtml(item.id)}"><button class="media-card__preview" type="button" ${selectable ? "data-media-select" : "data-media-open"} aria-label="${selectable ? "Выбрать" : "Открыть"} ${escapeHtml(item.original_name)}">${preview}${selectable ? `<span class="media-card__check" aria-hidden="true">${selected ? "✓" : ""}</span>` : ""}</button><div class="media-card__body"><strong title="${escapeHtml(item.original_name)}">${escapeHtml(item.original_name)}</strong><small>${escapeHtml(source)} · ${humanSize(item.size_bytes)}</small><small>${item.content_count ? `В ${item.content_count} материалах` : "Не используется"}</small></div>${!selectable ? '<button class="media-card__more" type="button" data-media-open aria-label="Подробности">•••</button>' : ""}</article>`;
  }

  function mediaQuery() {
    const params = new URLSearchParams({ limit: "48", offset: String(state.media.offset), sort: "newest" });
    if (state.media.q) params.set("q", state.media.q);
    if (state.media.kind) params.set("kind", state.media.kind);
    if (state.media.usage) params.set("usage", state.media.usage);
    return params;
  }

  async function loadMediaDialog() {
    const grid = document.querySelector("[data-media-grid]");
    grid.innerHTML = '<p class="media-loading">Загружаем файлы…</p>';
    const result = await apiRequest(`/api/admin/media?${mediaQuery()}`);
    state.media.total = result.total;
    grid.innerHTML = result.items.map(item => mediaCard(item, Boolean(state.media.chooser))).join("") || '<div class="history-empty">Файлы не найдены</div>';
    document.querySelector("[data-media-count]").textContent = `${Math.min(state.media.offset + 1, result.total)}–${Math.min(state.media.offset + result.items.length, result.total)} из ${result.total}`;
    document.querySelector("[data-media-prev]").disabled = state.media.offset === 0;
    document.querySelector("[data-media-next]").disabled = state.media.offset + result.items.length >= result.total;
    document.querySelector("[data-media-use]").hidden = !state.media.chooser;
    document.querySelector("[data-media-use]").disabled = state.media.selected.size === 0;
  }

  function openMediaChooser({ kind = "", multiple = false, apply }) {
    state.media.chooser = { multiple, apply };
    state.media.kind = kind;
    state.media.offset = 0;
    state.media.selected.clear();
    document.querySelector("[data-media-kind]").value = kind;
    document.querySelector("[data-media-search]").value = "";
    document.querySelector("[data-media-usage]").value = "";
    document.querySelector("[data-media-dialog-title]").textContent = multiple ? "Выбрать несколько файлов" : "Выбрать файл";
    document.querySelector("[data-library-upload]").accept = kind === "image" ? ".jpg,.jpeg,.png,.webp" : kind === "video" ? ".mp4" : ".pdf,.docx,.xlsx,.pptx,.csv,.txt,.doc,.xls,.ppt,.mp4";
    document.querySelector("[data-library-upload]").multiple = multiple;
    document.querySelector("[data-media-dialog]").showModal();
    loadMediaDialog().catch(error => toast(error.message));
  }

  function closeMediaDialog() {
    const dialog = document.querySelector("[data-media-dialog]");
    if (dialog.open) dialog.close();
    state.media.chooser = null; state.media.selected.clear();
  }

  function renderMediaPanel() {
    panel.innerHTML = `<div class="media-panel-head"><div><div class="eyebrow">Управление файлами</div><h1>Медиатека</h1><p>Оригиналы сохраняют постоянные URL. Повторное использование не создаёт копий.</p></div>${can("editor") ? '<label class="button button--primary">Загрузить файлы<input class="cms-file-input" type="file" multiple data-panel-media-upload accept=".jpg,.jpeg,.png,.webp,.mp4,.pdf,.docx,.xlsx,.pptx,.csv,.txt,.doc,.xls,.ppt"></label>' : ""}</div><nav class="media-tabs"><button class="is-active" type="button" data-media-tab="files">Файлы</button><button type="button" data-media-tab="issues">Утрачено</button></nav><div class="media-panel-toolbar"><input type="search" placeholder="Поиск по имени, alt или материалу" data-panel-media-search><select data-panel-media-kind><option value="">Все типы</option><option value="image">Изображения</option><option value="video">Видео</option><option value="document">Документы</option></select><select data-panel-media-usage><option value="">Любое использование</option><option value="used">Используется</option><option value="unused">Не используется</option></select>${can("admin") ? '<button class="button button--ghost button--compact" type="button" data-media-reindex>Проверить индекс</button>' : ""}</div><div class="media-grid media-grid--panel" data-panel-media-grid></div><div class="media-pagination"><span data-panel-media-count></span><button class="button button--ghost button--compact" type="button" data-panel-media-prev>← Назад</button><button class="button button--ghost button--compact" type="button" data-panel-media-next>Дальше →</button></div>`;
    panel.querySelector(".media-panel-head")?.setAttribute("data-media-dropzone", "");
    state.media.offset = 0; state.media.q = ""; state.media.kind = ""; state.media.usage = ""; state.media.panelTab = "files";
    loadMediaPanel().catch(error => toast(error.message));
  }

  async function loadMediaPanel() {
    const grid = document.querySelector("[data-panel-media-grid]");
    if (!grid) return;
    grid.innerHTML = '<p class="media-loading">Загружаем…</p>';
    if (state.media.panelTab === "issues") {
      const params = new URLSearchParams({ limit: "48", offset: String(state.media.offset) });
      if (state.media.q) params.set("q", state.media.q);
      const result = await apiRequest(`/api/admin/media-issues?${params}`);
      state.media.total = result.total;
      grid.innerHTML = result.items.map(item => `<article class="missing-media-card"><div class="missing-media-card__icon">!</div><div><strong>${escapeHtml(item.source_url.split("/").pop())}</strong><p>${escapeHtml(item.source_directory)}</p><small>${item.content_count ? `Связано с ${item.content_count} материалами` : "Связанные материалы не определены"}</small><small class="media-status media-status--${escapeHtml(item.status)}">${item.status === "resolved" ? "Замена загружена" : "Файл утрачен"}</small></div>${can("editor") && item.status === "pending" ? `<label class="button button--ghost button--compact">Загрузить замену<input class="cms-file-input" type="file" data-issue-upload="${escapeHtml(item.id)}"></label>` : ""}</article>`).join("") || '<div class="history-empty">Записей нет</div>';
      document.querySelector("[data-panel-media-count]").textContent = `${result.total} утраченных файлов`;
      document.querySelector("[data-panel-media-prev]").disabled = state.media.offset === 0;
      document.querySelector("[data-panel-media-next]").disabled = state.media.offset + result.items.length >= result.total;
      return;
    }
    const result = await apiRequest(`/api/admin/media?${mediaQuery()}`);
    state.media.total = result.total;
    grid.innerHTML = result.items.map(item => mediaCard(item)).join("") || '<div class="history-empty">Файлы не найдены</div>';
    document.querySelector("[data-panel-media-count]").textContent = `${Math.min(state.media.offset + 1, result.total)}–${Math.min(state.media.offset + result.items.length, result.total)} из ${result.total}`;
    document.querySelector("[data-panel-media-prev]").disabled = state.media.offset === 0;
    document.querySelector("[data-panel-media-next]").disabled = state.media.offset + result.items.length >= result.total;
  }

  async function openMediaDetail(mediaId) {
    const [item, usages] = await Promise.all([apiRequest(`/api/admin/media/${mediaId}`), apiRequest(`/api/admin/media/${mediaId}/usages?limit=50`)]);
    const preview = item.preview_url ? `<img class="media-detail__preview" src="${escapeHtml(item.preview_url)}" alt="">` : mediaIcon(item);
    document.querySelector("[data-media-detail]").innerHTML = `<div class="eyebrow">${escapeHtml(item.kind === "image" ? "Изображение" : item.kind === "video" ? "Видео" : "Документ")}</div><h2 id="media-detail-title">${escapeHtml(item.original_name)}</h2><div class="media-detail-grid"><div>${preview}<dl><div><dt>Размер</dt><dd>${humanSize(item.size_bytes)}</dd></div><div><dt>URL</dt><dd><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener">${escapeHtml(item.url)}</a></dd></div><div><dt>Источник</dt><dd>${item.source === "legacy" ? "Архив старого сайта" : "Загружен в CMS"}</dd></div></dl></div><div>${item.kind === "image" ? `<label class="field">Alt-текст<textarea rows="3" data-media-alt ${can("editor") ? "" : "readonly"}>${escapeHtml(item.alt_text || "")}</textarea></label>` : ""}<h3>Использование</h3><div class="media-usage-list">${usages.items.map(use => `<a href="/cms.html?content=${escapeHtml(use.content_id)}"><b>${escapeHtml(use.title)}</b><small>${use.revision_version ? `Версия ${use.revision_version}` : "Рабочая версия"}${use.is_published ? " · на сайте" : ""}</small></a>`).join("") || "<p>Файл не используется.</p>"}</div>${can("editor") ? `<div class="media-detail-actions"><button class="button button--primary button--compact" type="button" data-media-save="${escapeHtml(item.id)}" data-version="${item.version}">Сохранить alt</button><label class="button button--ghost button--compact">Загрузить замену<input class="cms-file-input" type="file" data-media-replace="${escapeHtml(item.id)}"></label>${can("admin") && item.usage_count === 0 ? `<button class="button button--danger button--compact" type="button" data-media-delete="${escapeHtml(item.id)}" data-version="${item.version}">Удалить</button>` : ""}</div>` : ""}</div></div>`;
    document.querySelector("[data-media-detail-dialog]").showModal();
  }

  async function postWorkflow(action, payload = {}) {
    if (!state.current) throw new Error("Сначала сохраните материал");
    state.current = await apiRequest(`/api/admin/contents/${state.current.id}/${action}`, { method: "POST", body: JSON.stringify({ version: state.current.version, ...payload }) });
    state.dirty = false;
    renderEditor(state.currentType, state.current);
    await loadContentList();
  }

  async function auditCurrent() {
    if (!state.current) return;
    if (state.dirty) await saveDraft();
    const result = await apiRequest(`/api/admin/contents/${state.current.id}/migration-audit`, { method: "POST", body: JSON.stringify({ check_external: true }) });
    toast(`Аудит завершён: ${Number(result.counts.blocker || 0)} блокирующих, ${Number(result.counts.warning || 0)} предупреждений`);
  }

  async function submitReview() { if (state.dirty || !state.current) await saveDraft(); await postWorkflow("submit-review"); toast("Материал отправлен на проверку"); }
  async function publishCurrent() { await postWorkflow("publish"); toast("Материал опубликован"); }

  async function openHistory() {
    const [revisions, audit] = await Promise.all([apiRequest(`/api/admin/contents/${state.current.id}/revisions`), apiRequest(`/api/admin/contents/${state.current.id}/audit-events`)]);
    document.querySelector("[data-revision-list]").innerHTML = revisions.items.map(item => `<article class="history-item"><div class="history-item__head"><div><b>Версия ${item.version}</b><p>${escapeHtml(formatDate(item.created_at))} · ${escapeHtml(item.actor_username || "система")}</p></div>${can("editor") && !item.is_current ? `<button class="button button--ghost button--compact" type="button" data-restore-revision="${item.version}">Восстановить</button>` : ""}</div><div class="history-item__badges">${item.is_current ? '<span class="history-badge">Рабочая</span>' : ""}${item.is_published ? '<span class="history-badge">На сайте</span>' : ""}</div></article>`).join("") || '<div class="history-empty">Ревизий пока нет</div>';
    document.querySelector("[data-audit-list]").innerHTML = audit.items.map(item => `<article class="history-item"><div class="history-item__head"><b>${escapeHtml(auditLabels[item.action] || item.action)}</b><span class="history-badge">v${item.content_version}</span></div><p>${escapeHtml(formatDate(item.created_at))} · ${escapeHtml(item.actor_username || "система")}</p></article>`).join("") || '<div class="history-empty">Действий пока нет</div>';
    document.querySelector("[data-history-dialog]").showModal();
  }

  async function restoreRevision(version) {
    state.current = await apiRequest(`/api/admin/contents/${state.current.id}/revisions/${version}/restore`, { method: "POST", body: JSON.stringify({ version: state.current.version }) });
    renderEditor(state.currentType, state.current); await loadContentList(); toast(`Версия ${version} восстановлена как новый черновик`);
  }

  function bulkActionConfig(action = state.bulk.action) {
    return {
      publish: { label: "Опубликовать", query: "status=in_review", confirm: "Опубликовать выбранные материалы?" },
      archive: { label: "Архивировать", query: "statuses=draft,in_review,scheduled,published", confirm: "Переместить выбранные материалы в архив?" },
    }[action];
  }

  function bulkAllowed(action = state.bulk.action) {
    return can("publisher");
  }

  function renderWorkflowPanel() {
    const actionButtons = ["publish", "archive"].map(action => {
      const label = { publish: "Готово к публикации", archive: "Архивирование" }[action];
      return `<button class="${state.bulk.action === action ? "is-active" : ""}" type="button" data-bulk-action="${action}">${label}</button>`;
    }).join("");
    panel.innerHTML = `<div class="workflow-panel"><div class="workflow-panel__head"><div><div class="eyebrow">Редакционная очередь</div><h1>Массовые действия</h1><p>Операция выполняется атомарно: при конфликте ни один выбранный материал не изменяется.</p></div></div><nav class="media-tabs">${actionButtons}</nav><div class="workflow-toolbar"><input type="search" value="${escapeHtml(state.bulk.q)}" placeholder="Название, slug или старый URL" data-bulk-search><button class="button button--ghost button--compact" type="button" data-bulk-select-all${bulkAllowed() ? "" : " hidden"}>Выбрать страницу</button><button class="button button--primary button--compact" type="button" data-bulk-apply${bulkAllowed() ? "" : " hidden"}>${escapeHtml(bulkActionConfig().label)}</button></div><div class="bulk-list" data-bulk-list><div class="history-empty">Загружаем очередь…</div></div><div class="media-pagination"><span data-bulk-count></span><button class="button button--ghost button--compact" type="button" data-bulk-prev>← Назад</button><button class="button button--ghost button--compact" type="button" data-bulk-next>Дальше →</button></div></div>`;
    loadBulkQueue().catch(error => toast(error.message));
  }

  async function loadBulkQueue() {
    const config = bulkActionConfig();
    const params = new URLSearchParams({ limit: "100", offset: String(state.bulk.offset), q: state.bulk.q });
    for (const pair of config.query.split("&")) { const [name, value] = pair.split("="); params.set(name, value); }
    const result = await apiRequest(`/api/admin/content-index?${params}`);
    state.bulk.items = result.items;
    state.bulk.total = result.total;
    const ids = new Set(result.items.map(item => item.id));
    state.bulk.selected = new Set([...state.bulk.selected].filter(id => ids.has(id)));
    const list = document.querySelector("[data-bulk-list]");
    if (!list) return;
    list.innerHTML = result.items.map(item => `<article class="bulk-item"><input type="checkbox" data-bulk-check="${escapeHtml(item.id)}"${state.bulk.selected.has(item.id) ? " checked" : ""}${bulkAllowed() ? "" : " disabled"} aria-label="Выбрать ${escapeHtml(item.title)}"><button type="button" data-bulk-open="${escapeHtml(item.id)}" data-bulk-type="${escapeHtml(item.content_type)}"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(state.schema.content_types[item.content_type]?.label || item.content_type)} · ${escapeHtml(statusLabels[item.status] || item.status)} · v${item.version}</small></button>${item.migration_review_required ? '<span class="state-pill state-pill--warn">Требует проверки</span>' : '<span class="state-pill">Проверен</span>'}</article>`).join("") || '<div class="history-empty">Для этого действия материалов нет</div>';
    document.querySelector("[data-bulk-count]").textContent = `${Math.min(state.bulk.offset + 1, result.total)}–${Math.min(state.bulk.offset + result.items.length, result.total)} из ${result.total} · выбрано ${state.bulk.selected.size}`;
    document.querySelector("[data-bulk-prev]").disabled = state.bulk.offset === 0;
    document.querySelector("[data-bulk-next]").disabled = state.bulk.offset + result.items.length >= result.total;
    const apply = document.querySelector("[data-bulk-apply]");
    if (apply) apply.disabled = !state.bulk.selected.size;
  }

  async function applyBulkAction() {
    const config = bulkActionConfig();
    if (!state.bulk.selected.size || !confirm(config.confirm)) return;
    const versions = new Map(state.bulk.items.map(item => [item.id, item.version]));
    const items = [...state.bulk.selected].map(id => ({ id, version: versions.get(id) })).filter(item => item.version);
    const result = await apiRequest("/api/admin/content-bulk", { method: "POST", body: JSON.stringify({ action: state.bulk.action, items }) });
    state.bulk.selected.clear();
    await loadBulkQueue();
    toast(`Обновлено материалов: ${result.updated}`);
  }

  const submissionTypeLabels = { prayer_note: "Записка", school_enrollment: "Воскресная школа" };
  const submissionStatusLabels = { new: "Новая", in_progress: "В работе", done: "Завершена", spam: "Спам" };
  const notificationStatusLabels = { pending: "Ожидает отправки", sending: "Отправляется", sent: "Отправлено", failed: "Ошибка отправки" };
  let submissionOpener = null;

  function submissionQuery() {
    const params = new URLSearchParams({ limit: "50", offset: String(state.submissions.offset) });
    if (state.submissions.q) params.set("q", state.submissions.q);
    if (state.submissions.type) params.set("type", state.submissions.type);
    if (state.submissions.status) params.set("status", state.submissions.status);
    return params.toString();
  }

  function updateSubmissionBadge(amount) {
    const badge = document.querySelector("[data-submissions-badge]");
    if (!badge) return;
    badge.textContent = String(amount || 0);
    badge.hidden = !amount || !can("publisher");
  }

  async function refreshSubmissionBadge() {
    if (!can("publisher")) { updateSubmissionBadge(0); return; }
    const result = await apiRequest("/api/admin/submissions?limit=1");
    updateSubmissionBadge(result.new_total);
  }

  function renderSubmissionsPanel() {
    if (!can("publisher")) {
      panel.innerHTML = '<div class="history-empty">Заявки посетителей доступны только выпускающему и администратору.</div>';
      return;
    }
    panel.innerHTML = `<div class="submissions-panel"><div class="submissions-panel__head"><div><div class="eyebrow">Обращения посетителей</div><h1>Заявки</h1><p>Персональные данные показываются только при открытии карточки. Закрытые записки удаляются через 30 дней, школьные заявки — через 180 дней.</p></div></div><div class="submissions-toolbar"><input type="search" value="${escapeHtml(state.submissions.q)}" placeholder="Номер заявки" data-submission-search><select data-submission-type><option value="">Все виды</option><option value="prayer_note"${state.submissions.type === "prayer_note" ? " selected" : ""}>Записки</option><option value="school_enrollment"${state.submissions.type === "school_enrollment" ? " selected" : ""}>Воскресная школа</option></select><select data-submission-status><option value="">Все статусы</option>${Object.entries(submissionStatusLabels).map(([value, label]) => `<option value="${value}"${state.submissions.status === value ? " selected" : ""}>${label}</option>`).join("")}</select></div><div class="submission-list" data-submission-list><div class="history-empty">Загружаем очередь…</div></div><div class="media-pagination"><span data-submission-count></span><button class="button button--ghost button--compact" type="button" data-submission-prev>← Назад</button><button class="button button--ghost button--compact" type="button" data-submission-next>Дальше →</button></div></div>`;
    loadSubmissions().catch(error => toast(error.message));
  }

  async function loadSubmissions() {
    const result = await apiRequest(`/api/admin/submissions?${submissionQuery()}`);
    state.submissions.items = result.items;
    state.submissions.total = result.total;
    updateSubmissionBadge(result.new_total);
    const list = document.querySelector("[data-submission-list]");
    if (!list) return;
    list.innerHTML = result.items.map(item => `<button class="submission-row" type="button" data-submission-open="${escapeHtml(item.id)}"><span class="submission-row__reference">${escapeHtml(item.reference_code)}</span><span><strong>${escapeHtml(submissionTypeLabels[item.submission_type] || item.submission_type)}</strong><small>${escapeHtml(formatDate(item.created_at))}</small></span><span class="submission-state submission-state--${escapeHtml(item.status)}">${escapeHtml(submissionStatusLabels[item.status] || item.status)}</span><span class="notification-state notification-state--${escapeHtml(item.notification.status || "pending")}">${escapeHtml(notificationStatusLabels[item.notification.status] || item.notification.status || "Неизвестно")}</span></button>`).join("") || '<div class="history-empty">Заявки не найдены</div>';
    document.querySelector("[data-submission-count]").textContent = result.total ? `${state.submissions.offset + 1}–${Math.min(state.submissions.offset + result.items.length, result.total)} из ${result.total}` : "0 заявок";
    document.querySelector("[data-submission-prev]").disabled = state.submissions.offset === 0;
    document.querySelector("[data-submission-next]").disabled = state.submissions.offset + result.items.length >= result.total;
  }

  function submissionPayloadHtml(item) {
    const payload = item.payload || {};
    if (item.submission_type === "prayer_note") {
      const labels = { health: "О здравии", repose: "Об упокоении", moleben: "Молебен" };
      return `<dl class="submission-fields"><div><dt>Вид записки</dt><dd>${escapeHtml(labels[payload.remembrance_type] || payload.remembrance_type || "—")}</dd></div><div><dt>Имена</dt><dd><ol>${(payload.names || []).map(name => `<li>${escapeHtml(name)}</li>`).join("")}</ol></dd></div></dl>`;
    }
    return `<dl class="submission-fields"><div><dt>Родитель</dt><dd>${escapeHtml(payload.parent_name || "—")}</dd></div><div><dt>Контакт</dt><dd>${escapeHtml(payload.contact || "—")}</dd></div><div><dt>Ребёнок</dt><dd>${escapeHtml(payload.child_name || "—")}</dd></div><div><dt>Возраст</dt><dd>${escapeHtml(payload.child_age ?? "—")}</dd></div><div><dt>Комментарий</dt><dd class="submission-comment">${escapeHtml(payload.comment || "—")}</dd></div><div><dt>Согласие</dt><dd>${payload.consent ? `Получено · ${escapeHtml(formatDate(payload.consented_at))}` : "Не получено"}</dd></div></dl>`;
  }

  function submissionActions(item) {
    const transitions = { new: ["in_progress", "done", "spam"], in_progress: ["new", "done", "spam"], done: ["in_progress"], spam: ["in_progress"] };
    return (transitions[item.status] || []).map(status => `<button class="button ${status === "spam" ? "button--danger" : status === "in_progress" ? "button--primary" : "button--ghost"} button--compact" type="button" data-submission-status-action="${status}">${escapeHtml({ new: "Вернуть в новые", in_progress: "Взять в работу", done: "Завершить", spam: "Это спам" }[status])}</button>`).join("");
  }

  function renderSubmissionDetail(item) {
    const notification = item.notification || {};
    const canRetry = !["sent", "sending"].includes(notification.status);
    const events = (item.events || []).map(event => `<article class="history-item"><div class="history-item__head"><b>${escapeHtml({ created: "Заявка создана", status_changed: "Статус изменён", notification_retried: "Отправка запущена повторно", notification_sent: "Уведомление отправлено" }[event.action] || event.action)}</b>${event.to_status ? `<span class="history-badge">${escapeHtml(submissionStatusLabels[event.to_status] || event.to_status)}</span>` : ""}</div><p>${escapeHtml(formatDate(event.created_at))} · ${escapeHtml(event.actor || "система")}</p></article>`).join("");
    document.querySelector("[data-submission-detail]").innerHTML = `<div class="eyebrow">${escapeHtml(submissionTypeLabels[item.submission_type] || item.submission_type)}</div><h2 id="submission-dialog-title">${escapeHtml(item.reference_code)}</h2><div class="submission-detail-meta"><span class="submission-state submission-state--${escapeHtml(item.status)}">${escapeHtml(submissionStatusLabels[item.status] || item.status)}</span><span>${escapeHtml(formatDate(item.created_at))}</span></div>${submissionPayloadHtml(item)}<section class="submission-notification"><h3>Email-уведомление</h3><p><b>${escapeHtml(notificationStatusLabels[notification.status] || notification.status || "Неизвестно")}</b> · попыток: ${Number(notification.attempts || 0)}</p>${notification.sent_at ? `<p>Отправлено: ${escapeHtml(formatDate(notification.sent_at))}</p>` : ""}${notification.last_error ? `<p class="form-error">${escapeHtml(notification.last_error)}</p>` : ""}${notification.configured === false ? '<p class="form-error">SMTP не настроен.</p>' : ""}${canRetry ? '<button class="button button--ghost button--compact" type="button" data-submission-retry>Повторить отправку</button>' : ""}</section><section class="submission-actions"><h3>Статус заявки</h3><div>${submissionActions(item)}</div></section><section class="submission-events"><h3>Журнал</h3><div class="history-list">${events || '<div class="history-empty">Событий пока нет</div>'}</div></section>`;
  }

  async function openSubmission(id, opener = null) {
    state.submissions.current = await apiRequest(`/api/admin/submissions/${id}`);
    submissionOpener = opener;
    renderSubmissionDetail(state.submissions.current);
    document.querySelector("[data-submission-dialog]").showModal();
  }

  async function changeSubmissionStatus(status) {
    const item = state.submissions.current;
    if (!item) return;
    state.submissions.current = await apiRequest(`/api/admin/submissions/${item.id}/status`, { method: "PATCH", body: JSON.stringify({ version: item.version, status }) });
    renderSubmissionDetail(state.submissions.current);
    await loadSubmissions();
    toast("Статус заявки обновлён");
  }

  async function retrySubmissionNotification() {
    const item = state.submissions.current;
    if (!item) return;
    state.submissions.current = await apiRequest(`/api/admin/submissions/${item.id}/retry-notification`, { method: "POST", body: JSON.stringify({ version: item.version }) });
    renderSubmissionDetail(state.submissions.current);
    await loadSubmissions();
    toast("Уведомление возвращено в очередь");
  }

  async function renderUsersPanel() {
    if (!can("admin")) { panel.innerHTML = '<div class="history-empty">Управление пользователями доступно только администратору.</div>'; return; }
    panel.innerHTML = '<div class="history-empty">Загружаем пользователей…</div>';
    const [users, events] = await Promise.all([apiRequest("/api/admin/users"), apiRequest("/api/admin/user-events?limit=100")]);
    state.users = users.items;
    panel.innerHTML = `<div class="users-panel"><div class="users-panel__head"><div><div class="eyebrow">Доступ к CMS</div><h1>Пользователи</h1><p>Роли определяют доступ к содержимому, публикации и администрированию.</p></div><button class="button button--primary" type="button" data-user-create>Создать пользователя</button></div><div class="users-table" data-users-list>${users.items.map(user => `<article class="user-row" data-user-id="${escapeHtml(user.id)}" data-version="${user.version}"><div class="user-row__identity"><span>${escapeHtml(user.username.slice(0, 1).toUpperCase())}</span><div><strong>${escapeHtml(user.username)}</strong><small>${user.last_login_at ? `Последний вход: ${escapeHtml(formatDate(user.last_login_at))}` : "Ещё не входил"}</small></div></div><label>Роль<select data-user-role>${Object.keys(roleLabels).map(role => `<option value="${role}"${role === user.role ? " selected" : ""}>${escapeHtml(roleLabels[role])}</option>`).join("")}</select></label><label class="user-active"><input type="checkbox" data-user-active${user.is_active ? " checked" : ""}> Активен</label><div class="user-row__sessions"><b>${user.active_sessions}</b><small>активных сессий</small></div><div class="user-row__actions"><button class="button button--ghost button--compact" type="button" data-user-save>Сохранить</button><button class="button button--ghost button--compact" type="button" data-user-terminate${user.id === state.user.id || !user.active_sessions ? " disabled" : ""}>Завершить сессии</button></div></article>`).join("")}</div><section class="user-events"><h2>Журнал доступа</h2><div class="history-list">${events.items.map(item => `<article class="history-item"><div class="history-item__head"><b>${escapeHtml(userEventLabels[item.action] || item.action)}</b><span class="history-badge">${escapeHtml(item.target_username || "удалённый пользователь")}</span></div><p>${escapeHtml(formatDate(item.created_at))} · ${escapeHtml(item.actor_username || "система")}</p></article>`).join("") || '<div class="history-empty">Событий пока нет</div>'}</div></section></div>`;
  }

  const migrationSeverityLabels = { blocker: "Блокирует приёмку", warning: "Требует подтверждения", info: "Справочно" };
  const migrationBatchStatusLabels = { draft: "Черновик", in_review: "На утверждении", finalized: "Завершена", cancelled: "Отменена" };
  const individualMigrationWarnings = new Set(["duplicate_content", "duplicate_title", "duplicate_media_reference", "external_link_unavailable", "missing_legacy_media", "unpublished_internal_link"]);

  function migrationSourceText(item) {
    const data = item.data || {};
    if (typeof data.body_text === "string") return data.body_text;
    const source = data.body || data.biography;
    if (typeof source === "string") return source;
    if (!Array.isArray(source)) return "";
    return source.filter(block => block?.type === "legacy_text").map(block => block.data?.text || block.text || "").join("\n\n");
  }

  function renderMigrationDashboardShell() {
    panel.innerHTML = `<div class="acceptance-panel"><div class="workflow-panel__head"><div><div class="eyebrow">Редакторская приёмка</div><h1>Перенесённые материалы</h1><p>Автоматический аудит ничего не публикует и не снимает флаги. Решения применяются только при финализации партии.</p></div><div class="migration-dashboard-actions">${can("publisher") ? '<button class="button button--ghost" type="button" data-migration-run>Запустить аудит</button><button class="button button--primary" type="button" data-migration-pilot>Создать пилотную партию</button>' : ""}</div></div><div class="metric-grid" data-acceptance-metrics></div><section class="acceptance-section"><div class="acceptance-section__head"><div><h2>Проблемы</h2><p>Фильтры применяются к последнему актуальному аудиту рабочей версии.</p></div></div><div class="acceptance-filters"><select data-migration-severity><option value="">Все уровни</option><option value="blocker">Блокирующие</option><option value="warning">Предупреждения</option><option value="info">Справочные</option></select><input type="text" inputmode="numeric" placeholder="Год" data-migration-year><select data-migration-type><option value="">Все типы</option>${Object.entries(state.schema.content_types).map(([type, definition]) => `<option value="${escapeHtml(type)}">${escapeHtml(definition.label)}</option>`).join("")}</select><input type="search" placeholder="Код, заголовок или старый URL" data-migration-query><button class="button button--ghost button--compact" type="button" data-migration-filter>Применить</button></div><div class="acceptance-issues" data-acceptance-issues></div><div class="media-pagination"><span data-acceptance-issue-count></span><button class="button button--ghost button--compact" type="button" data-migration-issues-prev>← Назад</button><button class="button button--ghost button--compact" type="button" data-migration-issues-next>Дальше →</button></div></section><section class="acceptance-section"><div class="acceptance-section__head"><div><h2>Редакционные партии</h2><p>До 50 зафиксированных версий; обязательная выборка и warnings проверяются до финализации.</p></div></div><div class="acceptance-batches" data-acceptance-batches></div></section><section class="acceptance-section"><h2>Запуски аудита</h2><div class="acceptance-runs" data-acceptance-runs></div></section></div>`;
    panel.querySelector("[data-acceptance-metrics]").insertAdjacentHTML(
      "afterend",
      '<div class="acceptance-breakdown" data-acceptance-breakdown></div>',
    );
  }

  function migrationIssueQuery() {
    const params = new URLSearchParams({ limit: "50", offset: String(state.migration.issuesOffset), status: "open" });
    const severity = panel.querySelector("[data-migration-severity]")?.value;
    const year = panel.querySelector("[data-migration-year]")?.value;
    const type = panel.querySelector("[data-migration-type]")?.value;
    const q = panel.querySelector("[data-migration-query]")?.value.trim();
    if (severity) params.set("severity", severity);
    if (year) params.set("year", year);
    if (type) params.set("content_type", type);
    if (q) params.set("q", q);
    return params;
  }

  async function refreshMigrationStatus() {
    if (!panel.querySelector("[data-acceptance-metrics]")) return;
    const [status, issues, batches] = await Promise.all([
      apiRequest("/api/admin/migration"),
      apiRequest(`/api/admin/migration/issues?${migrationIssueQuery()}`),
      apiRequest("/api/admin/migration/batches"),
    ]);
    const acceptance = status.acceptance || { totals: status.totals, issues: {}, runs: [] };
    const total = Number(acceptance.totals.contents || 0), remaining = Number(acceptance.totals.review_required || 0);
    panel.querySelector("[data-acceptance-metrics]").innerHTML = [
      ["Осталось принять", remaining], ["Блокирующих", acceptance.issues.blocker || 0],
      ["Предупреждений", acceptance.issues.warning || 0], ["Всего материалов", total],
    ].map(([label, value]) => `<article class="metric-card"><b>${Number(value).toLocaleString("ru-RU")}</b><span>${label}</span></article>`).join("");
    const breakdown = panel.querySelector("[data-acceptance-breakdown]");
    if (breakdown) {
      const typeRows = (acceptance.by_type || []).map(row => {
        const label = state.schema.content_types[row.content_type]?.label || row.content_type;
        return `<li><span>${escapeHtml(label)}</span><b>${Number(row.review_required || 0).toLocaleString("ru-RU")} / ${Number(row.total || 0).toLocaleString("ru-RU")}</b><small>${Number(row.blockers || 0)} блок. · ${Number(row.warnings || 0)} предупр.</small></li>`;
      }).join("");
      const yearRows = (acceptance.by_year || []).slice(0, 12).map(row => `<li><span>${escapeHtml(row.year === "unknown" ? "Без года" : row.year)}</span><b>${Number(row.review_required || 0).toLocaleString("ru-RU")} / ${Number(row.total || 0).toLocaleString("ru-RU")}</b><small>${Number(row.blockers || 0)} блок. · ${Number(row.warnings || 0)} предупр.</small></li>`).join("");
      breakdown.innerHTML = `<section><h2>По типам</h2><ul>${typeRows || "<li>Данных пока нет</li>"}</ul></section><section><h2>По годам</h2><ul>${yearRows || "<li>Данных пока нет</li>"}</ul></section>`;
    }
    panel.querySelector("[data-acceptance-issues]").innerHTML = issues.items.map(issue => `<article class="acceptance-issue acceptance-issue--${escapeHtml(issue.severity)}"><span class="state-pill">${escapeHtml(migrationSeverityLabels[issue.severity] || issue.severity)}</span><div><strong>${escapeHtml(issue.title)}</strong><p>${escapeHtml(issue.message)}</p><small>${escapeHtml(state.schema.content_types[issue.content_type]?.label || issue.content_type)} · v${issue.content_version} · ${escapeHtml(issue.code)}${issue.field_path ? ` · ${escapeHtml(issue.field_path)}` : ""}</small></div><button class="button button--ghost button--compact" type="button" data-migration-open-content="${escapeHtml(issue.content_id)}" data-content-type="${escapeHtml(issue.content_type)}">Исправить</button></article>`).join("") || '<div class="history-empty">По выбранным фильтрам открытых проблем нет.</div>';
    panel.querySelector("[data-acceptance-issue-count]").textContent = `${issues.total.toLocaleString("ru-RU")} проблем`;
    panel.querySelector("[data-migration-issues-prev]").disabled = state.migration.issuesOffset === 0;
    panel.querySelector("[data-migration-issues-next]").disabled = state.migration.issuesOffset + issues.items.length >= issues.total;
    panel.querySelector("[data-acceptance-batches]").innerHTML = batches.items.map(batch => `<button class="acceptance-batch" type="button" data-migration-batch="${escapeHtml(batch.id)}"><span class="state-pill">${escapeHtml(migrationBatchStatusLabels[batch.status] || batch.status)}</span><strong>${escapeHtml(batch.name)}</strong><small>${batch.decided_count || 0}/${batch.item_count || 0} решений · выборка ${batch.reviewed_count || 0}/${batch.sampled_count || 0}</small></button>`).join("") || '<div class="history-empty">Партий пока нет. После полного аудита создайте пилотную партию.</div>';
    panel.querySelector("[data-acceptance-runs]").innerHTML = (acceptance.runs || []).map(run => `<article class="acceptance-run"><span class="state-pill">${escapeHtml(run.status)}</span><b>${escapeHtml(formatDate(run.created_at))}</b><small>${Number(run.counts?.contents || 0)} материалов · ${Number(run.counts?.blocker || 0)} блокирующих · ${Number(run.counts?.warning || 0)} предупреждений${run.error ? ` · ${escapeHtml(run.error)}` : ""}</small></article>`).join("") || '<div class="history-empty">Аудит ещё не запускался.</div>';
  }

  function renderMigrationBatch(batch) {
    state.migration.currentBatch = batch;
    const batchWarningCodes = [...new Set(batch.items.flatMap(item => item.issues.filter(issue => issue.severity === "warning" && !individualMigrationWarnings.has(issue.code)).map(issue => issue.code)))];
    const readOnly = !can("editor") || ["finalized", "cancelled"].includes(batch.status);
    panel.innerHTML = `<div class="acceptance-panel"><button class="button button--ghost button--compact" type="button" data-migration-back>← К dashboard</button><div class="workflow-panel__head"><div><div class="eyebrow">${escapeHtml(batch.kind === "priority" ? "Приоритетная партия" : "Архивная партия")}</div><h1>${escapeHtml(batch.name)}</h1><p>${batch.progress.decided}/${batch.progress.items} решений · обязательная выборка ${batch.progress.reviewed}/${batch.progress.sampled} · ${batch.progress.blockers} блокирующих · ${batch.progress.warnings} предупреждений</p></div><span class="state-pill">${escapeHtml(migrationBatchStatusLabels[batch.status] || batch.status)}</span></div>${batchWarningCodes.length ? `<section class="acceptance-section"><h2>Подтверждения партии</h2><p>Для общих предупреждений укажите осмысленный редакционный комментарий.</p><div class="acceptance-ack-list">${batchWarningCodes.map(code => `<label>${escapeHtml(code)}<textarea rows="2" data-batch-ack="${escapeHtml(code)}" ${can("publisher") ? "" : "readonly"}>${escapeHtml(batch.warning_acknowledgements?.[code] || "")}</textarea></label>`).join("")}</div></section>` : ""}<div class="acceptance-batch-items">${batch.items.map(item => { const source = migrationSourceText(item); const warningInputs = item.issues.filter(issue => issue.severity === "warning" && individualMigrationWarnings.has(issue.code)).map(issue => `<label>${escapeHtml(issue.code)}<input type="text" value="${escapeHtml(item.warning_acknowledgements?.[issue.code] || "")}" placeholder="Комментарий обязателен" data-batch-warning="${escapeHtml(issue.code)}" ${readOnly ? "readonly" : ""}></label>`).join(""); return `<article class="acceptance-batch-item" data-batch-content="${escapeHtml(item.content_id)}" data-item-version="${item.version}"><div class="acceptance-batch-item__head"><div><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(state.schema.content_types[item.content_type]?.label || item.content_type)} · рабочая v${item.current_content_version} · аудит v${item.content_version}${item.sampled ? " · обязательная ручная выборка" : ""}</small></div><button class="button button--ghost button--compact" type="button" data-migration-open-content="${escapeHtml(item.content_id)}" data-content-type="${escapeHtml(item.content_type)}">Редактор и preview</button></div>${source ? `<details><summary>Исходный crawl-текст</summary><pre>${escapeHtml(source.slice(0, 12000))}</pre></details>` : ""}<div class="acceptance-item-issues">${item.issues.map(issue => `<span class="acceptance-chip acceptance-chip--${escapeHtml(issue.severity)}" title="${escapeHtml(issue.message)}">${escapeHtml(issue.code)}</span>`).join("") || '<span class="state-pill">Проблем не найдено</span>'}</div><div class="acceptance-decision"><label><input type="checkbox" data-batch-reviewed ${item.manual_reviewed ? "checked" : ""} ${readOnly ? "disabled" : ""}> Материал просмотрен вручную</label><label>Решение<select data-batch-disposition ${readOnly ? "disabled" : ""}><option value="pending">Не выбрано</option><option value="accept"${item.disposition === "accept" ? " selected" : ""}>Принять в черновики</option><option value="archive"${item.disposition === "archive" ? " selected" : ""}>В архив</option><option value="trash"${item.disposition === "trash" ? " selected" : ""}>В корзину</option></select></label><label>Комментарий<textarea rows="2" data-batch-note ${readOnly ? "readonly" : ""}>${escapeHtml(item.note || "")}</textarea></label>${warningInputs}${readOnly ? "" : '<button class="button button--primary button--compact" type="button" data-batch-item-save>Сохранить решение</button>'}</div></article>`; }).join("")}</div><div class="acceptance-footer-actions">${batch.status === "draft" && can("editor") ? '<button class="button button--primary" type="button" data-batch-submit>Отправить партию на утверждение</button>' : ""}${batch.status === "in_review" && can("publisher") ? '<button class="button button--primary" type="button" data-batch-finalize>Финализировать атомарно</button>' : ""}${!["finalized", "cancelled"].includes(batch.status) && can("publisher") ? '<button class="button button--danger" type="button" data-batch-cancel>Отменить партию</button>' : ""}</div></div>`;
  }

  async function openMigrationBatch(id) {
    renderMigrationBatch(await apiRequest(`/api/admin/migration/batches/${id}`));
  }

  async function saveMigrationBatchItem(button) {
    const article = button.closest("[data-batch-content]");
    const warning_acknowledgements = {};
    article.querySelectorAll("[data-batch-warning]").forEach(input => { if (input.value.trim()) warning_acknowledgements[input.dataset.batchWarning] = input.value.trim(); });
    const batch = state.migration.currentBatch;
    const updated = await apiRequest(`/api/admin/migration/batches/${batch.id}/items/${article.dataset.batchContent}`, { method: "PATCH", body: JSON.stringify({ version: Number(article.dataset.itemVersion), manual_reviewed: article.querySelector("[data-batch-reviewed]").checked, disposition: article.querySelector("[data-batch-disposition]").value, warning_acknowledgements, note: article.querySelector("[data-batch-note]").value }) });
    renderMigrationBatch(updated); toast("Решение сохранено");
  }

  async function migrationBatchAction(action) {
    const batch = state.migration.currentBatch;
    const payload = { version: batch.version };
    if (action === "finalize") {
      payload.warning_acknowledgements = {};
      panel.querySelectorAll("[data-batch-ack]").forEach(input => { if (input.value.trim()) payload.warning_acknowledgements[input.dataset.batchAck] = input.value.trim(); });
      if (!confirm("Применить все решения партии атомарно? Приёмка не публикует материалы.")) return;
    }
    renderMigrationBatch(await apiRequest(`/api/admin/migration/batches/${batch.id}/${action}`, { method: "POST", body: JSON.stringify(payload) }));
    toast(action === "finalize" ? "Партия финализирована; публикация выполняется отдельно" : "Состояние партии обновлено");
  }

  function showPanel(name) {
    document.querySelector("[data-editor-pane]").hidden = true;
    document.querySelector("[data-preview-pane]").hidden = true;
    panel.hidden = false;
    document.querySelectorAll("[data-content-type]").forEach(button => button.classList.remove("is-active"));
    document.querySelectorAll("[data-panel]").forEach(button => button.classList.toggle("is-active", button.dataset.panel === name));
    if (name === "workflow") renderWorkflowPanel();
    if (name === "submissions") renderSubmissionsPanel();
    if (name === "media") renderMediaPanel();
    if (name === "users") renderUsersPanel().catch(error => toast(error.message));
    if (name === "settings") panel.innerHTML = `<div class="eyebrow">Настройки</div><h1>Контентная схема ${escapeHtml(state.schema.schema_version)}</h1><p>Поля, роли, редакционный workflow и настройки медиатеки формируются сервером.</p>`;
    if (name === "migration") { renderMigrationDashboardShell(); refreshMigrationStatus().catch(error => toast(error.message)); }
  }

  function selectContentType(type) {
    state.currentType = type; state.current = null; state.dirty = false;
    document.querySelector("[data-editor-pane]").hidden = false; document.querySelector("[data-preview-pane]").hidden = false; panel.hidden = true;
    renderEditor(type, null); loadContentList().catch(error => toast(error.message));
    document.body.classList.remove("cms-menu-open");
  }

  function closeDialog(selector) { const dialog = document.querySelector(selector); if (dialog.open) dialog.close(); }

  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    const dialogs = [...document.querySelectorAll("dialog[open]")];
    const dialog = dialogs[dialogs.length - 1];
    if (!dialog) return;
    event.preventDefault();
    dialog.close();
    if (dialog.matches("[data-media-dialog]")) {
      state.media.chooser = null;
      state.media.selected.clear();
    }
  });

  function selectRange(range) {
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function insertAtRange(range, node) {
    range.deleteContents();
    range.insertNode(node);
    const selectionRange = document.createRange();
    selectionRange.selectNodeContents(node);
    selectRange(selectionRange);
  }

  function applyInlineCommand(command, range) {
    if (!range || range.collapsed) { toast("Сначала выделите текст"); return false; }
    const fragment = range.extractContents();
    if (command === "removeFormat") {
      insertAtRange(range, document.createTextNode(fragment.textContent || ""));
      return true;
    }
    const element = document.createElement(command === "bold" ? "strong" : "em");
    element.append(fragment);
    insertAtRange(range, element);
    return true;
  }

  document.addEventListener("click", async event => {
    const target = event.target.closest("button,a"); if (!target) return;
    if (target.dataset.contentType) selectContentType(target.dataset.contentType);
    if (target.dataset.panel) showPanel(target.dataset.panel);
    if (target.matches("[data-open-profile]")) document.querySelector("[data-password-dialog]").showModal();
    if (target.matches("[data-password-close]")) closeDialog("[data-password-dialog]");
    if (target.matches("[data-user-create-close]")) closeDialog("[data-user-create-dialog]");
    if (target.matches("[data-submission-close]")) closeDialog("[data-submission-dialog]");
    if (target.dataset.submissionOpen) await openSubmission(target.dataset.submissionOpen, target).catch(error => toast(error.message));
    if (target.matches("[data-submission-prev]")) { state.submissions.offset = Math.max(0, state.submissions.offset - 50); await loadSubmissions().catch(error => toast(error.message)); }
    if (target.matches("[data-submission-next]")) { state.submissions.offset += 50; await loadSubmissions().catch(error => toast(error.message)); }
    if (target.dataset.submissionStatusAction) await changeSubmissionStatus(target.dataset.submissionStatusAction).catch(error => toast(error.message));
    if (target.matches("[data-submission-retry]")) await retrySubmissionNotification().catch(error => toast(error.message));
    if (target.matches("[data-user-create]")) document.querySelector("[data-user-create-dialog]").showModal();
    if (target.matches("[data-logout]")) {
      try { await apiRequest("/api/admin/logout", { method: "POST" }); } catch (_) { /* The session may already be revoked. */ }
      state.user = null; state.csrf = ""; state.list = []; updateSessionUi(); renderEditor(); document.querySelector("[data-content-picker]").hidden = true; document.querySelector("[data-content-count]").textContent = "0 из 0"; document.querySelector("[data-save-status]").textContent = "Вход не выполнен"; document.querySelector("[data-login-dialog]").showModal(); toast("Сеанс завершён");
    }
    if (target.dataset.bulkAction) { state.bulk.action = target.dataset.bulkAction; state.bulk.offset = 0; state.bulk.selected.clear(); renderWorkflowPanel(); }
    if (target.matches("[data-bulk-select-all]")) { state.bulk.items.forEach(item => state.bulk.selected.add(item.id)); await loadBulkQueue().catch(error => toast(error.message)); }
    if (target.matches("[data-bulk-apply]")) await applyBulkAction().catch(error => toast(error.message));
    if (target.matches("[data-bulk-prev]")) { state.bulk.offset = Math.max(0, state.bulk.offset - 100); state.bulk.selected.clear(); await loadBulkQueue().catch(error => toast(error.message)); }
    if (target.matches("[data-bulk-next]")) { state.bulk.offset += 100; state.bulk.selected.clear(); await loadBulkQueue().catch(error => toast(error.message)); }
    if (target.dataset.bulkOpen) { selectContentType(target.dataset.bulkType); await openRecord(target.dataset.bulkOpen).catch(error => toast(error.message)); }
    if (target.matches("[data-user-save]")) {
      const row = target.closest("[data-user-id]");
      try {
        await apiRequest(`/api/admin/users/${row.dataset.userId}`, { method: "PATCH", body: JSON.stringify({ version: Number(row.dataset.version), role: row.querySelector("[data-user-role]").value, is_active: row.querySelector("[data-user-active]").checked }) });
        await renderUsersPanel(); toast("Учётная запись обновлена");
      } catch (error) { toast(error.message); }
    }
    if (target.matches("[data-user-terminate]")) {
      const row = target.closest("[data-user-id]");
      if (confirm("Завершить все активные сессии этого пользователя?")) {
        try { const result = await apiRequest(`/api/admin/users/${row.dataset.userId}/terminate-sessions`, { method: "POST", body: JSON.stringify({ version: Number(row.dataset.version) }) }); await renderUsersPanel(); toast(`Завершено сессий: ${result.closed_sessions}`); } catch (error) { toast(error.message); }
      }
    }
    if (target.matches("[data-create-current]")) { state.current = null; renderEditor(state.currentType, null); }
    if (target.matches("[data-cms-menu]")) document.body.classList.toggle("cms-menu-open");
    if (target.dataset.previewSize) applyPreviewSize(target.dataset.previewSize);
    if (target.matches("[data-save-draft]")) await saveDraft().catch(error => toast(error.message));
    if (target.matches("[data-audit-current]")) await auditCurrent().catch(error => toast(error.message));
    if (target.matches("[data-publish-close]")) closeDialog("[data-publish-dialog]");
    if (target.matches("[data-schedule-close]")) closeDialog("[data-schedule-dialog]");
    if (target.matches("[data-history-close]")) closeDialog("[data-history-dialog]");
    if (target.matches("[data-login-close]")) closeDialog("[data-login-dialog]");
    if (target.matches("[data-link-close]")) closeDialog("[data-link-dialog]");
    if (target.matches("[data-media-close]")) closeMediaDialog();
    if (target.matches("[data-media-detail-close]")) closeDialog("[data-media-detail-dialog]");
    if (target.matches("[data-media-prev]")) { state.media.offset = Math.max(0, state.media.offset - 48); await loadMediaDialog().catch(error => toast(error.message)); }
    if (target.matches("[data-media-next]")) { state.media.offset += 48; await loadMediaDialog().catch(error => toast(error.message)); }
    if (target.matches("[data-media-select]")) {
      const id = target.closest("[data-media-id]").dataset.mediaId;
      if (!state.media.chooser?.multiple) state.media.selected.clear();
      if (state.media.selected.has(id)) state.media.selected.delete(id); else state.media.selected.add(id);
      await loadMediaDialog().catch(error => toast(error.message));
    }
    if (target.matches("[data-media-use]")) {
      const items = [...state.media.selected].map(id => state.media.items.get(id)).filter(Boolean);
      state.media.chooser?.apply(items);
      closeMediaDialog();
    }
    if (target.matches("[data-media-open]")) await openMediaDetail(target.closest("[data-media-id]").dataset.mediaId).catch(error => toast(error.message));
    if (target.dataset.mediaTab) {
      state.media.panelTab = target.dataset.mediaTab; state.media.offset = 0;
      document.querySelectorAll("[data-media-tab]").forEach(button => button.classList.toggle("is-active", button === target));
      const toolbar = document.querySelector(".media-panel-toolbar"); if (toolbar) toolbar.hidden = state.media.panelTab === "issues";
      await loadMediaPanel().catch(error => toast(error.message));
    }
    if (target.matches("[data-panel-media-prev]")) { state.media.offset = Math.max(0, state.media.offset - 48); await loadMediaPanel().catch(error => toast(error.message)); }
    if (target.matches("[data-panel-media-next]")) { state.media.offset += 48; await loadMediaPanel().catch(error => toast(error.message)); }
    if (target.matches("[data-media-reindex]")) {
      target.disabled = true; target.textContent = "Проверяем…";
      try { const result = await apiRequest("/api/admin/media/reindex?dry_run=false", { method: "POST" }); toast(`Проверено файлов: ${result.files}`); await loadMediaPanel(); }
      catch (error) { toast(error.message); } finally { target.disabled = false; target.textContent = "Проверить индекс"; }
    }
    if (target.dataset.mediaSave) {
      try {
        const item = await apiRequest(`/api/admin/media/${target.dataset.mediaSave}`, { method: "PATCH", body: JSON.stringify({ version: Number(target.dataset.version), alt_text: document.querySelector("[data-media-alt]")?.value || "" }) });
        toast("Alt-текст сохранён"); closeDialog("[data-media-detail-dialog]"); state.media.items.set(item.id, item); await loadMediaPanel();
      } catch (error) { toast(error.message); }
    }
    if (target.dataset.mediaDelete && confirm("Удалить этот неиспользуемый файл без возможности восстановления?")) {
      try { await apiRequest(`/api/admin/media/${target.dataset.mediaDelete}?version=${target.dataset.version}`, { method: "DELETE" }); closeDialog("[data-media-detail-dialog]"); toast("Файл удалён"); await loadMediaPanel(); }
      catch (error) { toast(error.message); }
    }
    if (target.matches("[data-publish-confirm]")) {
      if ([...document.querySelectorAll("[data-publish-dialog] input")].some(input => !input.checked)) { toast("Подтвердите все пункты проверки"); return; }
      await publishCurrent().then(() => closeDialog("[data-publish-dialog]")).catch(error => toast(error.message));
    }
    if (target.dataset.workflowAction) {
      const action = target.dataset.workflowAction;
      try {
        if (action === "history") await openHistory();
        else if (action === "submit-review") await submitReview();
        else if (action === "publish") document.querySelector("[data-publish-dialog]").showModal();
        else if (action === "schedule") document.querySelector("[data-schedule-dialog]").showModal();
        else { if (["archive", "trash"].includes(action) && !confirm(action === "archive" ? "Переместить материал в архив?" : "Переместить материал в корзину?")) return; await postWorkflow(action); toast("Состояние материала обновлено"); }
      } catch (error) { toast(error.message); }
    }
    if (target.dataset.restoreRevision) await restoreRevision(Number(target.dataset.restoreRevision)).catch(error => toast(error.message));
    if (target.matches("[data-migration-run]")) { await apiRequest("/api/admin/migration/audits", { method: "POST", body: JSON.stringify({ check_external: true }) }); toast("Аудит поставлен в очередь"); setTimeout(() => refreshMigrationStatus().catch(error => toast(error.message)), 1200); }
    if (target.matches("[data-migration-pilot]")) { const batch = await apiRequest("/api/admin/migration/batches/pilot", { method: "POST" }); renderMigrationBatch(batch); }
    if (target.matches("[data-migration-filter]")) { state.migration.issuesOffset = 0; await refreshMigrationStatus().catch(error => toast(error.message)); }
    if (target.matches("[data-migration-issues-prev]")) { state.migration.issuesOffset = Math.max(0, state.migration.issuesOffset - 50); await refreshMigrationStatus().catch(error => toast(error.message)); }
    if (target.matches("[data-migration-issues-next]")) { state.migration.issuesOffset += 50; await refreshMigrationStatus().catch(error => toast(error.message)); }
    if (target.dataset.migrationBatch) await openMigrationBatch(target.dataset.migrationBatch).catch(error => toast(error.message));
    if (target.matches("[data-migration-back]")) { renderMigrationDashboardShell(); await refreshMigrationStatus().catch(error => toast(error.message)); }
    if (target.dataset.migrationOpenContent) { selectContentType(target.dataset.contentType); await openRecord(target.dataset.migrationOpenContent).catch(error => toast(error.message)); }
    if (target.matches("[data-batch-item-save]")) await saveMigrationBatchItem(target).catch(error => toast(error.message));
    if (target.matches("[data-batch-submit]")) await migrationBatchAction("submit").catch(error => toast(error.message));
    if (target.matches("[data-batch-finalize]")) await migrationBatchAction("finalize").catch(error => toast(error.message));
    if (target.matches("[data-batch-cancel]")) { if (confirm("Отменить редакционную партию? Материалы не изменятся.")) await migrationBatchAction("cancel").catch(error => toast(error.message)); }
  });

  let mediaSearchTimer;
  document.addEventListener("input", event => {
    if (!event.target.matches("[data-media-search],[data-panel-media-search]")) return;
    clearTimeout(mediaSearchTimer);
    mediaSearchTimer = setTimeout(() => {
      state.media.q = event.target.value.trim(); state.media.offset = 0;
      (event.target.matches("[data-media-search]") ? loadMediaDialog() : loadMediaPanel()).catch(error => toast(error.message));
    }, 250);
  });

  let bulkSearchTimer;
  document.addEventListener("input", event => {
    if (!event.target.matches("[data-bulk-search]")) return;
    clearTimeout(bulkSearchTimer);
    bulkSearchTimer = setTimeout(() => {
      state.bulk.q = event.target.value.trim(); state.bulk.offset = 0; state.bulk.selected.clear();
      loadBulkQueue().catch(error => toast(error.message));
    }, 250);
  });

  let submissionSearchTimer;
  document.addEventListener("input", event => {
    if (!event.target.matches("[data-submission-search]")) return;
    clearTimeout(submissionSearchTimer);
    submissionSearchTimer = setTimeout(() => {
      state.submissions.q = event.target.value.trim(); state.submissions.offset = 0;
      loadSubmissions().catch(error => toast(error.message));
    }, 250);
  });

  document.addEventListener("change", async event => {
    const input = event.target;
    try {
      if (input.matches("[data-bulk-check]")) { if (input.checked) state.bulk.selected.add(input.dataset.bulkCheck); else state.bulk.selected.delete(input.dataset.bulkCheck); await loadBulkQueue(); }
      if (input.matches("[data-submission-type]")) { state.submissions.type = input.value; state.submissions.offset = 0; await loadSubmissions(); }
      if (input.matches("[data-submission-status]")) { state.submissions.status = input.value; state.submissions.offset = 0; await loadSubmissions(); }
      if (input.matches("[data-media-kind]")) { state.media.kind = input.value; state.media.offset = 0; await loadMediaDialog(); }
      if (input.matches("[data-media-usage]")) { state.media.usage = input.value; state.media.offset = 0; await loadMediaDialog(); }
      if (input.matches("[data-panel-media-kind]")) { state.media.kind = input.value; state.media.offset = 0; await loadMediaPanel(); }
      if (input.matches("[data-panel-media-usage]")) { state.media.usage = input.value; state.media.offset = 0; await loadMediaPanel(); }
      if (input.matches("[data-library-upload]")) {
        const uploaded = await uploadFiles([...input.files]);
        uploaded.forEach(item => state.media.selected.add(item.id));
        await loadMediaDialog();
      }
      if (input.matches("[data-panel-media-upload]")) { await uploadFiles([...input.files]); await loadMediaPanel(); toast("Файлы добавлены в медиатеку"); }
      if (input.matches("[data-issue-upload]")) { await uploadFiles([...input.files], "", `/api/admin/media-issues/${input.dataset.issueUpload}/replacement`); await loadMediaPanel(); toast("Замена сохранена; откройте связанный материал и разместите её вручную"); }
      if (input.matches("[data-media-replace]")) { const [replacement] = await uploadFiles([...input.files], "", `/api/admin/media/${input.dataset.mediaReplace}/replacement`); closeDialog("[data-media-detail-dialog]"); await loadMediaPanel(); toast(`Создан новый файл ${replacement.original_name}`); }
    } catch (error) { toast(error.message); }
  });

  editorForm.addEventListener("input", event => {
    const wrapper = event.target.closest("[data-schema-field]");
    if (wrapper) markDirty(wrapper);
  });
  editorForm.addEventListener("change", event => {
    const wrapper = event.target.closest("[data-schema-field]");
    if (wrapper) markDirty(wrapper);
  });
  editorForm.addEventListener("paste", event => {
    if (!event.target.matches("[data-inline-editor]")) return;
    event.preventDefault();
    const selection = getSelection();
    if (!selection.rangeCount) return;
    const text = document.createTextNode(event.clipboardData.getData("text/plain"));
    const range = selection.getRangeAt(0);
    range.deleteContents();
    range.insertNode(text);
    range.setStartAfter(text);
    range.collapse(true);
    selectRange(range);
    markDirty(event.target.closest("[data-schema-field]"));
  });
  editorForm.addEventListener("mousedown", event => {
    const button = event.target.closest("[data-inline-command]");
    if (!button) return;
    const inline = button.closest(".inline-field").querySelector("[data-inline-editor]");
    const selection = getSelection();
    if (selection.rangeCount && inline.contains(selection.anchorNode) && inline.contains(selection.focusNode)) {
      button._inlineRange = selection.getRangeAt(0).cloneRange();
    }
    event.preventDefault();
  });
  editorForm.addEventListener("click", async event => {
    const target = event.target.closest("button,label"); if (!target) return;
    const wrapper = target.closest("[data-schema-field]");
    if (target.dataset.mediaChoose) {
      const kind = target.hasAttribute("data-media-kind") ? target.dataset.mediaKind : "image";
      const mode = target.dataset.mediaChoose;
      const multiple = ["image-list", "block-gallery"].includes(mode);
      openMediaChooser({ kind, multiple, apply: items => {
        if (!items.length) return;
        if (mode === "field") wrapper.querySelector('.media-field input[type="text"]').value = items[0].url;
        if (mode === "image-list") wrapper.querySelector("[data-image-list-items]").insertAdjacentHTML("beforeend", imageCards(items.map(item => ({ id: uuid(), image: item.url, alt: item.alt_text || "", caption: "" }))));
        if (mode === "block-gallery") target.closest("[data-block-id]").querySelector("[data-block-gallery]").insertAdjacentHTML("beforeend", imageCards(items.map(item => ({ id: uuid(), image: item.url, alt: item.alt_text || "", caption: "" }))));
        if (mode === "block-image" || mode === "block-file") {
          const card = target.closest("[data-block-id]");
          card.querySelector(`[data-block-value="${mode === "block-image" ? "image" : "url"}"]`).value = items[0].url;
          if (mode === "block-image" && !card.querySelector('[data-block-value="alt"]').value) card.querySelector('[data-block-value="alt"]').value = items[0].alt_text || "";
        }
        markDirty(wrapper);
      }});
      return;
    }
    if (target.dataset.addBlock) { const list = wrapper.querySelector("[data-block-list]"); list.insertAdjacentHTML("beforeend", blockCard(emptyBlock(target.dataset.addBlock))); markDirty(wrapper); }
    if (target.dataset.blockAction) {
      const card = target.closest("[data-block-id]"), list = card.parentElement;
      if (target.dataset.blockAction === "up" && card.previousElementSibling) list.insertBefore(card, card.previousElementSibling);
      if (target.dataset.blockAction === "down" && card.nextElementSibling) list.insertBefore(card.nextElementSibling, card);
      if (target.dataset.blockAction === "copy") { const copy = serializeBlock(card); copy.id = uuid(); if (copy.type === "gallery") copy.data.items.forEach(item => item.id = uuid()); card.insertAdjacentHTML("afterend", blockCard(copy)); }
      if (target.dataset.blockAction === "delete" && (!card.textContent.trim() || confirm("Удалить этот блок?"))) card.remove();
      markDirty(wrapper);
    }
    if (target.matches("[data-convert-legacy]")) {
      const card = target.closest("[data-block-id]"); if (!confirm("Разбить проверенный старый текст на абзацы? Исходник останется в предыдущей ревизии.")) return;
      const parts = card.querySelector("textarea").value.split(/\n\s*\n/).map(value => value.trim()).filter(Boolean);
      card.insertAdjacentHTML("beforebegin", parts.map(text => blockCard({ id: uuid(), type: "paragraph", data: { runs: [{ text, marks: [] }] } })).join("")); card.remove(); markDirty(wrapper);
    }
    if (target.matches("[data-list-add]")) { target.previousElementSibling.insertAdjacentHTML("beforeend", listItemMarkup({ runs: [] })); markDirty(wrapper); }
    if (target.matches("[data-list-remove]")) { target.closest("[data-list-item]").remove(); markDirty(wrapper); }
    if (target.dataset.inlineCommand) {
      const inline = target.closest(".inline-field").querySelector("[data-inline-editor]");
      const selection = getSelection();
      if (target._inlineRange) {
        selectRange(target._inlineRange);
      }
      if (target.dataset.inlineCommand === "link") { if (!selection.rangeCount || selection.isCollapsed) { toast("Сначала выделите текст"); return; } state.linkRange = selection.getRangeAt(0).cloneRange(); state.linkEditor = inline; document.querySelector("[data-link-dialog]").showModal(); return; }
      if (applyInlineCommand(target.dataset.inlineCommand, selection.rangeCount ? selection.getRangeAt(0) : null)) {
        inline.focus();
        markDirty(wrapper);
      }
    }
    if (target.matches("[data-schedule-add]")) { const rows = wrapper.querySelector("[data-schedule-rows]"); rows.insertAdjacentHTML("beforeend", '<div class="schedule-editor__row" data-schedule-row><label>День<select data-schedule-value="weekday"><option value="">По дате</option><option value="1">Понедельник</option><option value="2">Вторник</option><option value="3">Среда</option><option value="4">Четверг</option><option value="5">Пятница</option><option value="6">Суббота</option><option value="7">Воскресенье</option></select></label><label>Время<input type="time" data-schedule-value="time"></label><label>Название<input data-schedule-value="title"></label><label>Примечание<input data-schedule-value="note"></label><button class="icon-button" type="button" data-schedule-remove aria-label="Удалить строку">×</button></div>'); markDirty(wrapper); }
    if (target.matches("[data-schedule-remove]")) { target.closest("[data-schedule-row]").remove(); markDirty(wrapper); }
    if (target.dataset.imageAction) { const card = target.closest("[data-image-id]"), list = card.parentElement; if (target.dataset.imageAction === "up" && card.previousElementSibling) list.insertBefore(card, card.previousElementSibling); if (target.dataset.imageAction === "down" && card.nextElementSibling) list.insertBefore(card.nextElementSibling, card); if (target.dataset.imageAction === "delete" && confirm("Удалить фотографию из материала?")) card.remove(); markDirty(wrapper); }
    if (target.dataset.relationRemove) { const editor = target.closest("[data-relation-editor]"); editor._selected = editor._selected.filter(id => id !== target.dataset.relationRemove); renderRelationSelected(editor); markDirty(wrapper); }
    if (target.dataset.relationAdd) { const editor = target.closest("[data-relation-editor]"); if (editor.dataset.single === "true") editor._selected = [target.dataset.relationAdd]; else if (!editor._selected.includes(target.dataset.relationAdd) && editor._selected.length < 20) editor._selected.push(target.dataset.relationAdd); renderRelationSelected(editor); target.remove(); markDirty(wrapper); }
  });

  editorForm.addEventListener("change", async event => {
    const input = event.target, wrapper = input.closest("[data-schema-field]");
    try {
      if (input.matches("[data-media-upload]")) { const [uploaded] = await uploadFiles([...input.files]); wrapper.querySelector('input[type="text"]').value = uploaded.url; markDirty(wrapper); }
      if (input.matches("[data-image-list-upload]")) { const uploaded = await uploadFiles([...input.files]); const container = wrapper.querySelector("[data-image-list-items]"); container.insertAdjacentHTML("beforeend", imageCards(uploaded.map(item => ({ id: uuid(), image: item.url, alt: "", caption: "" })))); markDirty(wrapper); }
      if (input.matches("[data-block-upload]")) { const card = input.closest("[data-block-id]"), uploaded = await uploadFiles([...input.files]); if (input.dataset.blockUpload === "gallery") card.querySelector("[data-block-gallery]").insertAdjacentHTML("beforeend", imageCards(uploaded.map(item => ({ id: uuid(), image: item.url, alt: "", caption: "" })))); else card.querySelector(`[data-block-value="${input.dataset.blockUpload === "image" ? "image" : "url"}"]`).value = uploaded[0].url; markDirty(wrapper); }
    } catch (error) { toast(error.message); }
  });

  let relationTimer;
  editorForm.addEventListener("input", event => { if (event.target.matches("[data-relation-search]")) { clearTimeout(relationTimer); relationTimer = setTimeout(() => searchRelations(event.target.closest("[data-relation-editor]")).catch(error => toast(error.message)), 250); } });
  editorForm.addEventListener("focusin", event => { if (event.target.matches("[data-relation-search]")) searchRelations(event.target.closest("[data-relation-editor]")).catch(error => toast(error.message)); });

  editorForm.addEventListener("dragstart", event => { const card = event.target.closest("[data-block-id]"); if (card) { event.dataTransfer.setData("text/plain", card.dataset.blockId); card.classList.add("is-dragging"); } });
  editorForm.addEventListener("dragend", event => event.target.closest("[data-block-id]")?.classList.remove("is-dragging"));
  editorForm.addEventListener("dragover", event => { if (event.target.closest("[data-block-list]")) event.preventDefault(); });
  editorForm.addEventListener("drop", event => { const list = event.target.closest("[data-block-list]"); if (!list) return; event.preventDefault(); const id = event.dataTransfer.getData("text/plain"), dragged = list.querySelector(`[data-block-id="${CSS.escape(id)}"]`), target = event.target.closest("[data-block-id]"); if (dragged && target && dragged !== target) list.insertBefore(dragged, target); markDirty(list.closest("[data-schema-field]")); });

  document.querySelector("[data-link-form]").addEventListener("submit", event => {
    event.preventDefault(); const href = new FormData(event.currentTarget).get("href").trim();
    if (!/^(?:\/(?!\/)|https:\/\/|mailto:|tel:)/.test(href)) { toast("Разрешены /, https://, mailto: и tel:"); return; }
    if (!state.linkRange || state.linkRange.collapsed) { toast("Выделение текста потеряно"); return; }
    const fragment = state.linkRange.extractContents();
    const link = document.createElement("a");
    link.href = href;
    link.append(fragment);
    insertAtRange(state.linkRange, link);
    state.linkEditor.focus(); markDirty(state.linkEditor.closest("[data-schema-field]")); event.currentTarget.reset(); closeDialog("[data-link-dialog]");
  });

  document.querySelector("[data-schedule-form]").addEventListener("submit", async event => { event.preventDefault(); const value = new FormData(event.currentTarget).get("scheduled_at"); try { await postWorkflow("schedule", { scheduled_at: new Date(value).toISOString() }); closeDialog("[data-schedule-dialog]"); toast("Публикация запланирована"); } catch (error) { toast(error.message); } });
  document.querySelector("[data-content-select]").addEventListener("change", event => openRecord(event.target.value).catch(error => toast(error.message)));
  let searchTimer;
  document.querySelector("[data-content-search]").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadContentList().catch(error => toast(error.message)), 220); });
  document.querySelector("[data-review-only]").addEventListener("change", () => loadContentList().catch(error => toast(error.message)));
  document.querySelector("[data-login-form]").addEventListener("submit", async event => { event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); try { const session = await apiRequest("/api/admin/login", { method: "POST", body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) }); state.user = session.user; state.csrf = session.csrf_token; formElement.querySelector('[name="password"]').value = ""; closeDialog("[data-login-dialog]"); document.querySelector("[data-save-status]").textContent = "CMS подключена"; updateSessionUi(); renderEditor(); await loadContentList(); } catch (error) { console.error("CMS login initialization failed", error); document.querySelector("[data-login-error]").textContent = error.message; } });
  document.querySelector("[data-password-form]").addEventListener("submit", async event => {
    event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); const error = document.querySelector("[data-password-error]"); error.textContent = "";
    if (form.get("new_password") !== form.get("confirm_password")) { error.textContent = "Новые пароли не совпадают"; return; }
    try {
      await apiRequest("/api/admin/change-password", { method: "POST", body: JSON.stringify({ current_password: form.get("current_password"), new_password: form.get("new_password") }) });
      formElement.reset(); closeDialog("[data-password-dialog]"); state.user = null; state.csrf = ""; state.list = []; updateSessionUi(); renderEditor(); document.querySelector("[data-content-picker]").hidden = true; document.querySelector("[data-content-count]").textContent = "0 из 0"; document.querySelector("[data-save-status]").textContent = "Вход не выполнен"; document.querySelector("[data-login-dialog]").showModal(); toast("Пароль изменён. Войдите заново");
    } catch (requestError) { error.textContent = requestError.message; }
  });
  document.querySelector("[data-user-create-form]").addEventListener("submit", async event => {
    event.preventDefault(); const formElement = event.currentTarget; const form = new FormData(formElement); const error = document.querySelector("[data-user-create-error]"); error.textContent = "";
    try {
      await apiRequest("/api/admin/users", { method: "POST", body: JSON.stringify({ username: form.get("username"), password: form.get("password"), role: form.get("role") }) });
      formElement.reset(); closeDialog("[data-user-create-dialog]"); await renderUsersPanel(); toast("Пользователь создан");
    } catch (requestError) { error.textContent = requestError.message; }
  });
  document.querySelector("[data-media-dialog]").addEventListener("close", () => {
    state.media.chooser = null;
    state.media.selected.clear();
  });
  const submissionDialog = document.querySelector("[data-submission-dialog]");
  submissionDialog.addEventListener("click", event => {
    if (event.target === submissionDialog) submissionDialog.close();
  });
  submissionDialog.addEventListener("close", () => {
    submissionOpener?.focus();
    submissionOpener = null;
  });
  for (const eventName of ["dragenter", "dragover"]) {
    document.addEventListener(eventName, event => {
      const zone = event.target.closest?.("[data-media-dropzone]");
      if (!zone || !can("editor") || !event.dataTransfer?.types.includes("Files")) return;
      event.preventDefault();
      zone.classList.add("is-dragover");
    });
  }
  document.addEventListener("dragleave", event => {
    const zone = event.target.closest?.("[data-media-dropzone]");
    if (zone && !zone.contains(event.relatedTarget)) zone.classList.remove("is-dragover");
  });
  document.addEventListener("drop", async event => {
    const zone = event.target.closest?.("[data-media-dropzone]");
    if (!zone || !can("editor")) return;
    event.preventDefault();
    zone.classList.remove("is-dragover");
    const files = [...(event.dataTransfer?.files || [])];
    if (!files.length) return;
    try {
      await uploadFiles(files);
      await loadMediaPanel();
      toast("Файлы добавлены в медиатеку");
    } catch (error) { toast(error.message); }
  });
  window.addEventListener("resize", () => applyPreviewSize());

  async function initialize() {
    state.schema = await fetch("/cms-schema.json").then(response => response.json());
    renderNavigation();
    applyPreviewSize();
    const session = await apiRequest("/api/admin/session");
    if (!session.authenticated) { updateSessionUi(); document.querySelector("[data-login-dialog]").showModal(); renderEditor(); return; }
    state.user = session.user; state.csrf = session.csrf_token;
    updateSessionUi();
    await refreshSubmissionBadge();
    document.querySelector("[data-save-status]").textContent = `CMS подключена · схема ${state.schema.schema_version}`;
    renderEditor(); await loadContentList();
    const linkedContent = new URLSearchParams(location.search).get("content");
    if (linkedContent) await openRecord(linkedContent);
  }

  initialize().catch(error => { document.querySelector("[data-save-status]").textContent = "Ошибка подключения"; toast(error.message); });
})();
