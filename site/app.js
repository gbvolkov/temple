const STATIC_SEARCH_ITEMS = [
  ["Расписание богослужений", "Опубликованное время ближайших служб", "/schedule"],
  ["О храме", "История, духовенство и контакты", "/about"],
  ["Жизнь прихода", "Опубликованные направления приходской жизни", "/parish"],
  ["Воскресная школа", "Раздел для детей и родителей", "/school"],
  ["Новости", "Новости и анонсы прихода", "/news"],
  ["Фотогалерея", "Опубликованные фотоальбомы", "/gallery"],
  ["Иннокентиевский листок", "Опубликованные выпуски приходского издания", "/leaflet"],
  ["Видео и трансляции", "Опубликованные записи и прямые эфиры", "/media"],
  ["Как добраться", "Адрес, метро и контакты", "/about#contacts"],
];

const STATIC_HASH_ROUTES = {
  "#/": "/",
  "#/schedule": "/schedule",
  "#/about": "/about",
  "#/about#contacts": "/about#contacts",
  "#/parish": "/parish",
  "#/school": "/school",
  "#/news": "/news",
  "#/gallery": "/gallery",
  "#/leaflet": "/leaflet",
  "#/media": "/media",
};

const CONTENT_ROUTES = {
  news: "/news/",
  gallery: "/gallery/",
  parish_section: "/parish/",
  clergy: "/about/clergy/",
  page: "/pages/",
  home_feature: "/",
  service: "/schedule",
  leaflet_issue: "/leaflet",
  video: "/media",
  site_contact: "/about#contacts",
};

