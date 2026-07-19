const typeDefinitions = {
  home_feature: {
    label: "Главное", title: "Новый материал рубрики «Главное»", help: "Самостоятельный редакционный материал для первого экрана. Он не зависит от даты публикации новостей.",
    fields: [
      { name: "title", label: "Заголовок", type: "text", required: true, value: "" },
      { name: "kicker", label: "Надзаголовок", type: "text", required: true, value: "Главное" },
      { name: "summary", label: "Краткое описание", type: "textarea", required: true, value: "" },
      { name: "ctaLabel", label: "Текст кнопки", type: "text", required: true, value: "Прочитать" },
      { name: "image", label: "Главное изображение", type: "image", value: "" },
      { name: "alt", label: "Описание изображения", type: "text", required: true, value: "" },
      { name: "targetUrl", label: "Ссылка кнопки", type: "text", value: "/schedule", help: "Внутренняя ссылка начинается с /, внешняя — с https://" },
      { name: "contentSlug", label: "Связанный материал", type: "text", value: "" },
      { name: "startDate", label: "Показывать с", type: "datetime-local", value: "" },
      { name: "endDate", label: "Показывать до", type: "datetime-local", value: "" },
      { name: "priority", label: "Приоритет", type: "number", value: "100" },
    ],
  },
  news: {
    label: "Новости и анонсы", title: "Новая новость", help: "Материал появится в выбранной рубрике и, при необходимости, на главной странице.",
    fields: [
      { name: "title", label: "Заголовок", type: "text", required: true, value: "" },
      { name: "date", label: "Дата события", type: "date", value: "" },
      { name: "category", label: "Рубрика", type: "select", options: ["Новости прихода","Воскресная школа","Социальная служба","Молодёжное движение"], value: "Новости прихода" },
      { name: "summary", label: "Краткое описание", type: "textarea", value: "" },
      { name: "body", label: "Полный текст", type: "textarea", value: "", help: "Абзацы разделяются пустой строкой. Сложные импортированные блоки сохраняются." },
      { name: "relatedSection", label: "Направление прихода", type: "relation", value: "", help: "Необязательно: связывает новость с направлением прихода." },
      { name: "image", label: "Главное изображение", type: "image", value: "" },
      { name: "alt", label: "Описание изображения", type: "text", value: "", help: "Нужно для доступности сайта." },
      { name: "featured", label: "Показывать на главной странице", type: "checkbox", value: false },
    ],
  },
  service: {
    label: "Богослужения", title: "Новое богослужение", help: "Структурированная запись автоматически попадёт в расписание и блок ближайшей службы.",
    fields: [
      { name: "title", label: "Название службы", type: "text", required: true, value: "" },
      { name: "date", label: "Дата", type: "date", required: true, value: "" },
      { name: "time", label: "Время", type: "time", required: true, value: "" },
      { name: "serviceType", label: "Тип", type: "select", options: [
        { value: "liturgy", label: "Литургия" }, { value: "vigil", label: "Всенощное бдение" },
        { value: "vespers", label: "Вечерня" }, { value: "matins", label: "Утреня" },
        { value: "moleben", label: "Молебен" }, { value: "panikhida", label: "Панихида" },
        { value: "confession", label: "Исповедь" }, { value: "other", label: "Другое" },
      ], value: "liturgy" },
      { name: "summary", label: "Примечание", type: "textarea", value: "" },
      { name: "featured", label: "Показывать как ближайшую службу", type: "checkbox", value: false },
    ],
  },
  gallery: {
    label: "Фотоальбомы", title: "Новый фотоальбом", help: "Загрузите обложку и фотографии — миниатюры будут созданы автоматически.",
    fields: [
      { name: "title", label: "Название альбома", type: "text", required: true, value: "" },
      { name: "date", label: "Дата события", type: "date", value: "" },
      { name: "category", label: "Раздел", type: "select", options: ["Богослужения","Воскресная школа","Жизнь прихода","Паломничества"], value: "Богослужения" },
      { name: "summary", label: "Описание", type: "textarea", value: "" },
      { name: "relatedSection", label: "Направление прихода", type: "relation", value: "" },
      { name: "image", label: "Обложка альбома", type: "image", value: "" },
      { name: "photos", label: "Фотографии", type: "multiimage", value: "" },
    ],
  },
  leaflet: {
    label: "Выпуски листка", title: "Новый выпуск листка", help: "Номер, период, обложка и PDF сохраняются отдельными полями.",
    fields: [
      { name: "title", label: "Название выпуска", type: "text", value: "" },
      { name: "number", label: "Номер", type: "number", value: "" },
      { name: "period", label: "Период выпуска", type: "text", value: "" },
      { name: "date", label: "Дата публикации", type: "date", value: "" },
      { name: "image", label: "Обложка", type: "image", value: "" },
      { name: "pdf", label: "PDF-файл", type: "file", value: "" },
      { name: "featured", label: "Показывать последним выпуском", type: "checkbox", value: false },
    ],
  },
  section: {
    label: "Направления прихода", title: "Новое направление", help: "Одна карточка связывает описание, контакты, новости и фотоальбомы направления.",
    fields: [
      { name: "title", label: "Название", type: "text", value: "" },
      { name: "summary", label: "Краткое описание", type: "textarea", value: "" },
      { name: "body", label: "Полное описание", type: "textarea", value: "" },
      { name: "contact", label: "Контактное лицо", type: "text", value: "" },
      { name: "phone", label: "Телефон", type: "tel", value: "" },
      { name: "email", label: "Электронная почта", type: "email", value: "" },
      { name: "schedule", label: "Регулярное расписание", type: "schedule", value: "[]" },
      { name: "image", label: "Обложка", type: "image", value: "" },
      { name: "order", label: "Порядок на странице", type: "number", value: "100" },
    ],
  },
  page: {
    label: "Страницы", title: "Новая страница", help: "Страница публикуется отдельно или занимает одно из специальных мест публичного сайта.",
    fields: [
      { name: "title", label: "Заголовок", type: "text", value: "" },
      { name: "summary", label: "Вводный текст", type: "textarea", value: "" },
      { name: "placement", label: "Размещение", type: "select", options: [
        { value: "standalone", label: "Отдельная страница" },
        { value: "about_history", label: "О храме — история" },
        { value: "about_shrine", label: "О храме — святыня" },
        { value: "school_home", label: "Воскресная школа" },
        { value: "schedule_info", label: "Расписание — пояснение" },
      ], value: "standalone" },
      { name: "body", label: "Полный текст", type: "textarea", value: "", help: "Абзацы разделяются пустой строкой. Блочный редактор появится на этапе 5." },
      { name: "image", label: "Главное изображение", type: "image", value: "" },
      { name: "alt", label: "Описание изображения", type: "text", value: "" },
      { name: "pdf", label: "PDF-файл", type: "file", value: "" },
      { name: "schedule", label: "Регулярное расписание", type: "schedule", value: "[]" },
      { name: "order", label: "Порядок в навигации", type: "number", value: "100" },
    ],
  },
  clergy: {
    label: "Духовенство", title: "Новый священнослужитель", help: "Имя, сан, служение и биография собраны в понятной карточке.",
    fields: [
      { name: "title", label: "Имя", type: "text", value: "" },
      { name: "rank", label: "Сан", type: "text", value: "" },
      { name: "position", label: "Служение", type: "text", value: "" },
      { name: "nameDay", label: "День тезоименитства", type: "text", value: "" },
      { name: "image", label: "Фотография", type: "image", value: "" },
      { name: "biography", label: "Биография", type: "textarea", value: "" },
      { name: "order", label: "Порядок на странице", type: "number", value: "100" },
    ],
  },
  video: {
    label: "Видео и трансляции", title: "Новое видео", help: "Добавьте ссылку на трансляцию или запись — код вставки будет создан автоматически.",
    fields: [
      { name: "title", label: "Название", type: "text", value: "" },
      { name: "date", label: "Дата публикации", type: "date", value: "" },
      { name: "externalUrl", label: "Ссылка на видео", type: "url", value: "" },
      { name: "category", label: "Раздел", type: "select", options: ["Трансляции","Беседы","Жизнь прихода"], value: "Трансляции" },
      { name: "image", label: "Обложка", type: "image", value: "" },
      { name: "isLive", label: "Сейчас идёт прямой эфир", type: "checkbox", value: false },
    ],
  },
  contact: {
    label: "Контакты и соцсети", title: "Контакты храма", help: "Единое место для адреса, часов работы, реквизитов и ссылок на социальные сети.",
    fields: [
      { name: "title", label: "Название карточки", type: "text", value: "Контакты храма" },
      { name: "address", label: "Адрес", type: "text", value: "Москва, Бескудниковский бульвар, 1" },
      { name: "metro", label: "Метро и ориентир", type: "text", value: "" },
      { name: "phone", label: "Телефон", type: "tel", value: "+7 (499) 480-09-89" },
      { name: "email", label: "Электронная почта", type: "email", value: "svtinnokentiy2025@yandex.ru" },
      { name: "openingHours", label: "Часы работы", type: "textarea", value: "Ежедневно; в будни с 7:00, в воскресные и праздничные дни с 6:00." },
      { name: "mapCoordinates", label: "Координаты на карте", type: "text", value: "55.8706, 37.5597" },
      { name: "legalDetails", label: "Реквизиты", type: "textarea", value: "" },
      { name: "socialLinks", label: "Социальные сети — одна ссылка на строку", type: "textarea", value: "https://t.me/sv_innokenty\nhttps://vk.com/club37731945" },
    ],
  },
};

