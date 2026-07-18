const app = document.querySelector("#app");

const data = {
  schedule: {
    sunday: {
      label: "Воскресенье, 14 июля",
      services: [
        { time: "06:30", title: "Ранняя Божественная литургия", note: "Исповедь начинается в 06:15", tag: "Храм" },
        { time: "09:30", title: "Поздняя Божественная литургия", note: "После службы — молебен", tag: "Главная служба" },
        { time: "17:00", title: "Вечерня и утреня", note: "Полиелейная служба", tag: "Вечер" },
      ],
    },
    wednesday: {
      label: "Среда, 17 июля",
      services: [
        { time: "08:00", title: "Божественная литургия", note: "Исповедь с 07:30", tag: "Будний день" },
        { time: "17:00", title: "Молебен с акафистом", note: "Перед Казанской иконой Божией Матери", tag: "Акафист" },
      ],
    },
    saturday: {
      label: "Суббота, 20 июля",
      services: [
        { time: "08:00", title: "Божественная литургия и панихида", note: "Поминовение усопших", tag: "Суббота" },
        { time: "17:00", title: "Всенощное бдение", note: "Исповедь во время службы", tag: "К воскресенью" },
      ],
    },
  },
  albums: [
    { year: "2025", title: "Праздник Масленицы в Воскресной школе", date: "23 февраля 2025", image: "assets/school-maslenitsa.jpg", count: 38 },
    { year: "2025", title: "Сретение Господне", date: "15 февраля 2025", image: "assets/gallery-sretenie.jpg", count: 26 },
    { year: "2025", title: "Рождество Христово", date: "7 января 2025", image: "assets/gallery-christmas.jpg", count: 42 },
    { year: "2024", title: "Молодёжное движение прихода", date: "29 декабря 2024", image: "assets/parish-youth.jpg", count: 31 },
    { year: "2024", title: "Социальная служба: бесплатный магазин", date: "24 ноября 2024", image: "assets/parish-social.jpg", count: 24 },
    { year: "2023", title: "Богослужение в храме", date: "12 июля 2023", image: "assets/temple-history-012.jpg", count: 18 },
  ],
  issues: [
    { year: "2026", number: "148", period: "Май — июль 2026", pages: "20 страниц", cover: "assets/leaflet-148.jpg" },
    { year: "2026", number: "147", period: "Апрель 2026", pages: "16 страниц" },
    { year: "2026", number: "146", period: "Январь 2026", pages: "16 страниц" },
    { year: "2025", number: "145", period: "Август — сентябрь 2025", pages: "20 страниц" },
    { year: "2025", number: "144", period: "Июнь — июль 2025", pages: "20 страниц" },
    { year: "2025", number: "143", period: "Апрель — май 2025", pages: "16 страниц" },
    { year: "2024", number: "142", period: "Май — июнь 2024", pages: "20 страниц" },
    { year: "2024", number: "141", period: "Март — апрель 2024", pages: "16 страниц" },
    { year: "2024", number: "140", period: "Январь — февраль 2024", pages: "16 страниц" },
  ],
};

const state = { scheduleDay: "sunday", galleryYear: "2025", leafletYear: "2026", noteStep: 1, published: [] };

function escapeHtml(value = "") {
  return String(value).replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function contentImage(value) {
  return typeof value === "string" && (value.startsWith("assets/") || value.startsWith("/media/")) ? value : "assets/parish-social.jpg";
}

function contentFile(value) {
  return typeof value === "string" && (value.startsWith("assets/") || value.startsWith("/media/")) ? value : "";
}

function publishedHomeFeature() {
  const now = Date.now();
  const dedicated = state.published
    .filter(item => item.content_type === "home_feature")
    .filter(item => {
      const startsAt = item.data?.starts_at ? new Date(item.data.starts_at).valueOf() : null;
      const endsAt = item.data?.ends_at ? new Date(item.data.ends_at).valueOf() : null;
      return (!Number.isFinite(startsAt) || startsAt <= now) && (!Number.isFinite(endsAt) || endsAt >= now);
    })
    .sort((a, b) => Number(b.data?.priority || 0) - Number(a.data?.priority || 0) || String(b.published_at || "").localeCompare(String(a.published_at || "")))[0];
  if (dedicated) return dedicated;
  return state.published.find(item => item.content_type === "news" && item.data?.featured) || {
    content_type: "home_feature",
    title: "Воскресный день в храме",
    slug: "voskresnyy-den-v-hrame",
    data: {
      kicker: "Главное",
      summary: "Расписание служб, подготовка к Причастию и всё необходимое для первого посещения.",
      cover: "assets/home-hero.jpg",
      cover_alt: "Богослужение в храме святителя Иннокентия",
      target_url: "#/schedule",
    },
  };
}

function homeFeatureHref(item) {
  const target = String(item.data?.target_url || "");
  if (target.startsWith("#/") || target.startsWith("https://") || target.startsWith("http://")) return target;
  if (item.data?.content_slug) return `#/content/${encodeURIComponent(item.data.content_slug)}`;
  if (item.content_type === "news" && item.slug) return `#/news/${encodeURIComponent(item.slug)}`;
  return "#/schedule";
}

function nextServiceSummary() {
  const published = state.published
    .filter(item => item.content_type === "service" && item.data?.starts_at)
    .map(item => ({ item, start: new Date(item.data.starts_at) }))
    .filter(entry => !Number.isNaN(entry.start.valueOf()) && entry.start.valueOf() >= Date.now())
    .sort((a, b) => a.start - b.start)[0];
  if (published) {
    return {
      date: published.start.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" }),
      headerDate: published.start.toLocaleDateString("ru-RU", { day: "numeric", month: "long" }),
      shortDate: published.start.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }),
      time: published.start.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }),
      title: published.item.title,
      note: published.item.data.note || "Подробности опубликованы в расписании.",
    };
  }
  return { date: "Воскресенье, 14 июля", headerDate: "14 июля", shortDate: "14.07", time: "06:30", title: "Божественная литургия", note: "Исповедь начинается в 06:15" };
}

