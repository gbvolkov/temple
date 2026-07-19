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
}

runLegacyHashBridge();
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
else initialize();