const editorForm = document.querySelector("[data-editor-form]");
const preview = document.querySelector("[data-content-preview]");
const panel = document.querySelector("[data-cms-panel]");
let currentType = "news";
const apiState = { available: false, csrf: "", user: null, current: null, list: [], dirty: false };
const serverTypes = { leaflet: "leaflet_issue", section: "parish_section", contact: "site_contact" };
const uiTypes = { leaflet_issue: "leaflet", parish_section: "section", site_contact: "contact" };
const roleLevel = { viewer: 0, editor: 1, publisher: 2, admin: 3 };
const statusLabels = { draft: "Черновик", in_review: "На проверке", scheduled: "Запланирован", published: "Опубликован", archived: "В архиве", trash: "В корзине" };
const auditLabels = { create: "Материал создан", update: "Содержимое сохранено", import_create: "Материал импортирован", import_update: "Импортированный материал обновлён", migration_review: "Импортированный материал проверен", submit_review: "Отправлен на проверку", return_to_draft: "Возвращён в черновики", publish: "Опубликован", schedule: "Публикация запланирована", scheduled_publish: "Опубликован по расписанию", archive: "Перемещён в архив", trash: "Перемещён в корзину", restore: "Восстановлен как черновик", restore_revision: "Восстановлена историческая версия" };

function serverType(type = currentType) { return serverTypes[type] || type; }
function escapeCms(value = "") { return String(value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]); }
function safeCmsMedia(value = "") { return /^(?:assets\/|\/media\/|https:\/\/)/.test(value) ? escapeCms(value) : ""; }
function can(role) { return Boolean(apiState.user) && roleLevel[apiState.user.role] >= roleLevel[role]; }
function formatCmsDate(value, withTime = true) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString("ru-RU", withTime ? { dateStyle: "medium", timeStyle: "short" } : { dateStyle: "medium" });
}

async function apiRequest(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (apiState.csrf && !["GET", "HEAD"].includes(options.method || "GET")) headers["X-CSRF-Token"] = apiState.csrf;
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : detail?.message || `Ошибка CMS (${response.status})`;
    const fields = detail?.fields?.length ? `: ${detail.fields.join(", ")}` : "";
    throw new Error(message + fields);
  }
  return body;
}

function apiData(values) {
  const existing = apiState.current?.data || {};
  let changed;
  if (currentType === "home_feature") changed = { kicker: values.kicker, summary: values.summary, cover: values.image, cover_alt: values.alt, cta_label: values.ctaLabel, target_url: values.targetUrl, content_slug: values.contentSlug, starts_at: values.startDate, ends_at: values.endDate, priority: Number(values.priority || 100) };
  else if (currentType === "news") changed = { publication_date: values.date, category: values.category, summary: values.summary, body: bodyFromText(values.body, existing.body), related_section: values.relatedSection || null, cover: values.image, cover_alt: values.alt, featured: values.featured };
  else if (currentType === "service") changed = { starts_at: `${values.date}T${values.time || "00:00"}:00+03:00`, service_type: values.serviceType, location: existing.location || "Храм святителя Иннокентия", note: values.summary, featured: values.featured };
  else if (currentType === "gallery") changed = { event_date: values.date, category: values.category, summary: values.summary, related_section: values.relatedSection || null, cover: values.image, photos: parsePhotoList(values.photos) };
  else if (currentType === "leaflet") changed = { number: Number(values.number), period: values.period, publication_date: values.date, cover: values.image, pdf: values.pdf, featured: values.featured };
  else if (currentType === "section") changed = { summary: values.summary, body: bodyFromText(values.body, existing.body), contact_name: values.contact, contact: values.contact, phone: values.phone, email: values.email, schedule: parseSchedule(values.schedule), cover: values.image, order: Number(values.order || 100) };
  else if (currentType === "page") changed = { summary: values.summary, placement: values.placement || "standalone", body: bodyFromText(values.body, existing.body), cover: values.image, cover_alt: values.alt, pdf: values.pdf, schedule: parseSchedule(values.schedule), navigation_order: Number(values.order || 100) };
  else if (currentType === "clergy") changed = { full_name: values.title, rank: values.rank, position: values.position, photo: values.image, biography: [{ type: "paragraph", text: values.biography }], name_day: values.nameDay, order: Number(values.order || 100) };
  else if (currentType === "video") changed = { publication_date: values.date, external_url: values.externalUrl, cover: values.image, category: values.category, is_live: values.isLive };
  else if (currentType === "contact") changed = { address: values.address, metro: values.metro, phone: values.phone, email: values.email, opening_hours: values.openingHours, map_coordinates: values.mapCoordinates, legal_details: values.legalDetails, social_links: String(values.socialLinks || "").split(/\r?\n/).filter(Boolean).map(url => ({ network: url.includes("t.me") ? "telegram" : url.includes("vk.com") ? "vk" : "other", url, enabled: true })) };
  else changed = apiState.current
    ? { summary: values.summary, cover: values.image }
    : { summary: values.summary, cover: values.image };
  if (values.legacyBody !== undefined) changed.body_text = values.legacyBody;
  return { ...existing, ...changed };
}