function updateHeaderService() {
  const service = nextServiceSummary();
  const link = document.querySelector(".header-schedule");
  if (!link) return;
  link.querySelector("[data-header-service-date]").textContent = service.headerDate || service.date;
  link.querySelector("[data-header-service-date-short]").textContent = service.shortDate || service.headerDate || service.date;
  link.querySelector("[data-header-service-time]").textContent = service.time;
  link.setAttribute("aria-label", `Ближайшая служба ${service.headerDate || service.date} в ${service.time}. Открыть расписание`);
}

function homeNewsItems() {
  return state.published.filter(item => item.content_type === "news").slice(0, 3).map(item => ({
    date: item.data?.publication_date ? new Date(item.data.publication_date).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }) : "Сегодня",
    title: item.title,
    href: `#/news/${encodeURIComponent(item.slug)}`,
  }));
}

function publishedContact() {
  return state.published.find(item => item.content_type === "site_contact")?.data || {
    address: "Москва, Бескудниковский бульвар, 1", metro: "Верхние Лихоборы, северный вестибюль, выход №3",
    phone: "+7 (499) 480-09-89", email: "svtinnokentiy2025@yandex.ru",
    social_links: [{ network: "telegram", url: "https://t.me/sv_innokenty", enabled: true }, { network: "vk", url: "https://vk.com/club37731945", enabled: true }],
  };
}

function phoneHref(phone) { return `tel:${String(phone || "").replace(/[^+\d]/g, "")}`; }
function socialLinksHtml() {
  const labels = { telegram: "Telegram", vk: "ВКонтакте", youtube: "YouTube", other: "Ссылка" };
  return (publishedContact().social_links || []).filter(item => item.enabled !== false).map(item => `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${labels[item.network] || labels.other}</a>`).join(" · ");
}

const searchItems = [
  ["Расписание богослужений", "Время ближайших служб", "#/schedule"],
  ["Подать записку", "О здравии, об упокоении и молебен", "note"],
  ["Фотогалерея", "Альбомы прихода по годам", "#/gallery"],
  ["Иннокентиевский листок", "PDF-архив с 2006 года", "#/leaflet"],
  ["Воскресная школа", "Программа, расписание и новости", "#/school"],
  ["Социальная служба", "Помощь прихожанам и благотворительные акции", "#/parish"],
  ["Духовенство", "Священнослужители храма", "#/about"],
  ["Как добраться", "Адрес, метро и часы работы", "#/about#contacts"],
];

function hero(title, description, eyebrow = "Храм святителя Иннокентия") {
  return `<section class="inner-hero"><div class="shell inner-hero__grid"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1></div><p>${description}</p></div></section>`;
}