function cleanSlug(value) {
  return String(value || "").split(/[?#]/, 1)[0];
}

async function runLegacyHashBridge() {
  const hash = window.location.hash;
  if (!hash || hash === "#contacts" || !hash.startsWith("#/")) return;

  const staticTarget = STATIC_HASH_ROUTES[hash];
  if (staticTarget) {
    window.location.replace(staticTarget);
    return;
  }

  if (hash.startsWith("#/news/")) {
    const slug = cleanSlug(hash.slice("#/news/".length));
    if (slug) window.location.replace(`/news/${encodeURIComponent(decodeURIComponent(slug))}`);
    return;
  }

  if (!hash.startsWith("#/content/")) return;
  const slug = cleanSlug(hash.slice("#/content/".length));
  if (!slug) return;

  try {
    const response = await fetch(`/api/public/content/${encodeURIComponent(decodeURIComponent(slug))}`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const item = await response.json();
    const route = CONTENT_ROUTES[item.content_type];
    if (!route) throw new Error("Unknown content type");
    const target = route.endsWith("/") ? `${route}${encodeURIComponent(item.published_slug || item.slug)}` : route;
    window.location.replace(target);
  } catch (_error) {
    window.location.replace(`/pages/${encodeURIComponent(decodeURIComponent(slug))}`);
  }
}

function openDialog(dialog) {
  if (!dialog) return;
  if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
}

function closeDialog(dialog) {
  if (dialog?.open) dialog.close();
}

function showToast(message) {
  const toast = document.querySelector("[data-toast]");
  if (!toast || !message) return;
  toast.textContent = message;
  toast.classList.add("is-visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("is-visible"), 3200);
}

function setMenu(open) {
  document.body.classList.toggle("menu-open", open);
  document.querySelectorAll("[data-menu-toggle]").forEach(button => button.setAttribute("aria-expanded", String(open)));
}

function setNoteStep(step) {
  document.querySelectorAll("[data-note-step]").forEach(element => {
    element.hidden = Number(element.dataset.noteStep) !== step;
  });
  const summary = document.querySelector("[data-note-summary]");
  if (step === 3 && summary) {
    const type = document.querySelector('[name="noteType"]:checked')?.value || "Записка";
    const names = document.querySelector('[data-note-form] [name="names"]')?.value.trim();
    summary.textContent = names ? `${type}: ${names}` : `${type}. Имена не указаны.`;
  }
}

function renderSearch(query) {
  const results = document.querySelector("[data-search-results]");
  if (!results) return;
  const needle = query.trim().toLocaleLowerCase("ru-RU");
  const matches = needle
    ? STATIC_SEARCH_ITEMS.filter(item => `${item[0]} ${item[1]}`.toLocaleLowerCase("ru-RU").includes(needle))
    : STATIC_SEARCH_ITEMS;
  results.replaceChildren(...matches.map(([title, description, href]) => {
    const link = document.createElement("a");
    link.className = "search-result";
    link.href = href;
    const strong = document.createElement("strong");
    strong.textContent = title;
    const small = document.createElement("small");
    small.textContent = description;
    link.append(strong, small);
    return link;
  }));
}

function applyFilter(button) {
  const groupName = button.dataset.filterButton;
  const value = button.dataset.filterValue;
  const group = document.querySelector(`[data-filter-group="${groupName}"]`);
  if (!group) return;
  document.querySelectorAll(`[data-filter-button="${groupName}"]`).forEach(candidate => {
    candidate.classList.toggle("is-active", candidate === button);
  });
  group.querySelectorAll("[data-filter-item]").forEach(item => {
    item.hidden = value !== "all" && item.dataset.filterItem !== value;
  });

  if (groupName === "gallery") {
    const url = new URL(window.location.href);
    if (value === "all") url.searchParams.delete("year");
    else url.searchParams.set("year", value);
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }
}

function bindDialogs() {
  const noteDialog = document.querySelector("[data-note-dialog]");
  const searchDialog = document.querySelector("[data-search-dialog]");
  const lightbox = document.querySelector("[data-lightbox]");

  document.querySelectorAll("[data-note-open]").forEach(button => button.addEventListener("click", () => {
    setMenu(false);
    setNoteStep(1);
    openDialog(noteDialog);
  }));
  document.querySelectorAll("[data-note-close]").forEach(button => button.addEventListener("click", () => closeDialog(noteDialog)));
  document.querySelectorAll("[data-note-next]").forEach(button => button.addEventListener("click", () => {
    const current = Number(document.querySelector("[data-note-step]:not([hidden])")?.dataset.noteStep || 1);
    if (current === 2) {
      const names = document.querySelector('[data-note-form] [name="names"]');
      if (names && !names.reportValidity()) return;
    }
    setNoteStep(Math.min(3, current + 1));
  }));
  document.querySelectorAll("[data-note-back]").forEach(button => button.addEventListener("click", () => {
    const current = Number(document.querySelector("[data-note-step]:not([hidden])")?.dataset.noteStep || 2);
    setNoteStep(Math.max(1, current - 1));
  }));
  document.querySelector("[data-note-form]")?.addEventListener("submit", event => {
    event.preventDefault();
    closeDialog(noteDialog);
    showToast("Данные не отправлялись и не сохранялись");
  });

  document.querySelectorAll("[data-search-open]").forEach(button => button.addEventListener("click", () => {
    setMenu(false);
    renderSearch("");
    openDialog(searchDialog);
    document.querySelector("[data-search-input]")?.focus();
  }));
  document.querySelectorAll("[data-search-close]").forEach(button => button.addEventListener("click", () => closeDialog(searchDialog)));
  document.querySelector("[data-search-input]")?.addEventListener("input", event => renderSearch(event.target.value));

  document.querySelectorAll("[data-lightbox-src]").forEach(button => button.addEventListener("click", () => {
    const image = lightbox?.querySelector("[data-lightbox-image]");
    const caption = lightbox?.querySelector("[data-lightbox-caption]");
    if (image) {
      image.src = button.dataset.lightboxSrc;
      image.alt = button.dataset.lightboxCaption || "";
    }
    if (caption) caption.textContent = button.dataset.lightboxCaption || "";
    openDialog(lightbox);
  }));
  document.querySelectorAll("[data-lightbox-close]").forEach(button => button.addEventListener("click", () => closeDialog(lightbox)));

  document.querySelectorAll("dialog").forEach(dialog => dialog.addEventListener("click", event => {
    if (event.target === dialog) closeDialog(dialog);
  }));
}

function initialize() {
  document.querySelectorAll("[data-menu-toggle]").forEach(button => button.addEventListener("click", () => {
    setMenu(!document.body.classList.contains("menu-open"));
  }));
  document.querySelectorAll("[data-mobile-nav] a").forEach(link => link.addEventListener("click", () => setMenu(false)));
  document.querySelectorAll("[data-filter-button]").forEach(button => button.addEventListener("click", () => applyFilter(button)));
  document.querySelectorAll("[data-toast-message]").forEach(button => button.addEventListener("click", () => showToast(button.dataset.toastMessage)));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") setMenu(false);
  });
  bindDialogs();
}

runLegacyHashBridge();
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
else initialize();