function blockText(block) {
  if (!block || typeof block !== "object") return "";
  if (typeof block.text === "string") return block.text;
  if (typeof block.value === "string") return block.value;
  if (typeof block.data?.text === "string") return block.data.text;
  if (typeof block.data?.value === "string") return block.data.value;
  return "";
}

function editableBodyBlock(block) {
  return typeof block === "string" || block?.type === "paragraph" || (!block?.type && Boolean(blockText(block)));
}

function bodyToText(body) {
  if (typeof body === "string") return body;
  if (!Array.isArray(body)) return "";
  return body.filter(editableBodyBlock).map(blockText).filter(Boolean).join("\n\n");
}

function bodyFromText(value, existing = []) {
  const text = String(value || "").trim();
  if (text === bodyToText(existing).trim()) return existing;
  const paragraphTexts = text ? text.split(/\n\s*\n/).map(paragraph => paragraph.trim()) : [];
  let paragraphIndex = 0;
  const makeParagraph = (paragraph, index, previous = {}) => ({
    id: previous.id || globalThis.crypto?.randomUUID?.() || `paragraph-${Date.now()}-${index}`,
    type: "paragraph",
    data: { text: paragraph },
  });
  const result = [];
  for (const block of Array.isArray(existing) ? existing : []) {
    if (!editableBodyBlock(block)) {
      result.push(block);
      continue;
    }
    if (paragraphIndex < paragraphTexts.length) {
      result.push(makeParagraph(paragraphTexts[paragraphIndex], paragraphIndex, typeof block === "object" ? block : {}));
    }
    paragraphIndex += 1;
  }
  while (paragraphIndex < paragraphTexts.length) {
    result.push(makeParagraph(paragraphTexts[paragraphIndex], paragraphIndex));
    paragraphIndex += 1;
  }
  return result;
}

function parseSchedule(value) {
  try {
    const rows = JSON.parse(value || "[]");
    return Array.isArray(rows) ? rows.map(row => ({ weekday: String(row.weekday || ""), time: String(row.time || ""), title: String(row.title || ""), note: String(row.note || "") })) : [];
  } catch (_) { return []; }
}

function parsePhotoList(value) {
  try { const parsed = JSON.parse(value || "[]"); return Array.isArray(parsed) ? parsed : []; }
  catch (_) { return []; }
}

async function uploadSelectedFiles(input) {
  if (!apiState.available) { toast("Загрузка файлов доступна при запуске рабочей CMS"); return; }
  const files = [...input.files];
  if (!files.length) return;
  const field = input.closest(".field");
  const button = field.querySelector("[data-upload-button]");
  const hidden = field.querySelector('input[type="hidden"]');
  button.disabled = true;
  try {
    const uploaded = [];
    for (const file of files) {
      const form = new FormData();
      form.append("file", file);
      form.append("alt_text", editorForm.elements.alt?.value || "");
      uploaded.push(await apiRequest("/api/admin/media", { method: "POST", body: form }));
    }
    hidden.value = input.multiple ? JSON.stringify(uploaded.map((item, index) => ({ image: item.url, alt: "", order: index + 1 }))) : uploaded[0].url;
    button.querySelector("b").textContent = input.multiple ? `${uploaded.length} фотографий` : uploaded[0].name;
    updatePreview();
    toast(input.multiple ? `Загружено файлов: ${uploaded.length}` : "Файл загружен в новую CMS");
  } finally { button.disabled = false; input.value = ""; }
}

function editorValuesFromRecord(record) {
  const data = record.data || {};
  const starts = data.starts_at || "";
  return {
    title: record.title,
    kicker: data.kicker,
    date: (data.publication_date || data.event_date || starts).slice(0, 10),
    time: starts.slice(11, 16),
    category: data.category,
    summary: data.summary || data.note || "",
    body: bodyToText(data.body),
    relatedSection: data.related_section || "",
    image: data.cover || data.photo || "",
    alt: data.cover_alt,
    ctaLabel: data.cta_label,
    targetUrl: data.target_url,
    contentSlug: data.content_slug,
    startDate: starts.slice(0, 16),
    endDate: (data.ends_at || "").slice(0, 16),
    priority: data.priority,
    featured: data.featured,
    serviceType: data.service_type,
    number: data.number,
    period: data.period,
    pdf: data.pdf,
    contact: data.contact_name || data.contact,
    phone: data.phone,
    email: data.email,
    schedule: JSON.stringify(Array.isArray(data.schedule) ? data.schedule : []),
    placement: data.placement || "standalone",
    rank: data.rank,
    position: data.position,
    nameDay: data.name_day,
    biography: Array.isArray(data.biography) ? data.biography.map(block => block.text || "").join("\n") : data.biography,
    order: data.order ?? data.navigation_order,
    externalUrl: data.external_url,
    isLive: data.is_live,
    address: data.address,
    metro: data.metro,
    openingHours: data.opening_hours,
    mapCoordinates: data.map_coordinates,
    legalDetails: data.legal_details,
    socialLinks: Array.isArray(data.social_links) ? data.social_links.map(item => item.url).join("\n") : "",
    photos: Array.isArray(data.photos) ? JSON.stringify(data.photos) : data.photos || "",
    legacyBody: data.body_text,
  };
}

function workflowText(record) {
  if (!record) return { state: "Новый материал", note: "Сохраните материал, чтобы начать согласование." };
  const working = `v${record.version}`;
  const published = record.published_version ? `v${record.published_version}` : "";
  if (record.is_public && record.has_unpublished_changes) {
    const action = record.status === "in_review" ? "на проверке" : record.status === "scheduled" ? "запланирована" : "редактируется";
    return { state: `На сайте ${published} · ${action} ${working}`, note: record.scheduled_at ? `Автопубликация: ${formatCmsDate(record.scheduled_at)}.` : "Посетители видят прежнюю опубликованную версию." };
  }
  if (record.is_public) return { state: `На сайте ${published}`, note: "Рабочая и опубликованная версии совпадают." };
  if (record.status === "scheduled") return { state: `Запланирована ${working}`, note: `Автопубликация: ${formatCmsDate(record.scheduled_at)}.` };
  return { state: `${statusLabels[record.status] || record.status} ${working}`, note: record.status === "archived" || record.status === "trash" ? "Материал скрыт с сайта. После восстановления потребуется новая проверка." : "Материал пока не виден посетителям." };
}

function workflowButton(label, action, variant = "ghost") {
  return `<button class="button button--${variant} button--compact" type="button" data-workflow-action="${action}">${label}</button>`;
}