function renderHome() {
  const feature = publishedHomeFeature();
  const featureData = feature.data || {};
  const service = nextServiceSummary();
  const news = homeNewsItems();
  const newsMarkup = news.length
    ? news.map(item => `<article><time>${escapeHtml(item.date)}</time><h3><a href="${escapeHtml(item.href)}">${escapeHtml(item.title)}</a></h3><a class="text-link" href="${escapeHtml(item.href)}">Подробнее</a></article>`).join("")
    : `<div class="empty home-news__empty"><h3>Новости готовятся к публикации</h3><p>Проверенные материалы появятся здесь после редакторской приёмки.</p></div>`;
  return `
    <section class="home-hero">
      <img class="home-hero__image" src="${contentImage(featureData.cover || "assets/home-hero.jpg")}" alt="${escapeHtml(featureData.cover_alt || feature.title)}">
      <div class="shell home-hero__content">
        <div class="home-feature">
          <div class="eyebrow eyebrow--light">${escapeHtml(featureData.kicker || "Главное")}</div>
          <h1>${escapeHtml(feature.title)}</h1>
          <p>${escapeHtml(featureData.summary || "Важный материал прихода")}</p>
          <a class="button button--primary" href="${escapeHtml(homeFeatureHref(feature))}">${escapeHtml(featureData.cta_label || "Прочитать")}</a>
        </div>
        <aside class="home-service" aria-label="Ближайшее богослужение">
          <div class="eyebrow eyebrow--light">Ближайшая служба</div>
          <div class="home-service__date">${escapeHtml(service.date)}</div>
          <div class="home-service__time">${escapeHtml(service.time)} · ${escapeHtml(service.title)}</div>
          <p>${escapeHtml(service.note)}</p>
          <a class="text-link" href="#/schedule">Полное расписание</a>
        </aside>
      </div>
    </section>
    <nav class="home-shortcuts" aria-label="Быстрые действия"><div class="shell home-shortcuts__inner">
      <a href="#/schedule"><b>Расписание</b><span>Богослужения на неделю</span></a>
      <button type="button" data-note-open><b>Подать записку</b><span>О здравии и упокоении</span></button>
      <a href="#/about#contacts"><b>Как добраться</b><span>Адрес и маршрут</span></a>
      <a href="#/media"><b>Трансляция</b><span>Прямой эфир и архив</span></a>
    </div></nav>
    <section class="home-editorial"><div class="shell">
      <div class="home-section-head"><div><div class="eyebrow">Дела милосердия</div><h2>Приход помогает тем, кто рядом</h2></div><p>Социальная служба принимает одежду, продукты и школьные принадлежности для семей, которым сейчас необходима поддержка.</p></div>
      <article class="home-story"><img src="assets/parish-social.jpg" alt="Социальная служба прихода"><div class="home-story__copy"><div class="eyebrow">Социальная служба</div><h3>Продолжается сбор помощи многодетным семьям</h3><p>Вещи можно принести по воскресеньям после поздней литургии. Перед поездкой проверьте актуальный список необходимого.</p><a class="text-link" href="#/parish">Что сейчас необходимо</a></div></article>
    </div></section>
    <section class="home-news"><div class="shell"><div class="home-section-head"><div><div class="eyebrow">Новости и анонсы</div><h2>Жизнь прихода</h2></div><a class="text-link" href="#/parish">Все материалы</a></div><div class="home-news-list">${newsMarkup}</div></div></section>
    <section class="home-photo-band"><img src="assets/parish-youth.jpg" alt="Молодёжное движение прихода"><div class="home-photo-band__copy"><div class="eyebrow eyebrow--light">Жизнь прихода</div><h2>Храм — это люди</h2><p>Молодёжные встречи, социальное служение, воскресная школа, паломничества и общие праздники.</p><a class="button button--light" href="#/parish">Все направления</a></div></section>
    <section class="home-leaflet"><div class="shell home-leaflet__inner"><img src="assets/leaflet-148.jpg" alt="Обложка Иннокентиевского листка №148"><div><div class="eyebrow">Иннокентиевский листок</div><h2>№ 148 · май — июль 2026</h2><p>Новый выпуск приходского издания и полный PDF-архив с 2006 года.</p><div class="home-leaflet__actions"><a class="button button--primary" href="#/leaflet">Открыть выпуск</a><a class="text-link" href="#/leaflet">Весь архив</a></div></div></div></section>
    <section class="home-contact"><div class="shell home-contact__inner"><div><div class="eyebrow">Контакты</div><h2>Рядом с метро Верхние Лихоборы</h2><p>${escapeHtml(publishedContact().address)}</p></div><div class="home-contact__details"><div><small>Телефон</small><a href="${phoneHref(publishedContact().phone)}">${escapeHtml(publishedContact().phone)}</a></div><div><small>Социальные сети</small>${socialLinksHtml()}</div><a class="button button--primary" href="#/about#contacts">Маршрут и реквизиты</a></div></div></section>`;
}

function renderSchedule() {
  const current = data.schedule[state.scheduleDay];
  const liveServices = state.published.filter(item => item.content_type === "service").sort((a,b)=>String(a.data.starts_at).localeCompare(String(b.data.starts_at)));
  const liveMarkup = liveServices.length ? `<div class="cms-live-schedule"><div class="eyebrow">Опубликовано из CMS</div><h2>Актуальные службы</h2><div class="schedule-list">${liveServices.map(item => { const start = new Date(item.data.starts_at); return `<article class="service-row"><div class="service-row__time">${start.toLocaleTimeString("ru-RU", {hour:"2-digit",minute:"2-digit"})}</div><div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.data.note || start.toLocaleDateString("ru-RU", {day:"numeric",month:"long"}))}</p></div><div class="service-row__tag">${escapeHtml(item.data.service_type || "Служба")}</div></article>`; }).join("")}</div></div>` : "";
  return `${hero("Расписание богослужений", "Структурированное расписание удобно читать на экране и обновлять в CMS без ручной вёрстки.", "Богослужения")}
    <section class="section section--white"><div class="shell schedule-layout"><div>
      ${liveMarkup}
      <div class="subnav" role="tablist" aria-label="Дни расписания">
        ${[["sunday","Воскресенье, 14 июля"],["wednesday","Среда, 17 июля"],["saturday","Суббота, 20 июля"]].map(([key,label])=>`<button class="filter-button ${state.scheduleDay===key?"is-active":""}" type="button" role="tab" aria-selected="${state.scheduleDay===key}" data-schedule-day="${key}">${label}</button>`).join("")}
      </div>
      <div class="eyebrow">${current.label}</div><h2>Службы дня</h2>
      <div class="schedule-list">${current.services.map(item=>`<article class="service-row"><div class="service-row__time">${item.time}</div><div><h3>${item.title}</h3><p>${item.note}</p></div><div class="service-row__tag">${item.tag}</div></article>`).join("")}</div>
    </div><aside class="aside-card"><div class="eyebrow">Полезно знать</div><h2>Перед службой</h2><ul><li>Исповедь начинается заранее.</li><li>Изменения публикуются в этом разделе и Telegram.</li><li>Расписание можно добавить в календарь.</li></ul><button class="button button--primary" type="button" data-note-open>Подать записку</button></aside></div></section>
    <section class="section"><div class="shell notice"><div class="notice__label">Архив</div><div><h2>Печатная версия расписания</h2><p>PDF остаётся доступным, но не заменяет структурированные данные страницы.</p></div><button class="button button--ghost" type="button" data-toast-message="В прототипе PDF не загружается">Скачать PDF</button></div></section>`;
}

