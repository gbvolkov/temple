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

function setMenu(open) {
  document.body.classList.toggle("menu-open", open);
  document.querySelectorAll("[data-menu-toggle]").forEach(button => {
    button.setAttribute("aria-expanded", String(open));
  });
}

function bindLightbox() {
  const lightbox = document.querySelector("[data-lightbox]");
  document.querySelectorAll("[data-lightbox-src]").forEach(button => button.addEventListener("click", () => {
    const image = lightbox?.querySelector("[data-lightbox-image]");
    const caption = lightbox?.querySelector("[data-lightbox-caption]");
    if (image) {
      image.src = button.dataset.lightboxSrc;
      image.alt = button.dataset.lightboxCaption || "";
    }
    if (caption) caption.textContent = button.dataset.lightboxCaption || "";
    if (typeof lightbox?.showModal === "function" && !lightbox.open) lightbox.showModal();
  }));
  document.querySelectorAll("[data-lightbox-close]").forEach(button => {
    button.addEventListener("click", () => lightbox?.close());
  });
  lightbox?.addEventListener("click", event => {
    if (event.target === lightbox) lightbox.close();
  });
}

function dialogController(dialog, openSelector, closeSelector, resetAfterSuccess) {
  if (!dialog) return;
  let opener = null;
  document.querySelectorAll(openSelector).forEach(button => button.addEventListener("click", () => {
    opener = button;
    setMenu(false);
    if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
    window.setTimeout(() => dialog.querySelector("input,textarea,button")?.focus(), 0);
  }));
  dialog.querySelectorAll(closeSelector).forEach(button => button.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", event => {
    if (event.target === dialog) dialog.close();
  });
  dialog.addEventListener("close", () => {
    if (dialog.dataset.completed === "true") {
      resetAfterSuccess();
      delete dialog.dataset.completed;
    }
    opener?.focus();
  });
}

function clearFormErrors(form) {
  form.querySelectorAll(".field-error").forEach(node => node.remove());
  form.querySelectorAll("[aria-invalid='true']").forEach(field => field.removeAttribute("aria-invalid"));
  form.querySelectorAll(".form-error").forEach(node => { node.textContent = ""; });
}

function placeFieldError(form, name, message) {
  const field = form.elements.namedItem(name);
  if (!field || field instanceof RadioNodeList) return false;
  field.setAttribute("aria-invalid", "true");
  const error = document.createElement("small");
  error.className = "field-error";
  error.textContent = message;
  field.insertAdjacentElement("afterend", error);
  return true;
}

async function postSubmission(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
  });
  let result = null;
  try { result = await response.json(); } catch (_error) { result = null; }
  if (!response.ok) {
    const error = new Error(typeof result?.detail === "string" ? result.detail : "Не удалось отправить форму. Попробуйте ещё раз.");
    error.status = response.status;
    error.details = Array.isArray(result?.detail) ? result.detail : [];
    error.retryAfter = response.headers.get("Retry-After");
    throw error;
  }
  return result;
}

function showSubmissionError(form, selector, error) {
  let placed = false;
  const invalidFields = new Set();
  error.details.forEach(item => {
    const name = [...(item.loc || [])].reverse().find(value => typeof value === "string" && value !== "body");
    if (name) {
      invalidFields.add(name);
      placed = placeFieldError(form, name, item.msg) || placed;
    }
  });
  const region = form.querySelector(selector);
  if (region) {
    region.textContent = placed ? "Проверьте отмеченные поля." : error.message;
    if (error.status === 429 && error.retryAfter) region.textContent += ` Повторите через ${error.retryAfter} сек.`;
  }
  return invalidFields;
}