function renderWorkflow() {
  const record = apiState.current;
  const card = document.querySelector("[data-workflow-card]");
  const state = workflowText(record);
  card.hidden = !apiState.user;
  document.querySelector("[data-workflow-state]").textContent = state.state;
  document.querySelector("[data-workflow-note]").textContent = apiState.dirty ? "Есть несохранённые изменения. Публичные действия временно недоступны." : state.note;
  document.querySelector(".editor-head__status span").textContent = record ? statusLabels[record.status] || record.status : "Черновик";
  const actions = [];
  if (record) actions.push(workflowButton("История", "history"));
  if (record?.status === "draft" && !record.migration_review_required && can("editor")) actions.push(workflowButton("Отправить на проверку", "submit-review", "primary"));
  if (record?.status === "in_review" && can("publisher")) {
    actions.push(workflowButton("Опубликовать", "publish", "primary"));
    actions.push(workflowButton("Запланировать", "schedule"));
    actions.push(workflowButton("Вернуть редактору", "return-to-draft"));
  }
  if (record?.status === "scheduled" && can("publisher")) actions.push(workflowButton("Отменить расписание", "return-to-draft"));
  if (record && ["draft", "in_review", "scheduled", "published"].includes(record.status) && can("publisher")) {
    actions.push(workflowButton("В архив", "archive"));
    actions.push(workflowButton("В корзину", "trash", "danger"));
  }
  if (record?.status === "archived" && can("publisher")) {
    actions.push(workflowButton("Восстановить", "restore", "primary"));
    actions.push(workflowButton("В корзину", "trash", "danger"));
  }
  if (record?.status === "trash" && can("publisher")) actions.push(workflowButton("Восстановить", "restore", "primary"));
  if (!can("editor")) actions.push('<span class="read-only-note">Режим просмотра</span>');
  document.querySelector("[data-workflow-actions]").innerHTML = actions.join("");
  if (apiState.dirty) document.querySelectorAll('[data-workflow-action]:not([data-workflow-action="history"]):not([data-workflow-action="submit-review"])').forEach(button => { button.disabled = true; });

  const editable = can("editor") && (!record || ["draft", "in_review", "scheduled", "published"].includes(record.status));
  editorForm.querySelectorAll("input,textarea,select,button[data-upload-button],button[data-schedule-add],button[data-schedule-remove]").forEach(element => { element.disabled = !editable; });
  document.querySelectorAll("[data-save-draft]").forEach(button => { button.hidden = !editable; button.disabled = !editable; });
  document.querySelector("[data-create-current]").disabled = !can("editor");
}

function fillEditor(record) {
  if (Object.prototype.hasOwnProperty.call(record.data || {}, "body_text")) {
    editorForm.querySelector(".form-footer").insertAdjacentHTML("beforebegin", `<div class="field-card legacy-text-card"><h2>Текст со старого сайта</h2><label class="field">Проверьте и очистите текст<textarea name="legacyBody" rows="14"></textarea><small class="field-help">Удалите старое меню, футер, повторяющиеся заголовки и устаревшие сообщения. Форматирование и переносы строк сохранятся.</small></label></div>`);
  }
  const mapped = editorValuesFromRecord(record);
  Object.entries(mapped).forEach(([name, value]) => {
    const input = editorForm.elements.namedItem(name);
    if (!input || value === undefined || value === null) return;
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = String(value);
  });
  initializeScheduleEditors();
  loadSectionRelations().then(() => {
    const relation = editorForm.elements.namedItem("relatedSection");
    if (relation) relation.value = mapped.relatedSection || "";
  }).catch(error => toast(error.message));
  document.querySelector(".editor-head .eyebrow").textContent = "Редактирование материала";
  document.querySelector("[data-editor-title]").textContent = record.title;
  document.querySelector("[data-migration-warning]")?.remove();
  if (record.migration_review_required) {
    const rawLength = (record.data?.body_text || "").length;
    const legacyHref = record.legacy_url?.startsWith("/") ? `https://www.sv-innokenty.ru${record.legacy_url}` : record.legacy_url;
    const legacyLink = legacyHref ? `<a class="text-link" href="${escapeCms(legacyHref)}" target="_blank" rel="noopener">Сравнить со старой страницей ↗</a>` : "";
    const reviewButton = can("editor") ? '<button class="button button--primary button--compact" type="button" data-mark-reviewed>Сохранить и отметить проверенным</button>' : "";
    document.querySelector(".editor-head").insertAdjacentHTML("afterend", `<div class="migration-warning" data-migration-warning><strong>Черновик перенесён со старого сайта</strong><span>Сравните материал с исходной страницей, проверьте заголовок, текст и изображения.${rawLength ? ` Исходный снимок сохранён в CMS (${rawLength.toLocaleString("ru-RU")} знаков).` : ""}</span><div class="migration-warning__actions">${reviewButton}${legacyLink}</div></div>`);
  }
  apiState.dirty = false;
  updatePreview(false);
  renderWorkflow();
  document.querySelector("[data-save-status]").textContent = record.migration_review_required
    ? "Материал загружен — требуется проверка"
    : "Материал загружен";
}

async function loadContentList(type = currentType) {
  if (!apiState.available || !apiState.user) return;
  const query = document.querySelector("[data-content-search]").value.trim();
  const reviewOnly = document.querySelector("[data-review-only]").checked;
  const reviewFilter = reviewOnly ? "&review_required=true" : "";
  const index = await apiRequest(`/api/admin/content-index?content_type=${encodeURIComponent(serverType(type))}&limit=100&q=${encodeURIComponent(query)}${reviewFilter}`);
  apiState.list = index.items;
  const picker = document.querySelector("[data-content-picker]");
  const select = document.querySelector("[data-content-select]");
  picker.hidden = false;
  select.innerHTML = `<option value="">Новый материал</option>${apiState.list.map(item => `<option value="${escapeCms(item.id)}">${item.is_public ? "●" : item.migration_review_required ? "!" : "○"} ${escapeCms(item.title)} · ${escapeCms(statusLabels[item.status] || item.status)} v${item.version}</option>`).join("")}`;
  if (apiState.current) select.value = apiState.current.id;
  document.querySelector("[data-content-count]").textContent = `${apiState.list.length} из ${index.total}`;
}

async function saveDraft() {
  if (!apiState.available) {
    localStorage.setItem(`cms-draft-${currentType}`, JSON.stringify(values()));
    document.querySelector("[data-save-status]").textContent = "Черновик сохранён локально";
    toast("Черновик сохранён только в этом браузере");
    return null;
  }
  if (apiState.current && !apiState.dirty) {
    toast("Изменений для сохранения нет");
    return apiState.current;
  }
  const v = values();
  const data = apiData(v);
  const options = apiState.current
    ? { method: "PUT", body: JSON.stringify({ title: v.title, slug: apiState.current.slug, data, version: apiState.current.version }) }
    : { method: "POST", body: JSON.stringify({ content_type: serverType(), title: v.title, data }) };
  apiState.current = await apiRequest(apiState.current ? `/api/admin/contents/${apiState.current.id}` : "/api/admin/contents", options);
  apiState.dirty = false;
  document.querySelector(".editor-head .eyebrow").textContent = "Редактирование материала";
  document.querySelector("[data-editor-title]").textContent = apiState.current.title;
  document.querySelector("[data-save-status]").textContent = "Черновик сохранён в CMS";
  renderWorkflow();
  await loadContentList();
  toast("Черновик сохранён в новой CMS");
  return apiState.current;
}

async function publishCurrent() {
  if (!apiState.available) {
    toast("Локальный прототип: публикация не выполнялась");
    return;
  }
  if (!apiState.current || apiState.current.status !== "in_review") throw new Error("Публикация доступна только после отправки на проверку");
  if (apiState.dirty) throw new Error("Есть несохранённые изменения. Сохраните их и снова отправьте материал на проверку");
  apiState.current = await apiRequest(`/api/admin/contents/${apiState.current.id}/publish`, { method: "POST", body: JSON.stringify({ version: apiState.current.version }) });
  document.querySelector("[data-save-status]").textContent = "Материал опубликован";
  renderWorkflow();
  await loadContentList();
  toast("Материал опубликован на новом сайте");
}