function renderGallery() {
  const cmsAlbums = state.published.filter(item => item.content_type === "gallery").map(item => ({
    year: String(item.data.event_date || "").slice(0, 4), title: item.title,
    date: item.data.event_date ? new Date(item.data.event_date).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }) : "Дата не указана",
    image: contentImage(item.data.cover), count: item.data.photos?.length || 0, slug: item.slug,
  }));
  const allAlbums = [...cmsAlbums, ...data.albums];
  const years = [...new Set([...cmsAlbums.map(item => item.year).filter(Boolean), "2025", "2024", "2023"])];
  const albums = allAlbums.filter(a=>a.year===state.galleryYear);
  return `${hero("Фотогалерея", "Альбомы сгруппированы по годам и направлениям прихода; фотографии открываются без ухода со страницы.", "Медиа и архив")}
    <section class="section section--white"><div class="shell"><div class="subnav" aria-label="Фильтр по годам">${years.map(year=>`<button class="filter-button ${state.galleryYear===year?"is-active":""}" type="button" data-gallery-year="${year}">${year}</button>`).join("")}</div>
    <div class="album-grid">${albums.map(a=>{ const body = `<div class="album-card__media"><img src="${a.image}" alt="${escapeHtml(a.title)}"></div><div class="album-card__body"><div class="meta">${escapeHtml(a.date)}</div><h3>${escapeHtml(a.title)}</h3><p>${a.count} фотографий</p></div>`; return a.slug ? `<a class="album-card" href="#/content/${encodeURIComponent(a.slug)}">${body}</a>` : `<article class="album-card"><button type="button" data-lightbox-src="${a.image}" data-lightbox-caption="${escapeHtml(a.title)}">${body}</button></article>`; }).join("") || `<div class="empty">В этом году альбомы ещё не перенесены.</div>`}</div></div></section>`;
}

function renderLeaflet() {
  const cmsIssues = state.published.filter(item => item.content_type === "leaflet_issue").map(item => ({ year: String(item.data.year || String(item.data.publication_date).slice(0,4)), number: String(item.data.number), period: item.data.period, pages: "PDF", cover: item.data.cover, pdf: item.data.pdf }));
  const allIssues = [...cmsIssues, ...data.issues.filter(item => !cmsIssues.some(cms => cms.number === item.number))];
  const years = [...new Set([...cmsIssues.map(item => item.year).filter(Boolean), "2026", "2025", "2024"])];
  const issues = allIssues.filter(i=>i.year===state.leafletYear);
  const latest = allIssues[0] || data.issues[0];
  return `${hero("Иннокентиевский листок", "Обложка, номер, период и PDF каждого выпуска хранятся отдельными понятными полями.", "Приходское издание")}
    <section class="section section--white"><div class="shell issue-layout"><aside class="issue-latest"><div class="eyebrow">Последний выпуск</div><h2>№ ${escapeHtml(latest.number)}</h2><img src="${contentImage(latest.cover || "assets/leaflet-148.jpg")}" alt="Обложка выпуска №${escapeHtml(latest.number)}"><p>${escapeHtml(latest.period)}</p>${latest.pdf ? `<a class="button button--primary" href="${escapeHtml(latest.pdf)}" target="_blank" rel="noreferrer">Открыть PDF</a>` : `<button class="button button--primary" type="button" data-toast-message="PDF будет подключён после проверки миграции">Открыть PDF</button>`}</aside><div><div class="subnav" aria-label="Фильтр выпусков по годам">${years.map(year=>`<button class="filter-button ${state.leafletYear===year?"is-active":""}" type="button" data-leaflet-year="${year}">${year}</button>`).join("")}</div><div class="eyebrow">Архив ${state.leafletYear}</div><h2>Выпуски года</h2><div class="issue-list">${issues.map(i=>`<article class="issue-row"><div class="issue-row__number">№ ${escapeHtml(i.number)}</div><div><h3>${escapeHtml(i.period)}</h3><p>${escapeHtml(i.pages)}</p></div>${i.pdf ? `<a class="button button--ghost button--compact" href="${escapeHtml(i.pdf)}" target="_blank" rel="noreferrer">PDF</a>` : `<button class="button button--ghost button--compact" type="button" data-toast-message="PDF выпуска № ${escapeHtml(i.number)} будет подключён после миграции">PDF</button>`}</article>`).join("")}</div></div></div></section>
    <section class="section section--sky"><div class="shell"><div class="section-head"><div><div class="eyebrow">Полный архив</div><h2>Выпуски с 2006 года</h2></div><p>В рабочей CMS появятся фильтр по году, номеру и полнотекстовый поиск по метаданным.</p></div></div></section>`;
}