function bindPrayerNoteForm() {
  const dialog = document.querySelector("[data-note-dialog]");
  const form = document.querySelector("[data-note-form]");
  if (!dialog || !form) return;
  let step = 1;
  const typeLabels = { health: "О здравии", repose: "Об упокоении", moleben: "Молебен" };

  function names() {
    return String(new FormData(form).get("names") || "").split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  }
  function showStep(value) {
    step = value;
    form.querySelectorAll("[data-note-step]").forEach(section => { section.hidden = Number(section.dataset.noteStep) !== step; });
    form.querySelectorAll("[data-note-progress]").forEach(marker => marker.classList.toggle("is-active", Number(marker.dataset.noteProgress) <= step));
    form.querySelector(`[data-note-step='${step}'] input, [data-note-step='${step}'] textarea, [data-note-step='${step}'] button`)?.focus();
  }
  function reset() {
    form.reset();
    form.classList.remove("is-success");
    form.querySelector("[data-note-success]").hidden = true;
    clearFormErrors(form);
    showStep(1);
  }
  function validateNames() {
    clearFormErrors(form);
    const values = names();
    if (!values.length || values.length > 10) {
      placeFieldError(form, "names", values.length ? "Можно указать не более 10 имён." : "Укажите хотя бы одно имя.");
      form.querySelector("[data-note-step='2'] [data-note-error]").textContent = "Проверьте список имён.";
      return false;
    }
    return true;
  }
  function renderSummary() {
    const data = new FormData(form);
    const summary = form.querySelector("[data-note-summary]");
    summary.replaceChildren();
    const title = document.createElement("strong");
    title.textContent = typeLabels[data.get("remembrance_type")] || "Записка";
    const list = document.createElement("ol");
    names().forEach(name => { const item = document.createElement("li"); item.textContent = name; list.append(item); });
    summary.append(title, list);
  }

  dialogController(dialog, "[data-note-open]", "[data-note-close]", reset);
  form.querySelectorAll("[data-note-next]").forEach(button => button.addEventListener("click", () => {
    if (step === 1) showStep(2);
    else if (validateNames()) { renderSummary(); showStep(3); }
  }));
  form.querySelectorAll("[data-note-back]").forEach(button => button.addEventListener("click", () => showStep(Math.max(1, step - 1))));
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!validateNames()) { showStep(2); return; }
    clearFormErrors(form);
    const data = new FormData(form);
    const submit = form.querySelector("[data-note-submit]");
    submit.disabled = true;
    submit.textContent = "Отправляем…";
    try {
      const result = await postSubmission("/api/public/submissions/prayer-note", {
        remembrance_type: data.get("remembrance_type"), names: names(), website: data.get("website") || "",
      });
      form.querySelector("[data-note-reference]").textContent = result.reference_code;
      form.querySelector("[data-note-success]").hidden = false;
      form.classList.add("is-success");
      dialog.dataset.completed = "true";
      form.querySelector("[data-note-success]").focus();
    } catch (error) {
      const invalidFields = showSubmissionError(form, "[data-note-step='3'] [data-note-error]", error);
      if (invalidFields.has("names")) showStep(2);
    } finally {
      submit.disabled = false;
      submit.textContent = "Отправить";
    }
  });
  reset();
}

function bindSchoolForm() {
  const dialog = document.querySelector("[data-school-dialog]");
  const form = document.querySelector("[data-school-form]");
  if (!dialog || !form) return;
  function reset() {
    form.reset();
    form.classList.remove("is-success");
    form.querySelector("[data-school-success]").hidden = true;
    clearFormErrors(form);
  }
  dialogController(dialog, "[data-school-open]", "[data-school-close]", reset);
  form.addEventListener("submit", async event => {
    event.preventDefault();
    clearFormErrors(form);
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    const submit = form.querySelector("[data-school-submit]");
    submit.disabled = true;
    submit.textContent = "Отправляем…";
    try {
      const result = await postSubmission("/api/public/submissions/school-enrollment", {
        parent_name: data.get("parent_name"), contact: data.get("contact"), child_name: data.get("child_name"),
        child_age: Number(data.get("child_age")), comment: data.get("comment") || "",
        consent: data.get("consent") === "on", website: data.get("website") || "",
      });
      form.querySelector("[data-school-reference]").textContent = result.reference_code;
      form.querySelector("[data-school-success]").hidden = false;
      form.classList.add("is-success");
      dialog.dataset.completed = "true";
      form.querySelector("[data-school-success]").focus();
    } catch (error) {
      showSubmissionError(form, "[data-school-error]", error);
    } finally {
      submit.disabled = false;
      submit.textContent = "Отправить заявку";
    }
  });
  reset();
}

function initialize() {
  document.querySelectorAll("[data-menu-toggle]").forEach(button => button.addEventListener("click", () => {
    setMenu(!document.body.classList.contains("menu-open"));
  }));
  document.querySelectorAll("[data-mobile-nav] a").forEach(link => {
    link.addEventListener("click", () => setMenu(false));
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") setMenu(false);
  });
  bindLightbox();
  bindPrayerNoteForm();
  bindSchoolForm();
}

runLegacyHashBridge();
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
else initialize();