async function markCurrentReviewed() {
  if (!apiState.current?.migration_review_required) return;
  const saved = await saveDraft();
  apiState.current = await apiRequest(`/api/admin/contents/${saved.id}/review`, { method: "POST", body: JSON.stringify({ version: saved.version }) });
  document.querySelector("[data-migration-warning]")?.remove();
  renderWorkflow();
  document.querySelector("[data-save-status]").textContent = "Проверка редактора сохранена";
  await loadContentList();
  toast("Материал отмечен проверенным. Теперь его можно опубликовать.");
}

async function postWorkflow(action, payload = {}) {
  if (!apiState.current) throw new Error("Сначала выберите материал");
  if (apiState.dirty) throw new Error("Сначала сохраните изменения. После сохранения материал вернётся в черновики");
  apiState.current = await apiRequest(`/api/admin/contents/${apiState.current.id}/${action}`, {
    method: "POST", body: JSON.stringify({ version: apiState.current.version, ...payload }),
  });
  apiState.dirty = false;
  renderWorkflow();
  await loadContentList();
  return apiState.current;
}

async function submitCurrentForReview() {
  if (!apiState.current || apiState.dirty) await saveDraft();
  await postWorkflow("submit-review");
  document.querySelector("[data-save-status]").textContent = "Материал отправлен на проверку";
  toast("Материал ожидает решения публикатора");
}

function openScheduleDialog() {
  if (apiState.dirty) { toast("Сначала сохраните изменения и повторно отправьте материал на проверку"); return; }
  const dialog = document.querySelector("[data-schedule-dialog]");
  const input = dialog.querySelector('input[name="scheduled_at"]');
  const soon = new Date(Date.now() + 60 * 60 * 1000);
  soon.setMinutes(Math.ceil(soon.getMinutes() / 5) * 5, 0, 0);
  input.value = `${soon.getFullYear()}-${String(soon.getMonth() + 1).padStart(2, "0")}-${String(soon.getDate()).padStart(2, "0")}T${String(soon.getHours()).padStart(2, "0")}:${String(soon.getMinutes()).padStart(2, "0")}`;
  if (!dialog.open) dialog.showModal();
}

async function openHistory() {
  if (!apiState.current) return;
  const contentId = apiState.current.id;
  const dialog = document.querySelector("[data-history-dialog]");
  document.querySelector("[data-revision-list]").innerHTML = '<div class="history-empty">Загружаем версии…</div>';
  document.querySelector("[data-audit-list]").innerHTML = '<div class="history-empty">Загружаем журнал…</div>';
  if (!dialog.open) dialog.showModal();
  const [revisions, audit] = await Promise.all([
    apiRequest(`/api/admin/contents/${contentId}/revisions?limit=50`),
    apiRequest(`/api/admin/contents/${contentId}/audit-events?limit=50`),
  ]);
  const canRestore = can("editor") && !["archived", "trash"].includes(apiState.current.status);
  document.querySelector("[data-revision-list]").innerHTML = revisions.items.length ? revisions.items.map(item => `<article class="history-item"><div class="history-item__head"><div><b>Версия ${item.version}</b><p>${escapeCms(formatCmsDate(item.created_at))} · ${escapeCms(item.actor_username || "система")}</p></div>${canRestore && !item.is_current ? `<button class="button button--ghost button--compact" type="button" data-restore-revision="${item.version}">Восстановить</button>` : ""}</div><div class="history-item__badges">${item.is_current ? '<span class="history-badge">Рабочая</span>' : ""}${item.is_published ? '<span class="history-badge">На сайте</span>' : ""}<span class="history-badge">${escapeCms(statusLabels[item.status] || item.status)}</span></div></article>`).join("") : '<div class="history-empty">Ревизий пока нет</div>';
  document.querySelector("[data-audit-list]").innerHTML = audit.items.length ? audit.items.map(item => `<article class="history-item"><div class="history-item__head"><b>${escapeCms(auditLabels[item.action] || item.action)}</b><span class="history-badge">v${item.content_version}</span></div><p>${escapeCms(formatCmsDate(item.created_at))} · ${escapeCms(item.actor_username || "система")}</p>${item.from_status || item.to_status ? `<div class="history-item__badges"><span class="history-badge">${escapeCms(statusLabels[item.from_status] || item.from_status || "создание")} → ${escapeCms(statusLabels[item.to_status] || item.to_status || "—")}</span></div>` : ""}</article>`).join("") : '<div class="history-empty">Действий пока нет</div>';
}

async function restoreRevision(version) {
  if (apiState.dirty) throw new Error("Сохраните или отмените текущие изменения перед восстановлением версии");
  apiState.current = await apiRequest(`/api/admin/contents/${apiState.current.id}/revisions/${version}/restore`, { method: "POST", body: JSON.stringify({ version: apiState.current.version }) });
  apiState.dirty = false;
  renderEditor(uiTypes[apiState.current.content_type] || apiState.current.content_type);
  fillEditor(apiState.current);
  await loadContentList();
  await openHistory();
  toast(`Версия ${version} скопирована в новый черновик v${apiState.current.version}`);
}

async function openRecord(id) {
  if (!id) {
    apiState.current = null;
    renderEditor(currentType);
    return;
  }
  const record = apiState.list.find(item => item.id === id) || await apiRequest(`/api/admin/contents/${id}`);
  apiState.current = record;
  currentType = uiTypes[record.content_type] || record.content_type;
  renderEditor(currentType);
  fillEditor(record);
  const select = document.querySelector("[data-content-select]");
  if ([...select.options].some(option => option.value === record.id)) select.value = record.id;
}

function applySession(session) {
  apiState.user = session.user;
  apiState.csrf = session.csrf_token;
  document.querySelector(".cms-user strong").textContent = session.user.username;
  document.querySelector(".cms-user small").textContent = session.user.role;
  document.querySelector("[data-save-status]").textContent = "CMS подключена — можно редактировать";
  document.querySelector("[data-publish-note]").textContent = "Материал будет опубликован на новом сайте. Старый MODX при этом не изменяется.";
  renderWorkflow();
  loadContentList().catch(error => toast(error.message));
}

async function initApi() {
  try {
    const health = await fetch("/api/health", { credentials: "same-origin" });
    if (!health.ok) throw new Error("no-api");
    apiState.available = true;
    const session = await fetch("/api/admin/session", { credentials: "same-origin" });
    const sessionData = await session.json();
    if (sessionData.authenticated) applySession(sessionData);
    else document.querySelector("[data-login-dialog]").showModal();
  } catch (_) {
    document.querySelector("[data-save-status]").textContent = "Локальный демонстрационный режим";
    document.querySelector("[data-publish-note]").textContent = "В демонстрационном режиме публикация не отправляет данные.";
  }
}