function renderSchool() {
  return `${hero("Воскресная школа", "Расписание, новости и фотографии школы собраны в одном разделе, понятном родителям.", "Для детей и родителей")}
    <section class="section section--white"><div class="shell school-intro"><div><div class="eyebrow">О школе</div><h2>Учимся вере через общение и общее дело</h2><p>Занятия проходят по воскресеньям после Божественной литургии. Программа включает Закон Божий, церковное пение, творчество и совместные праздники.</p><button class="button button--primary" type="button" data-toast-message="Форма записи будет подключена к новой CMS">Записать ребёнка</button></div><div class="school-collage"><img src="assets/school-maslenitsa.jpg" alt="Масленица в Воскресной школе"><img src="assets/school-defender.jpg" alt="Праздник в младшей группе"><img src="assets/school-christmas.jpg" alt="Рождественский праздник"></div></div></section>
    <section class="section"><div class="shell"><div class="section-head"><div><div class="eyebrow">2025–2026 учебный год</div><h2>Расписание занятий</h2></div><p>На смартфоне таблица автоматически превращается в читаемые карточки.</p></div><table class="timetable"><thead><tr><th>Группа</th><th>Время</th><th>Занятие</th><th>Преподаватель</th></tr></thead><tbody><tr><td data-label="Группа">Младшая</td><td data-label="Время">10:15–11:00</td><td data-label="Занятие">Основы веры и творчество</td><td data-label="Преподаватель">Елена Алексеевна</td></tr><tr><td data-label="Группа">Средняя</td><td data-label="Время">11:15–12:00</td><td data-label="Занятие">Закон Божий</td><td data-label="Преподаватель">Преподаватель школы</td></tr><tr><td data-label="Группа">Хор</td><td data-label="Время">Суббота, 08:40</td><td data-label="Занятие">Хоровая практика</td><td data-label="Преподаватель">Регент</td></tr></tbody></table></div></section>
    <section class="section section--white"><div class="shell"><div class="section-head"><div><div class="eyebrow">Новости школы</div><h2>Последние события</h2></div><a class="button button--ghost" href="#/gallery">Фотоальбомы школы</a></div><div class="album-grid">${data.albums.filter(a=>a.title.includes("школ")||a.title.includes("Рождество")).slice(0,3).map(a=>`<a class="album-card" href="#/gallery"><div class="album-card__media"><img src="${a.image}" alt="${a.title}"></div><div class="album-card__body"><div class="meta">${a.date}</div><h3>${a.title}</h3></div></a>`).join("")}</div></div></section>`;
}

function renderParish() {
  const directions = [
    ["Молодёжное движение","Встречи, волонтёрство и поездки","assets/parish-youth.jpg"],
    ["Социальная служба","Помощь семьям и благотворительные акции","assets/parish-social.jpg"],
    ["Паломническая служба","Поездки по святым местам","assets/temple-history-010.jpg"],
    ["Молодёжный церковный хор","Пение на богослужениях и репетиции","assets/temple-history-012.jpg"],
    ["Театральный кружок","Спектакли и приходские праздники","assets/school-christmas.jpg"],
    ["Спортивные занятия","Рукопашный бой и совместные тренировки","assets/school-defender.jpg"],
  ];
  return `${hero("Жизнь прихода", "Каждое направление получает собственную страницу, контакты, новости и связанные фотоальбомы.", "Сообщество")}
    <section class="section section--white"><div class="shell"><div class="direction-grid">${directions.map(d=>`<article class="direction-card"><div class="direction-card__media"><img src="${d[2]}" alt="${d[0]}"></div><div class="direction-card__body"><div class="meta">Направление прихода</div><h3>${d[0]}</h3><p>${d[1]}</p><button class="text-link footer-button" type="button" data-toast-message="В полном прототипе откроется страница направления">Подробнее →</button></div></article>`).join("")}</div></div></section>`;
}

