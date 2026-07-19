(() => {
  "use strict";

  const editorForm = document.querySelector("[data-editor-form]");
  const previewFrame = document.querySelector("[data-content-preview]");
  const panel = document.querySelector("[data-cms-panel]");
  const roleLevel = { viewer: 0, editor: 1, publisher: 2, admin: 3 };
  const statusLabels = { draft: "Черновик", in_review: "На проверке", scheduled: "Запланирован", published: "Опубликован", archived: "В архиве", trash: "В корзине" };
  const auditLabels = { create: "Материал создан", update: "Содержимое сохранено", import_create: "Материал импортирован", import_update: "Импортированный материал обновлён", migration_review: "Импортированный материал проверен", submit_review: "Отправлен на проверку", return_to_draft: "Возвращён в черновики", publish: "Опубликован", schedule: "Публикация запланирована", scheduled_publish: "Опубликован по расписанию", archive: "Перемещён в архив", trash: "Перемещён в корзину", restore: "Восстановлен как черновик", restore_revision: "Восстановлена историческая версия" };
  const state = { schema: null, currentType: "news", current: null, list: [], user: null, csrf: "", dirty: false, previewTimer: null, previewAbort: null, previewSize: "desktop", linkRange: null, linkEditor: null };

  const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  const uuid = () => globalThis.crypto?.randomUUID?.() || `block-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
  const can = role => Boolean(state.user) && roleLevel[state.user.role] >= roleLevel[role];
  const definition = () => state.schema.content_types[state.currentType];

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
    image: (name, field, value) => mediaField(name, field, value, "image/*"),
    file: (name, field, value) => mediaField(name, field, value, "application/pdf,video/mp4"),
    media: (name, field, value) => mediaField(name, field, value, field.accept || "image/*,application/pdf,video/mp4"),
    schedule: (name, field, value) => fieldShell(name, field, `<div class="schedule-editor" data-schedule-editor><div class="schedule-editor__rows" data-schedule-rows></div><button class="button button--ghost button--compact" type="button" data-schedule-add>Добавить строку</button></div>`),
    blocks: (name, field) => fieldShell(name, field, `<div class="block-editor" data-block-editor><div class="block-list" data-block-list></div><div class="block-palette" data-block-palette></div></div>`),
    relation_list: (name, field) => relationField(name, field, false),
    "relation-list": (name, field) => relationField(name, field, false),
    relation: (name, field) => relationField(name, field, true),
    image_list: (name, field) => fieldShell(name, field, `<div class="image-list-editor" data-image-list><div class="image-list-editor__items" data-image-list-items></div><label class="button button--ghost button--compact">Загрузить фотографии<input class="cms-file-input" type="file" accept="image/*" multiple data-image-list-upload></label></div>`),
    social_links: (name, field, value) => fieldShell(name, field, `<textarea name="${escapeHtml(name)}" rows="5" placeholder="Одна HTTPS-ссылка на строку">${escapeHtml((value || []).map(item => item.url || item).join("\n"))}</textarea>`),
  };

  function mediaField(name, field, value, accept) {
    return fieldShell(name, field, `<div class="media-field"><input name="${escapeHtml(name)}" type="text" value="${escapeHtml(value || "")}" readonly><label class="button button--ghost button--compact">Загрузить<input class="cms-file-input" type="file" accept="${accept}" data-media-upload></label></div>`);
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
    else if (block.type === "gallery") body = `<div class="block-gallery" data-block-gallery>${imageCards(block.data.items)}</div><label class="button button--ghost button--compact">Загрузить фотографии<input class="cms-file-input" type="file" accept="image/*" multiple data-block-upload="gallery"></label>`;
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
    const accept = kind === "image" ? "image/*" : "application/pdf,video/mp4";
    return `<div class="media-field"><input type="text" data-block-value="${key}" value="${escapeHtml(data[key] || "")}" readonly><label class="button button--ghost button--compact">Загрузить<input class="cms-file-input" type="file" accept="${accept}" data-block-upload="${kind}"></label></div>${kind === "image" ? `<label class="field">Alt-текст<input data-block-value="alt" value="${escapeHtml(data.alt || "")}"></label><label class="field">Подпись<input data-block-value="caption" value="${escapeHtml(data.caption || "")}"></label>` : ""}`;
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
    const button = can("editor") ? '<button class="button button--primary button--compact" type="button" data-mark-reviewed>Сохранить и отметить проверенным</button>' : "";
    document.querySelector(".editor-head").insertAdjacentHTML("afterend", `<div class="migration-warning" data-migration-warning><strong>Черновик перенесён со старого сайта</strong><span>Сравните содержимое и явно преобразуйте legacy-текст только после проверки.</span><div class="migration-warning__actions">${button}${legacyLink}</div></div>`);
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
    state.dirty = false;
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

  async function uploadFiles(files, alt = "") {
    const uploaded = [];
    for (const file of files) {
      const form = new FormData(); form.append("file", file); form.append("alt_text", alt);
      uploaded.push(await apiRequest("/api/admin/media", { method: "POST", body: form }));
    }
    return uploaded;
  }

  async function postWorkflow(action, payload = {}) {
    if (!state.current) throw new Error("Сначала сохраните материал");
    state.current = await apiRequest(`/api/admin/contents/${state.current.id}/${action}`, { method: "POST", body: JSON.stringify({ version: state.current.version, ...payload }) });
    state.dirty = false;
    renderEditor(state.currentType, state.current);
    await loadContentList();
  }

  async function markReviewed() {
    const saved = state.dirty ? await saveDraft() : state.current;
    state.current = await apiRequest(`/api/admin/contents/${saved.id}/review`, { method: "POST", body: JSON.stringify({ version: saved.version }) });
    renderEditor(state.currentType, state.current); await loadContentList(); toast("Материал отмечен проверенным");
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

  function showPanel(name) {
    document.querySelector("[data-editor-pane]").hidden = true;
    document.querySelector("[data-preview-pane]").hidden = true;
    panel.hidden = false;
    document.querySelectorAll("[data-content-type]").forEach(button => button.classList.remove("is-active"));
    document.querySelectorAll("[data-panel]").forEach(button => button.classList.toggle("is-active", button.dataset.panel === name));
    if (name === "media") panel.innerHTML = '<div class="eyebrow">Следующий этап</div><h1>Медиатека</h1><p>На этапе 5 файлы загружаются непосредственно из полей и блоков. Повторный выбор и управление файлами появятся на этапе 6.</p>';
    if (name === "settings") panel.innerHTML = '<div class="eyebrow">Настройки</div><h1>Контентная схема 1.2</h1><p>Поля и типы материалов формируются из единой серверной схемы. Управление пользователями остаётся этапу 7.</p>';
    if (name === "migration") { panel.innerHTML = '<div class="eyebrow">Редакторская приёмка</div><h1>Перенесённые материалы</h1><section class="review-dashboard" data-review-dashboard><p class="review-summary" data-review-summary>Загружаем прогресс…</p><div class="review-progress"><span data-review-progress></span></div><div class="review-types" data-review-types></div><button class="button button--primary" type="button" data-review-start>Начать проверку</button></section>'; refreshMigrationStatus().catch(error => toast(error.message)); }
  }

  async function refreshMigrationStatus() {
    const result = await apiRequest("/api/admin/migration");
    const total = Number(result.totals.contents || 0), remaining = Number(result.totals.review_required || 0), reviewed = total - remaining, percent = total ? Math.round(reviewed / total * 100) : 0;
    document.querySelector("[data-review-summary]").textContent = `Проверено ${reviewed.toLocaleString("ru-RU")} из ${total.toLocaleString("ru-RU")} · осталось ${remaining.toLocaleString("ru-RU")} · ${percent}%`;
    document.querySelector("[data-review-progress]").style.width = `${percent}%`;
    document.querySelector("[data-review-types]").innerHTML = Object.entries(result.review_by_type || {}).map(([type, counts]) => `<span class="review-type"><b>${escapeHtml(state.schema.content_types[type]?.label || type)}</b> · ${counts.reviewed}/${counts.total}</span>`).join("");
  }

  function selectContentType(type) {
    state.currentType = type; state.current = null; state.dirty = false;
    document.querySelector("[data-editor-pane]").hidden = false; document.querySelector("[data-preview-pane]").hidden = false; panel.hidden = true;
    renderEditor(type, null); loadContentList().catch(error => toast(error.message));
    document.body.classList.remove("cms-menu-open");
  }

  function closeDialog(selector) { const dialog = document.querySelector(selector); if (dialog.open) dialog.close(); }

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
    if (target.matches("[data-create-current]")) { state.current = null; renderEditor(state.currentType, null); }
    if (target.matches("[data-cms-menu]")) document.body.classList.toggle("cms-menu-open");
    if (target.dataset.previewSize) applyPreviewSize(target.dataset.previewSize);
    if (target.matches("[data-save-draft]")) await saveDraft().catch(error => toast(error.message));
    if (target.matches("[data-mark-reviewed]")) await markReviewed().catch(error => toast(error.message));
    if (target.matches("[data-publish-close]")) closeDialog("[data-publish-dialog]");
    if (target.matches("[data-schedule-close]")) closeDialog("[data-schedule-dialog]");
    if (target.matches("[data-history-close]")) closeDialog("[data-history-dialog]");
    if (target.matches("[data-login-close]")) closeDialog("[data-login-dialog]");
    if (target.matches("[data-link-close]")) closeDialog("[data-link-dialog]");
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
    if (target.matches("[data-review-start]")) { const result = await apiRequest("/api/admin/migration"); const next = Object.entries(result.review_by_type || {}).find(([, counts]) => counts.review_required > 0); if (next) selectContentType(next[0]); }
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
  document.querySelector("[data-login-form]").addEventListener("submit", async event => { event.preventDefault(); const form = new FormData(event.currentTarget); try { const session = await apiRequest("/api/admin/login", { method: "POST", body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) }); state.user = session.user; state.csrf = session.csrf_token; closeDialog("[data-login-dialog]"); document.querySelector("[data-save-status]").textContent = "CMS подключена"; document.querySelector(".cms-user strong").textContent = state.user.username; document.querySelector(".cms-user small").textContent = state.user.role; renderEditor(); await loadContentList(); } catch (error) { document.querySelector("[data-login-error]").textContent = error.message; } });
  window.addEventListener("resize", () => applyPreviewSize());

  async function initialize() {
    state.schema = await fetch("/cms-schema.json").then(response => response.json());
    renderNavigation();
    applyPreviewSize();
    const session = await apiRequest("/api/admin/session");
    if (!session.authenticated) { document.querySelector("[data-login-dialog]").showModal(); renderEditor(); return; }
    state.user = session.user; state.csrf = session.csrf_token;
    document.querySelector(".cms-user strong").textContent = state.user.username; document.querySelector(".cms-user small").textContent = state.user.role;
    document.querySelector("[data-save-status]").textContent = `CMS подключена · схема ${state.schema.schema_version}`;
    renderEditor(); await loadContentList();
  }

  initialize().catch(error => { document.querySelector("[data-save-status]").textContent = "Ошибка подключения"; toast(error.message); });
})();