function fieldMarkup(field) {
  if (field.type === "checkbox") return `<label class="choice"><input type="checkbox" name="${field.name}" ${field.value ? "checked" : ""}><span>${field.label}</span></label>`;
  if (field.type === "relation") return `<label class="field">${field.label}<select name="${field.name}" data-section-relation><option value="">Не выбрано</option></select>${field.help ? `<small class="field-help">${field.help}</small>` : ""}</label>`;
  if (field.type === "schedule") return `<div class="field schedule-editor" data-schedule-editor><span>${field.label}</span><div class="schedule-editor__rows" data-schedule-rows></div><button class="button button--ghost button--compact" type="button" data-schedule-add>Добавить строку</button><input type="hidden" name="${field.name}" value="${escapeCms(field.value || "[]")}"></div>`;
  if (["image","file","multiimage"].includes(field.type)) {
    const accept = field.type === "file" ? "application/pdf" : "image/jpeg,image/png,image/webp";
    return `<div class="field"><span>${field.label}</span><button class="upload-zone" type="button" data-upload-button="${field.type}"><span><b>${field.value || "Выбрать файл"}</b>${field.type === "multiimage" ? "Выберите несколько фотографий" : "Нажмите, чтобы выбрать файл"}</span></button><input class="cms-file-input" type="file" data-upload-input="${field.name}" accept="${accept}" ${field.type === "multiimage" ? "multiple" : ""}><input type="hidden" name="${field.name}" value="${field.value || ""}"></div>`;
  }
  if (field.type === "select") return `<label class="field">${field.label}<select name="${field.name}">${field.options.map(option => { const value = typeof option === "object" ? option.value : option; const label = typeof option === "object" ? option.label : option; return `<option value="${escapeCms(value)}" ${value === field.value ? "selected" : ""}>${escapeCms(label)}</option>`; }).join("")}</select>${field.help?`<small class="field-help">${field.help}</small>`:""}</label>`;
  if (field.type === "textarea") return `<label class="field">${field.label}<textarea name="${field.name}" rows="5" ${field.required?"required":""}>${escapeCms(field.value || "")}</textarea>${field.help?`<small class="field-help">${field.help}</small>`:""}</label>`;
  return `<label class="field">${field.label}<input type="${field.type}" name="${field.name}" value="${escapeCms(field.value || "")}" ${field.required?"required":""}>${field.help?`<small class="field-help">${field.help}</small>`:""}</label>`;
}

const weekdays = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];

function renderScheduleEditor(editor) {
  const hidden = editor.querySelector('input[type="hidden"]');
  const rows = parseSchedule(hidden.value);
  editor.querySelector("[data-schedule-rows]").innerHTML = rows.map((row, index) => `<div class="schedule-editor__row" data-schedule-row>
    <label>День<select data-schedule-key="weekday"><option value="">—</option>${weekdays.map((label, day) => `<option value="${day + 1}" ${String(row.weekday) === String(day + 1) ? "selected" : ""}>${label}</option>`).join("")}</select></label>
    <label>Время<input type="time" data-schedule-key="time" value="${escapeCms(row.time)}"></label>
    <label>Название<input type="text" data-schedule-key="title" value="${escapeCms(row.title)}"></label>
    <label>Примечание<input type="text" data-schedule-key="note" value="${escapeCms(row.note)}"></label>
    <button class="icon-button" type="button" data-schedule-remove="${index}" aria-label="Удалить строку">×</button>
  </div>`).join("");
}

function syncScheduleEditor(editor) {
  const rows = [...editor.querySelectorAll("[data-schedule-row]")].map(row => Object.fromEntries(
    [...row.querySelectorAll("[data-schedule-key]")].map(input => [input.dataset.scheduleKey, input.value.trim()])
  ));
  editor.querySelector('input[type="hidden"]').value = JSON.stringify(rows);
}

function initializeScheduleEditors() {
  editorForm.querySelectorAll("[data-schedule-editor]").forEach(renderScheduleEditor);
}

async function loadSectionRelations() {
  const selects = [...editorForm.querySelectorAll("[data-section-relation]")];
  if (!selects.length || !apiState.available || !apiState.user) return;
  const index = await apiRequest("/api/admin/content-index?content_type=parish_section&limit=200");
  selects.forEach(select => {
    const selected = select.value;
    select.innerHTML = `<option value="">Не выбрано</option>${index.items.map(item => `<option value="${escapeCms(item.id)}">${escapeCms(item.title)}</option>`).join("")}`;
    select.value = selected;
  });
}

function renderEditor(type = currentType) {
  currentType = type;
  const def = typeDefinitions[type];
  panel.hidden = true;
  document.querySelector("[data-editor-pane]").hidden = false;
  document.querySelector("[data-preview-pane]").hidden = false;
  document.querySelector("[data-editor-title]").textContent = def.title;
  document.querySelector("[data-editor-help]").textContent = def.help;
  document.querySelector(".editor-head .eyebrow").textContent = "Новый материал";
  document.querySelector("[data-migration-warning]")?.remove();
  document.querySelector("[data-mobile-title]").textContent = def.label;
  editorForm.innerHTML = `<div class="field-card"><h2>Основная информация</h2><div class="field-row">${def.fields.slice(0,2).map(fieldMarkup).join("")}</div>${def.fields.slice(2,4).map(fieldMarkup).join("")}</div><div class="field-card"><h2>Медиа и публикация</h2>${def.fields.slice(4).map(fieldMarkup).join("")}</div><div class="form-footer"><span class="field-help">Сохранение создаёт новую рабочую версию только при изменении содержимого.</span><button class="button button--primary" type="button" data-save-draft>Сохранить</button></div>`;
  document.querySelectorAll("[data-content-type]").forEach(button => button.classList.toggle("is-active", button.dataset.contentType === type));
  document.querySelectorAll("[data-panel]").forEach(button => button.classList.remove("is-active"));
  apiState.dirty = !apiState.current;
  initializeScheduleEditors();
  if (!apiState.current) loadSectionRelations().catch(error => toast(error.message));
  updatePreview(false);
  renderWorkflow();
}

function values() {
  const formData = new FormData(editorForm);
  const result = Object.fromEntries(formData.entries());
  editorForm.querySelectorAll("input[type=checkbox]").forEach(input => result[input.name] = input.checked);
  return result;
}

function updatePreview(markDirty = true) {
  const v = values();
  const def = typeDefinitions[currentType];
  const image = v.image || "";
  const date = v.date ? new Date(v.date).toLocaleDateString("ru-RU", { day:"numeric", month:"long", year:"numeric" }) : "Черновик";
  preview.innerHTML = `<div class="preview-site-head"><span>✣ Храм святителя Иннокентия</span><span>☰</span></div><div class="preview-site-main"><div class="preview-meta">${escapeCms(v.category || v.serviceType || def.label)} · ${escapeCms(date)}</div>${image ? `<img src="${safeCmsMedia(image)}" alt="">` : ""}<h2>${escapeCms(v.title || def.title)}</h2>${v.summary || v.period ? `<p>${escapeCms(v.summary || v.period)}</p>` : ""}<span class="text-link">Подробнее →</span></div>`;
  if (markDirty) {
    apiState.dirty = true;
    document.querySelector("[data-save-status]").textContent = "Есть несохранённые изменения";
    renderWorkflow();
  }
}