function renderAbout() {
  return `${hero("О храме", "История, духовенство, святыни и контакты больше не смешиваются с новостной лентой.", "Храм святителя Иннокентия")}
    <section class="section section--white"><div class="shell history-grid"><img class="archive-photo" src="assets/temple-history-013.jpg" alt="Архивная фотография приходской жизни"><div class="prose"><div class="eyebrow">Летопись прихода</div><h2>История начинается в 1996 году</h2><p>Инициатива строительства храма в Бескудникове возникла как ответ на потребность жителей большого района в доступном месте молитвы. Храм был освящён в честь святителя Иннокентия, митрополита Московского.</p><a class="text-link" href="#/media">Архивные фотографии →</a></div></div></section>
    <section class="section"><div class="shell"><div class="section-head"><div><div class="eyebrow">Духовенство</div><h2>Священнослужители храма</h2></div></div><div class="clergy-grid"><article class="clergy-card"><div class="clergy-card__avatar">МД</div><h3>Протоиерей Михаил Дудко</h3><p>Настоятель храма</p></article><article class="clergy-card"><div class="clergy-card__avatar">АЕ</div><h3>Иерей Алексий Есипов</h3><p>Клирик храма</p></article><article class="clergy-card"><div class="clergy-card__avatar">ГС</div><h3>Иерей Глеб Седов</h3><p>Клирик храма</p></article></div></div></section>
    <section class="section section--sky"><div class="shell"><div class="section-head"><div><div class="eyebrow">Святыни</div><h2>Святитель Иннокентий и святыни храма</h2></div><p>Житие, молитвы и исторические статьи становятся связанными, но самостоятельными материалами.</p></div></div></section>
    <section class="section section--white" id="contacts"><div class="shell contact-panel"><div><div class="eyebrow">Контакты</div><h2>Как добраться</h2><div class="contact-list"><div class="contact-row"><small>Адрес</small>${escapeHtml(publishedContact().address)}</div><div class="contact-row"><small>Метро</small>${escapeHtml(publishedContact().metro || "Верхние Лихоборы")}</div><div class="contact-row"><small>Телефон</small><a href="${phoneHref(publishedContact().phone)}">${escapeHtml(publishedContact().phone)}</a></div><div class="contact-row"><small>Электронная почта</small><a href="mailto:${escapeHtml(publishedContact().email)}">${escapeHtml(publishedContact().email)}</a></div><div class="contact-row"><small>Социальные сети</small>${socialLinksHtml()}</div></div></div><div class="map-card" aria-label="Схематическая карта"><span class="map-pin"></span></div></div></section>`;
}

function renderMedia() {
  return `${hero("Медиа и архив", "Фотографии, приходской листок, трансляции и поиск собраны в одном разделе.", "Медиа")}
    <section class="section section--white"><div class="shell direction-grid"><a class="direction-card direction-card--text" href="#/gallery"><div class="direction-card__body"><div class="meta">Фото</div><h3>Фотогалерея</h3><p>Альбомы по годам, событиям и направлениям прихода.</p></div><span class="text-link">Открыть →</span></a><a class="direction-card direction-card--text" href="#/leaflet"><div class="direction-card__body"><div class="meta">PDF-архив</div><h3>Иннокентиевский листок</h3><p>Выпуски с 2006 года, обложки и приложения.</p></div><span class="text-link">Открыть →</span></a><article class="direction-card direction-card--text"><div class="direction-card__body"><div class="meta">Видео</div><h3>Трансляции</h3><p>Прямой эфир и избранные записи богослужений.</p></div><a class="text-link" href="https://www.youtube.com/channel/UCZAWBWuzSLA5KcNmEXDM0gA/" target="_blank" rel="noreferrer">YouTube ↗</a></article></div></div></section>`;
}

const contentTypeLabels = {
  home_feature: "Главное",
  news: "Новости прихода",
  gallery: "Фотогалерея",
  leaflet_issue: "Иннокентиевский листок",
  clergy: "Духовенство",
  parish_section: "Жизнь прихода",
  page: "О храме",
  site_contact: "Контакты",
  service: "Богослужения",
  video: "Видео и трансляции",
};

function plainContent(value) {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(plainContent).filter(Boolean).join("\n\n");
  if (value && typeof value === "object") return plainContent(value.text ?? value.value ?? value.body ?? "");
  return "";
}

function textContentHtml(value) {
  const normalized = String(value || "").trim();
  if (!normalized) return "";
  return normalized.split(/\n\s*\n/).map(paragraph => `<p>${escapeHtml(paragraph).replace(/\n/g, "<br>")}</p>`).join("");
}

function renderContentDetail(item) {
  const details = item.data || {};
  const label = contentTypeLabels[item.content_type] || "Материал";
  const summary = details.summary || details.note || "Материал из архива прихода";
  const rawCover = details.cover || details.photo || details.legacy_images?.[0]?.image || "";
  const body = plainContent(details.body) || plainContent(details.biography) || details.body_text || "";
  const photos = (details.photos || details.legacy_images || []).filter(photo => photo && typeof photo.image === "string");
  const pdf = contentFile(details.pdf);
  const dateValue = details.publication_date || details.event_date || details.starts_at || "";
  const date = dateValue ? new Date(dateValue).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }) : "";
  const backRoute = item.content_type === "home_feature" ? "#/" : item.content_type === "gallery" ? "#/gallery" : item.content_type === "leaflet_issue" ? "#/leaflet" : item.content_type === "parish_section" ? "#/parish" : "#/about";
  const gallery = photos.length ? `<div class="article-gallery">${photos.map(photo => {
    const src = contentImage(photo.image);
    const caption = photo.alt || photo.caption || item.title;
    return `<button type="button" data-lightbox-src="${escapeHtml(src)}" data-lightbox-caption="${escapeHtml(caption)}"><img src="${escapeHtml(src)}" alt="${escapeHtml(caption)}"></button>`;
  }).join("")}</div>` : "";
  return `${hero(escapeHtml(item.title), escapeHtml(summary), escapeHtml(label))}
    <section class="section section--white"><div class="shell article-layout"><article class="article-body">
      ${rawCover && item.content_type !== "gallery" ? `<img class="article-cover" src="${escapeHtml(contentImage(rawCover))}" alt="${escapeHtml(item.title)}">` : ""}
      ${date ? `<div class="meta">${escapeHtml(date)}</div>` : ""}
      ${body ? `<div class="prose article-prose">${textContentHtml(body)}</div>` : summary ? `<p>${escapeHtml(summary)}</p>` : ""}
      ${gallery}
      ${pdf ? `<p><a class="button button--primary" href="${escapeHtml(pdf)}" target="_blank" rel="noopener">Открыть PDF ↗</a></p>` : ""}
    </article><aside class="aside-card"><div class="eyebrow">${escapeHtml(label)}</div><p>${date ? escapeHtml(date) : "Опубликовано на новом сайте"}</p><a class="text-link" href="${backRoute}">← Вернуться в раздел</a></aside></div></section>`;
}