function showPanel(name) {
  document.querySelector("[data-editor-pane]").hidden = true;
  document.querySelector("[data-preview-pane]").hidden = true;
  panel.hidden = false;
  document.querySelectorAll("[data-content-type]").forEach(button => button.classList.remove("is-active"));
  document.querySelectorAll("[data-panel]").forEach(button => button.classList.toggle("is-active", button.dataset.panel === name));
  document.querySelector("[data-mobile-title]").textContent = name === "migration" ? "Миграция" : name === "media" ? "Медиатека" : "Настройки";
  if (name === "migration") panel.innerHTML = `<div class="eyebrow">Загрузка старого сайта</div><h1>Миграция контента</h1><p>Панель показывает прогресс переноса, не изменяя исходный MODX.</p><div class="metric-grid"><div class="metric-card"><b>2 436</b><span>ресурсов в дереве CMS</span></div><div class="metric-card"><b>148</b><span>выпусков листка</span></div><div class="metric-card"><b>13</b><span>лет фотогалереи</span></div><div class="metric-card"><b>0</b><span>изменений в старой CMS</span></div></div><table class="migration-table"><thead><tr><th>Тип</th><th>Источник</th><th>Новая сущность</th><th>Состояние</th></tr></thead><tbody><tr><td>Новости</td><td>Ресурсы MODX</td><td>Новость / анонс</td><td><span class="state-pill">Сопоставлено</span></td></tr><tr><td>Расписание</td><td>HTML в TinyMCE</td><td>Богослужения по датам</td><td><span class="state-pill state-pill--warn">Требует разбора</span></td></tr><tr><td>Фотогалерея</td><td>EvoGallery / MultiPhotos</td><td>Альбомы и изображения</td><td><span class="state-pill">Сопоставлено</span></td></tr><tr><td>Приходской листок</td><td>multiTV по годам</td><td>Выпуски и PDF</td><td><span class="state-pill">Сопоставлено</span></td></tr><tr><td>Редиректы</td><td>Старые URL</td><td>301-карта</td><td><span class="state-pill state-pill--warn">После импорта</span></td></tr></tbody></table>`;
  else if (name === "media") panel.innerHTML = `<div class="eyebrow">Файлы</div><h1>Медиатека</h1><p>Изображения, документы и видео доступны через поиск и папки; редактору не нужно знать пути файлов.</p><div class="metric-grid"><div class="metric-card"><b>Фото</b><span>автоматические миниатюры</span></div><div class="metric-card"><b>PDF</b><span>обложка и метаданные</span></div><div class="metric-card"><b>Alt</b><span>проверка описаний</span></div><div class="metric-card"><b>Поиск</b><span>по имени и материалу</span></div></div>`;
  else panel.innerHTML = `<div class="eyebrow">Настройки</div><h1>Только понятные параметры</h1><p>Название храма, контакты, часы работы, социальные сети и роли редакторов. Технические шаблоны и системные поля скрыты.</p>`;
  if (name === "migration" && apiState.available) {
    panel.insertAdjacentHTML("beforeend", `<section class="review-dashboard" data-review-dashboard><div class="review-dashboard__head"><div><div class="eyebrow">Редакторская приёмка</div><h2>Проверка перенесённых материалов</h2></div><button class="button button--primary" type="button" data-review-start>Начать проверку</button></div><div class="review-progress" aria-label="Прогресс редакторской проверки"><span data-review-progress></span></div><p class="review-summary" data-review-summary>Загружаем прогресс…</p><div class="review-types" data-review-types></div></section>`);
    panel.insertAdjacentHTML("beforeend", `<div class="migration-actions"><button class="button button--ghost" type="button" data-migration-dry-run>Проверить импорт</button><button class="button button--primary" type="button" data-migration-run>Импортировать черновики</button><span data-migration-live-status>Загружаем состояние…</span></div>`);
    refreshMigrationStatus().catch(error => toast(error.message));
  }
}

async function refreshMigrationStatus() {
  const status = await apiRequest("/api/admin/migration");
  const target = document.querySelector("[data-migration-live-status]");
  if (!target) return;
  const latest = status.runs[0];
  target.textContent = latest
    ? `В новой CMS: ${status.totals.contents || 0}; требуют проверки: ${status.totals.review_required || 0}; последний импорт: ${latest.imported} новых, ${latest.skipped} без изменений.`
    : "Импорт ещё не запускался. Сначала выполните безопасную проверку.";
  const cards = [...document.querySelectorAll(".metric-card")];
  if (cards.length >= 4) {
    cards[0].querySelector("b").textContent = Number(status.totals.contents || 0).toLocaleString("ru-RU");
    cards[0].querySelector("span").textContent = "материалов в новой CMS";
    cards[1].querySelector("b").textContent = Number(status.by_type.leaflet_issue || 0).toLocaleString("ru-RU");
    cards[1].querySelector("span").textContent = "выпусков листка";
    cards[2].querySelector("b").textContent = Number(status.by_type.gallery || 0).toLocaleString("ru-RU");
    cards[2].querySelector("span").textContent = "фотоальбомов";
    const redirectState = document.querySelector(".migration-table tbody tr:last-child .state-pill");
    if (redirectState) { redirectState.textContent = `${Number(status.totals.redirects || 0).toLocaleString("ru-RU")} URL`; redirectState.classList.remove("state-pill--warn"); }
  }
  const total = Number(status.totals.contents || 0);
  const reviewed = Number(status.totals.reviewed || 0);
  const remaining = Number(status.totals.review_required || 0);
  const percent = total ? Math.round(reviewed / total * 100) : 0;
  const progress = document.querySelector("[data-review-progress]");
  if (progress) progress.style.width = `${percent}%`;
  const summary = document.querySelector("[data-review-summary]");
  if (summary) summary.textContent = `Проверено ${reviewed.toLocaleString("ru-RU")} из ${total.toLocaleString("ru-RU")} · осталось ${remaining.toLocaleString("ru-RU")} · ${percent}%`;
  const typeLabels = { home_feature: "Главное", clergy: "Духовенство", gallery: "Фотоальбомы", leaflet_issue: "Листок", news: "Новости", page: "Страницы", parish_section: "Направления", site_contact: "Контакты" };
  const types = document.querySelector("[data-review-types]");
  if (types) types.innerHTML = Object.entries(status.review_by_type || {}).map(([type, counts]) => `<span class="review-type"><b>${escapeCms(typeLabels[type] || type)}</b> · ${Number(counts.reviewed).toLocaleString("ru-RU")}/${Number(counts.total).toLocaleString("ru-RU")}</span>`).join("");
  const start = document.querySelector("[data-review-start]");
  if (start) { start.disabled = remaining === 0; start.textContent = remaining ? "Начать проверку" : "Всё проверено"; }
}

async function openReviewQueue() {
  const status = await apiRequest("/api/admin/migration");
  const next = Object.entries(status.review_by_type || {}).find(([, counts]) => Number(counts.review_required) > 0);
  if (!next) { toast("Все импортированные материалы уже проверены"); return; }
  const type = uiTypes[next[0]] || next[0];
  apiState.current = null;
  document.querySelector("[data-content-search]").value = "";
  document.querySelector("[data-review-only]").checked = true;
  renderEditor(type);
  await loadContentList(type);
  const first = apiState.list.find(item => item.migration_review_required);
  if (first) await openRecord(first.id);
}

function toast(message) {
  const el = document.querySelector("[data-toast]");
  el.textContent = message; el.classList.add("is-visible");
  clearTimeout(toast.timer); toast.timer = setTimeout(()=>el.classList.remove("is-visible"),2600);
}

document.addEventListener("click", async event => {
  const target = event.target.closest("button"); if (!target) return;
  if (target.dataset.contentType) {
    apiState.current = null;
    document.querySelector("[data-content-search]").value = "";
    renderEditor(target.dataset.contentType);
    document.body.classList.remove("cms-menu-open");
    if (apiState.available) await loadContentList(target.dataset.contentType).catch(error => toast(error.message));
  }
  if (target.dataset.panel) {
    showPanel(target.dataset.panel);
    document.body.classList.remove("cms-menu-open");
  }
  if (target.matches("[data-create-current]")) { apiState.current = null; renderEditor(currentType); document.querySelector("[data-content-select]").value = ""; toast("Открыт новый пустой материал выбранного типа"); }
  if (target.matches("[data-cms-menu]")) document.body.classList.toggle("cms-menu-open");
  if (target.dataset.previewSize) { preview.classList.toggle("is-mobile", target.dataset.previewSize === "mobile"); document.querySelectorAll("[data-preview-size]").forEach(b=>b.classList.toggle("is-active",b===target)); }
  if (target.matches("[data-save-draft]")) await saveDraft().catch(error => toast(error.message));
  if (target.matches("[data-mark-reviewed]")) {
    target.disabled = true;
    try { await markCurrentReviewed(); }
    catch (error) { toast(error.message); }
    finally { target.disabled = false; }
  }
  if (target.matches("[data-publish-close]")) document.querySelector("[data-publish-dialog]").close();
  if (target.matches("[data-publish-confirm]")) {
    const unchecked = [...document.querySelectorAll("[data-publish-dialog] .publish-checklist input")].some(input => !input.checked);
    if (unchecked) { toast("Подтвердите все пункты проверки перед публикацией"); return; }
    target.disabled = true;
    try { await publishCurrent(); document.querySelector("[data-publish-dialog]").close(); }
    catch (error) { toast(error.message); }
    finally { target.disabled = false; }
  }
  if (target.matches("[data-schedule-close]")) document.querySelector("[data-schedule-dialog]").close();
  if (target.matches("[data-history-close]")) document.querySelector("[data-history-dialog]").close();
  if (target.matches("[data-login-close]")) document.querySelector("[data-login-dialog]").close();
  if (target.dataset.restoreRevision) {
    target.disabled = true;
    try { await restoreRevision(Number(target.dataset.restoreRevision)); }
    catch (error) { toast(error.message); }
    finally { target.disabled = false; }
  }
  if (target.dataset.workflowAction) {
    const action = target.dataset.workflowAction;
    target.disabled = true;
    try {
      if (action === "history") await openHistory();
      else if (action === "submit-review") await submitCurrentForReview();
      else if (action === "publish") {
        if (apiState.dirty) throw new Error("Сначала сохраните изменения и повторно отправьте материал на проверку");
        document.querySelector("[data-publish-dialog]").showModal();
      } else if (action === "schedule") openScheduleDialog();
      else {
        if (["archive", "trash"].includes(action) && !window.confirm(action === "archive" ? "Переместить материал в архив и скрыть его с сайта?" : "Переместить материал в корзину и скрыть его с сайта?")) return;
        await postWorkflow(action);
        const messages = { "return-to-draft": "Материал возвращён в черновики", archive: "Материал перемещён в архив", trash: "Материал перемещён в корзину", restore: "Материал восстановлен как скрытый черновик" };
        document.querySelector("[data-save-status]").textContent = messages[action] || "Состояние материала обновлено";
        toast(messages[action] || "Состояние материала обновлено");
      }
    } catch (error) { toast(error.message); }
    finally { target.disabled = false; }
  }
  if (target.matches("[data-upload-button]")) target.closest(".field").querySelector("[data-upload-input]").click();
  if (target.matches("[data-migration-dry-run]")) {
    target.disabled = true;
    try { const result = await apiRequest("/api/admin/migration/import?dry_run=true", { method: "POST" }); toast(`Проверка завершена: ${result.records_found} записей, ошибок: ${result.errors}`); }
    catch (error) { toast(error.message); }
    finally { target.disabled = false; }
  }
  if (target.matches("[data-migration-run]")) {
    target.disabled = true;
    try { const result = await apiRequest("/api/admin/migration/import?dry_run=false", { method: "POST" }); toast(`Импорт завершён: ${result.imported} новых, ${result.skipped} без изменений`); await refreshMigrationStatus(); }
    catch (error) { toast(error.message); }
    finally { target.disabled = false; }
  }
  if (target.matches("[data-review-start]")) {
    target.disabled = true;
    try { await openReviewQueue(); }
    catch (error) { toast(error.message); target.disabled = false; }
  }
});

editorForm.addEventListener("click", event => {
  const add = event.target.closest("[data-schedule-add]");
  const remove = event.target.closest("[data-schedule-remove]");
  if (!add && !remove) return;
  const editor = event.target.closest("[data-schedule-editor]");
  const hidden = editor.querySelector('input[type="hidden"]');
  const rows = parseSchedule(hidden.value);
  if (add) rows.push({ weekday: "", time: "", title: "", note: "" });
  if (remove) rows.splice(Number(remove.dataset.scheduleRemove), 1);
  hidden.value = JSON.stringify(rows);
  renderScheduleEditor(editor);
  updatePreview();
});
editorForm.addEventListener("input", event => {
  const editor = event.target.closest("[data-schedule-editor]");
  if (editor) syncScheduleEditor(editor);
});
editorForm.addEventListener("change", event => {
  const editor = event.target.closest("[data-schedule-editor]");
  if (editor) syncScheduleEditor(editor);
});
editorForm.addEventListener("input", updatePreview);
editorForm.addEventListener("change", updatePreview);
editorForm.addEventListener("change", event => { if (event.target.matches("[data-upload-input]")) uploadSelectedFiles(event.target).catch(error => toast(error.message)); });
document.querySelector("[data-content-select]").addEventListener("change", event => openRecord(event.target.value).catch(error => toast(error.message)));
let contentSearchTimer;
document.querySelector("[data-content-search]").addEventListener("input", () => {
  clearTimeout(contentSearchTimer);
  contentSearchTimer = setTimeout(() => loadContentList().catch(error => toast(error.message)), 220);
});
document.querySelector("[data-review-only]").addEventListener("change", () => loadContentList().catch(error => toast(error.message)));
document.querySelector("[data-login-form]").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const error = document.querySelector("[data-login-error]");
  error.textContent = "";
  try {
    const session = await apiRequest("/api/admin/login", { method: "POST", body: JSON.stringify({ username: form.get("username"), password: form.get("password") }) });
    applySession(session);
    document.querySelector("[data-login-dialog]").close();
  } catch (reason) { error.textContent = reason.message; }
});
document.querySelector("[data-schedule-form]").addEventListener("submit", async event => {
  event.preventDefault();
  const submit = event.currentTarget.querySelector('button[type="submit"]');
  const value = new FormData(event.currentTarget).get("scheduled_at");
  submit.disabled = true;
  try {
    const date = new Date(String(value));
    if (Number.isNaN(date.valueOf())) throw new Error("Укажите корректные дату и время");
    await postWorkflow("schedule", { scheduled_at: date.toISOString() });
    document.querySelector("[data-schedule-dialog]").close();
    document.querySelector("[data-save-status]").textContent = `Публикация запланирована: ${formatCmsDate(date)}`;
    toast("Публикация запланирована");
  } catch (error) { toast(error.message); }
  finally { submit.disabled = false; }
});
renderEditor();
initApi();