function renderNewsDetail(slug) {
  const item = state.published.find(entry => entry.content_type === "news" && entry.slug === slug);
  if (!item) return `${hero("Материал не найден", "Возможно, публикация ещё не загружена или была снята с публикации.", "Новости")}`;
  const data = item.data || {};
  return `${hero(escapeHtml(item.title), escapeHtml(data.summary || "Новость прихода"), escapeHtml(data.category || "Новости"))}
    <section class="section section--white"><div class="shell article-layout"><article class="article-body"><img class="article-cover" src="${contentImage(data.cover)}" alt="${escapeHtml(data.cover_alt || item.title)}"><p>${escapeHtml(data.summary || "")}</p></article><aside class="aside-card"><div class="eyebrow">Публикация</div><p>${data.publication_date ? new Date(data.publication_date).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" }) : "Дата не указана"}</p><a class="text-link" href="#/">← На главную</a></aside></div></section>`;
}

const routes = { schedule: renderSchedule, gallery: renderGallery, leaflet: renderLeaflet, school: renderSchool, parish: renderParish, about: renderAbout, media: renderMedia };

function routeKey() {
  const value = location.hash.replace(/^#\//, "").split("#")[0].split("?")[0];
  return value || "home";
}

function finishRoute(key) {
  document.querySelectorAll("[data-nav]").forEach(link => link.classList.toggle("is-active", link.dataset.nav === key || (link.dataset.nav === "media" && ["gallery","leaflet"].includes(key))));
  document.title = `${key === "home" ? "Главная" : app.querySelector("h1")?.textContent || "Храм"} | Храм святителя Иннокентия`;
  document.body.classList.toggle("is-home", key === "home");
  document.body.classList.remove("menu-open", "mega-open");
  syncMenuButton(false);
  updateHeaderService();
  document.querySelectorAll(".primary-nav__link").forEach(link => {
    if (link.getAttribute("href") === `#/${key}`) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  document.querySelector("[data-mega-toggle]")?.setAttribute("aria-expanded", "false");
  window.scrollTo({ top: 0, behavior: "instant" });
  requestAnimationFrame(() => {
    if (location.hash.includes("#contacts")) document.querySelector("#contacts")?.scrollIntoView();
  });
}

function syncMenuButton(open) {
  document.querySelectorAll("[data-menu-toggle]").forEach(button => {
    button.setAttribute("aria-expanded", String(open));
    button.setAttribute("aria-label", open ? "Закрыть все разделы" : "Открыть все разделы");
  });
}

async function renderContentRoute(key) {
  const slug = decodeURIComponent(key.slice("content/".length));
  const known = state.published.find(item => item.slug === slug);
  app.innerHTML = hero("Загружаем материал…", "Пожалуйста, подождите несколько секунд.", "Архив прихода");
  finishRoute(key);
  try {
    const item = known || await fetch(`/api/public/content/${encodeURIComponent(slug)}`, { credentials: "same-origin" }).then(response => {
      if (!response.ok) throw new Error("not-found");
      return response.json();
    });
    if (routeKey() !== key) return;
    app.innerHTML = renderContentDetail(item);
  } catch (_) {
    if (routeKey() !== key) return;
    app.innerHTML = hero("Материал не найден", "Возможно, он ещё не прошёл редакторскую проверку или был снят с публикации.", "Архив прихода");
  }
  finishRoute(key);
}

function renderRoute() {
  const key = routeKey();
  if (key.startsWith("content/")) {
    renderContentRoute(key);
    return;
  }
  app.innerHTML = key === "home" ? renderHome() : key.startsWith("news/") ? renderNewsDetail(decodeURIComponent(key.slice(5))) : (routes[key]?.() || renderHome());
  finishRoute(key);
}

function openNoteDialog() {
  state.noteStep = 1;
  updateNoteSteps();
  document.querySelector("[data-note-dialog]").showModal();
}

function updateNoteSteps() {
  document.querySelectorAll("[data-note-step]").forEach(step => step.hidden = Number(step.dataset.noteStep) !== state.noteStep);
  if (state.noteStep === 3) {
    const form = document.querySelector("[data-note-form]");
    const type = new FormData(form).get("noteType");
    const names = form.elements.names.value.trim() || "Имена не указаны";
    document.querySelector("[data-note-summary]").innerHTML = `<strong>${type}</strong><p>${names}</p>`;
  }
}

function showToast(message) {
  const toast = document.querySelector("[data-toast]");
  toast.textContent = message;
  toast.classList.add("is-visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("is-visible"), 2800);
}

function renderSearch(query = "") {
  const container = document.querySelector("[data-search-results]");
  const normalized = query.trim().toLowerCase();
  const found = normalized ? searchItems.filter(item => `${item[0]} ${item[1]}`.toLowerCase().includes(normalized)) : searchItems.slice(0, 5);
  container.innerHTML = found.length ? found.map(item => `<a class="search-result" href="${item[2] === "note" ? "#" : item[2]}" ${item[2] === "note" ? "data-note-open" : ""}><strong>${item[0]}</strong><br><small>${item[1]}</small></a>`).join("") : `<div class="empty">Ничего не найдено. Попробуйте другое слово.</div>`;
}

document.addEventListener("click", event => {
  const target = event.target.closest("button,a");
  if (!target) {
    if (!event.target.closest("[data-header]")) document.body.classList.remove("mega-open");
    return;
  }
  if (target.matches("[data-mega-toggle]")) {
    const open = document.body.classList.toggle("mega-open");
    document.body.classList.remove("menu-open");
    target.setAttribute("aria-expanded", String(open));
  }
  if (target.matches("[data-menu-toggle]")) {
    const open = document.body.classList.toggle("menu-open");
    document.body.classList.remove("mega-open");
    syncMenuButton(open);
  }
  if (target.closest("[data-mega-menu]") && !target.matches("[data-mega-toggle]")) {
    document.body.classList.remove("mega-open");
    document.querySelector("[data-mega-toggle]")?.setAttribute("aria-expanded", "false");
  }
  if (target.closest("[data-mobile-nav]") && (target.tagName === "A" || target.matches("[data-note-open],[data-search-open]"))) {
    document.body.classList.remove("menu-open");
    syncMenuButton(false);
  }
  if (target.matches("[data-note-open]")) { event.preventDefault(); document.querySelector("[data-search-dialog]")?.close(); openNoteDialog(); }
  if (target.matches("[data-note-close]")) document.querySelector("[data-note-dialog]").close();
  if (target.matches("[data-note-next]")) { const names = document.querySelector("[name=names]"); if (state.noteStep === 2 && !names.value.trim()) { names.focus(); names.setCustomValidity("Укажите хотя бы одно имя"); names.reportValidity(); return; } names?.setCustomValidity(""); state.noteStep = Math.min(3, state.noteStep + 1); updateNoteSteps(); }
  if (target.matches("[data-note-back]")) { state.noteStep = Math.max(1, state.noteStep - 1); updateNoteSteps(); }
  if (target.matches("[data-note-submit]")) { showToast("Прототип: записка не отправлена, данные не сохранены"); }
  if (target.matches("[data-search-open]")) { const dialog = document.querySelector("[data-search-dialog]"); renderSearch(); dialog.showModal(); setTimeout(()=>dialog.querySelector("input").focus(), 20); }
  if (target.matches("[data-search-close]")) document.querySelector("[data-search-dialog]").close();
  if (target.matches(".search-result")) document.querySelector("[data-search-dialog]").close();
  if (target.matches("[data-schedule-day]")) { state.scheduleDay = target.dataset.scheduleDay; app.innerHTML = renderSchedule(); }
  if (target.matches("[data-gallery-year]")) { state.galleryYear = target.dataset.galleryYear; app.innerHTML = renderGallery(); }
  if (target.matches("[data-leaflet-year]")) { state.leafletYear = target.dataset.leafletYear; app.innerHTML = renderLeaflet(); }
  if (target.matches("[data-lightbox-src]")) { const dialog = document.querySelector("[data-lightbox]"); const image = dialog.querySelector("[data-lightbox-image]"); image.src = target.dataset.lightboxSrc; image.alt = target.dataset.lightboxCaption || "Фотография"; dialog.querySelector("[data-lightbox-caption]").textContent = target.dataset.lightboxCaption || ""; dialog.showModal(); }
  if (target.matches("[data-lightbox-close]")) document.querySelector("[data-lightbox]").close();
  if (target.matches("[data-toast-message]")) showToast(target.dataset.toastMessage);
});

document.querySelector("[data-search-input]").addEventListener("input", event => renderSearch(event.target.value));
document.querySelector("[data-search-dialog]").addEventListener("click", event => { if (event.target.classList.contains("search-modal")) event.currentTarget.close(); });
document.querySelector("[data-lightbox]").addEventListener("click", event => { if (event.target.classList.contains("lightbox")) event.currentTarget.close(); });
document.addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  document.body.classList.remove("mega-open", "menu-open");
  document.querySelector("[data-mega-toggle]")?.setAttribute("aria-expanded", "false");
  syncMenuButton(false);
});
window.addEventListener("hashchange", renderRoute);
renderRoute();
fetch("/api/public/content?limit=200", { credentials: "same-origin" })
  .then(response => response.ok ? response.json() : [])
  .then(items => {
    state.published = items;
    const latestGallery = items.find(item => item.content_type === "gallery" && item.data?.event_date);
    if (latestGallery) state.galleryYear = String(latestGallery.data.event_date).slice(0, 4);
    const contact = publishedContact();
    const address = document.querySelector(".utility-bar__inner > span");
    if (address) address.textContent = contact.address;
    document.querySelectorAll('.utility-bar a[href^="tel:"]').forEach(link => { link.href = phoneHref(contact.phone); link.textContent = contact.phone; });
    renderRoute();
  })
  .catch(() => {});
