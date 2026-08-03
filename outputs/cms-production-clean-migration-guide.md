# Пошаговая миграция CMS в чистую production-среду

Дата: 1 августа 2026 года

Источник: `https://www.sv-innokenty.ru/`

Целевая версия БД: schema 13

## 1. Граница миграции

Переносятся:

- все публично доступные страницы, до которых доходит read-only Playwright-crawler;
- все публичные URL и создаваемые для них редиректы;
- все обнаруженные изображения, документы и публичные MP4-видео размером до
  целевого лимита 200 MiB;
- метаданные, извлекаемые из публичных страниц: заголовки, даты, разделы, текст, ссылки, изображения и документы;
- отчёт обо всех файлах, которые упомянуты источником, но уже отсутствуют на нём.

Не переносятся по подтверждённому решению владельца:

- закрытые черновики MODX;
- пользователи MODX;
- внутренняя история MODX.

Старый MODX используется только для чтения. Скрипты не входят в `/manager/`, не выполняют POST/PUT/PATCH/DELETE и не изменяют источник.
Браузерный контекст блокирует service workers и прерывает любой сетевой запрос,
метод которого не входит в `GET`, `HEAD`, `OPTIONS`; попытки записываются в
`blocked_write_requests` crawl-артефакта.

Поскольку пользователи источника не переносятся, первая учётная запись новой
CMS создаётся заново из `CMS_BOOTSTRAP_USER` и `CMS_BOOTSTRAP_PASSWORD`.
Публичные страницы MODX импортируются в целевую CMS со статусом `draft` только
как мера безопасной приёмки; это не означает перенос закрытых черновиков MODX.
Ревизии и audit-записи, создаваемые самим новым импортом и автоматической
подготовкой, являются новой технической историей, а не историей источника.

## 2. Схема процесса

Для нового выката поддерживается только связка
`build_public_source_bundle.ps1` → `production_bootstrap_clean.sh` →
`production_verify_clean.sh`. Сохранённые в репозитории скрипты с именами
`build_public_migration_bundle.sh` и
`production_bootstrap_from_public_bundle.sh` относятся к предыдущему формату
bundle и не должны подставляться вместо команд этой инструкции.

Граница технически доказуемого покрытия — весь текущий граф публичных ссылок
плюс 1 615 ранее известных публичных URL. На источнике нет рабочего
`/sitemap.xml` (на 1 августа 2026 года он отвечает 404). Если после исходной
инвентаризации в MODX создавались опубликованные страницы-сироты, на которые
не ведёт ни одна ссылка, до сборки нужен их список URL либо фильтрованный
экспорт только опубликованных ресурсов. Закрытые ресурсы, пользователи и
история в таком экспорте не нужны и не должны присутствовать.

1. На Windows выполняется свежий crawl публичного сайта; сохранённый inventory из 1 615 исторически известных публичных URL используется как seed, чтобы не потерять страницы-сироты, но их содержимое читается заново из MODX.
2. Все обнаруженные медиа скачиваются с лимитом 200 MiB на файл и
   дедуплицируются по SHA-256.
3. Из свежего crawl заново выделяются 16 структурных маршрутов; они создают основные страницы и выпуски приходского листка тем же способом, что при первоначальной миграции.
4. Формируются inventory, структурный dry-run, полный import plan и missing-media report.
5. Все артефакты упаковываются в неизменяемый `tar.gz`; рядом создаётся SHA-256 архива.
6. Архив и checksum копируются на Ubuntu.
7. Ubuntu проверяет архив, Git SHA и production `.env`.
8. Структурный и полный импорты выполняются последовательно в отдельном staging-каталоге, а не в `data/`.
9. На staging проходят schema 13, media reconciliation, FTS, полный migration audit, preparation preview/apply и semantic verify.
10. Только после успешных gate staging атомарно становится production `data/`.
11. При ошибке после активации скрипт удаляет неуспешный контейнер и возвращает исходный пустой `data/`; неуспешные данные сохраняются для расследования.

## 3. Часть 1 — Windows: формирование source-bundle

### 3.1. Требования

- Windows 10/11 x64;
- Git;
- PowerShell 5.1 или PowerShell 7;
- минимум 15 GiB свободного места;
- стабильный доступ к `https://www.sv-innokenty.ru/`;
- `uv`. Официальная установка: `winget install --id=astral-sh.uv -e` или инструкция `https://docs.astral.sh/uv/getting-started/installation/`.

Если Git или `uv` ещё не установлены, откройте PowerShell от имени обычного
пользователя и выполните:

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
```

После установки закройте и заново откройте PowerShell.

Проверьте инструменты:

```powershell
git --version
uv --version
tar --version
```

### 3.2. Получение точной версии кода

Windows-preprod **не должен использовать dev-checkout**
`C:\Projects\temple` и не должен сохранять runtime-данные или bundle внутри
репозитория. Для него используется отдельный корень со строго заданной
структурой:

```text
C:\CMS-Preprod\
├── release-checkout\       # отдельный чистый checkout main
├── runtime\                # отдельные venv, uv cache и Playwright browsers
└── source-bundles\         # crawl, media, tar.gz и checksum
```

Можно выбрать другой абсолютный путь или отдельный диск, но имя каталога
checkout должно оставаться `release-checkout`. В новом PowerShell задайте
корень и создайте **новый отдельный checkout**:

```powershell
$PreprodRoot = 'C:\CMS-Preprod'
$ReleaseCheckout = Join-Path $PreprodRoot 'release-checkout'
$BundleRoot = Join-Path $PreprodRoot 'source-bundles'

if (Test-Path -LiteralPath $ReleaseCheckout) {
    throw "Dedicated preprod checkout already exists: $ReleaseCheckout"
}
New-Item -ItemType Directory -Path $PreprodRoot -Force | Out-Null
git clone https://github.com/gbvolkov/temple.git $ReleaseCheckout
Set-Location $ReleaseCheckout
git checkout main
git pull --ff-only origin main
git status --short
```

`git status --short` не должен ничего вывести. Bundle запоминает текущий
commit; Ubuntu разрешит импорт только из того же commit. Dev-каталог при этих
действиях не читается и не изменяется.

### 3.3. Запуск полного read-only сбора

```powershell
$PreprodRoot = 'C:\CMS-Preprod'
$ReleaseCheckout = Join-Path $PreprodRoot 'release-checkout'
$BundleRoot = Join-Path $PreprodRoot 'source-bundles'
$BundleId = "public-source-{0}" -f (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')

Set-Location $ReleaseCheckout
powershell -ExecutionPolicy Bypass `
  -File .\scripts\build_public_source_bundle.ps1 `
  -PreprodRoot $PreprodRoot `
  -BundleId $BundleId

$BundleDir = Join-Path $BundleRoot $BundleId
$Archive = "$BundleDir.tar.gz"
$Checksum = "$Archive.sha256"
```

Скрипт прекращает работу, если он запущен не из
`<PreprodRoot>\release-checkout`. `uv`-кэш, виртуальная среда и браузер
Playwright принудительно размещаются в `<PreprodRoot>\runtime`, а результаты —
в `<PreprodRoot>\source-bundles`. Поэтому dev `.venv`, `output/` и рабочее
дерево не используются.

Скрипт самостоятельно:

1. синхронизирует группы `dev` и `crawl`;
2. устанавливает Chromium для Python Playwright;
3. запускает новый crawl с `--fresh`;
4. добавляет к очереди все ранее известные публичные URL из `outputs/legacy-inventory.json` и требует, чтобы каждый из них был повторно посещён;
5. требует пустую очередь и отсутствие crawler failures;
6. пересобирает структурные страницы и выпуски листка из свежих данных crawl, используя `current-sections.json` только как перечень обязательных маршрутов;
7. строит inventory и dry-run структурного импорта;
8. зеркалирует все обнаруженные медиа; временные сетевые ошибки и HTTP
   408/425/429/5xx повторяются до трёх раз с увеличивающейся задержкой, а
   постоянные 404/410 сразу классифицируются как `permanent_missing`;
9. строит полный import plan;
10. формирует missing-media report;
11. проверяет каждый зеркалированный файл по SHA-256 из media manifest;
12. создаёт manifest всех файлов bundle;
13. повторно проверяет bundle и упаковывает его.

Типовой результат:

```text
C:\CMS-Preprod\source-bundles\public-source-YYYYMMDDTHHMMSSZ.tar.gz
C:\CMS-Preprod\source-bundles\public-source-YYYYMMDDTHHMMSSZ.tar.gz.sha256
C:\CMS-Preprod\source-bundles\public-source-YYYYMMDDTHHMMSSZ\
```

Появление 404/410 в списке обследованных страниц или медиа не равно
технической ошибке: такие URL входят в `broken` и missing reports. Сетевое
исключение crawler, 401/403/429/5xx, timeout, превышение лимита 200 MiB,
незавершённая очередь, отсутствующий зеркалированный файл или несовпадение
SHA-256 останавливают сбор. Поэтому успешный bundle не может молча потерять
доступный файл.

Во время media mirror поле `errors` включает все ещё отсутствующие файлы.
Смотрите также отдельные счётчики: `permanent_missing` допустим для 404/410;
`retry_exhausted` и `fatal` должны завершиться нулём. `retried` показывает,
сколько файлов потребовали повторного сетевого запроса.

### 3.4. Возобновление незапечатанного bundle

Если сборка прервалась после crawl или во время/после media mirror, не удаляйте
каталог. После устранения причины обновите чистый `main` и возобновите тот же
`BundleId`:

```powershell
$PreprodRoot = 'C:\CMS-Preprod'
$ReleaseCheckout = Join-Path $PreprodRoot 'release-checkout'
$BundleId = 'public-source-YYYYMMDDTHHMMSSZ'

Set-Location $ReleaseCheckout
git pull --ff-only origin main
git status --short

powershell -ExecutionPolicy Bypass `
  -File .\scripts\build_public_source_bundle.ps1 `
  -PreprodRoot $PreprodRoot `
  -BundleId $BundleId `
  -Resume
```

`git status --short` должен быть пустым. Resume продолжает незавершённую crawl
queue, повторно использует успешно скачанные и проверяемые по manifest файлы,
повторяет только отсутствующие медиа, пересобирает производные отчёты и затем
запечатывает bundle. Уже созданные `source-bundle-manifest.json`, `tar.gz` или
checksum означают запечатанный bundle: его изменение и resume запрещены.

### 3.5. Повторная локальная проверка

Продолжайте в том же PowerShell. Если открыто новое окно, повторно задайте
`$PreprodRoot`, `$ReleaseCheckout`, `$BundleRoot`, `$BundleId`, `$BundleDir`,
`$Archive` и `$Checksum`. Проверка выполняется Python из изолированного
preprod-runtime:

```powershell
$PreprodPython = Join-Path $PreprodRoot 'runtime\venv\Scripts\python.exe'
Set-Location $ReleaseCheckout
& $PreprodPython -m scripts.validate_source_bundle verify $BundleDir

Get-FileHash -Algorithm SHA256 -LiteralPath $Archive

Get-Content -LiteralPath $Checksum
```

Хеши должны совпасть. Сохраните JSON-результат Windows-этапа вместе с релизными материалами.

### 3.6. Копирование на Ubuntu

Создайте закрытый каталог на сервере:

```powershell
ssh <ubuntu-user>@<ubuntu-host> "mkdir -p ~/migration-incoming && chmod 700 ~/migration-incoming"
```

Передайте ровно два файла:

```powershell
scp $Archive `
  <ubuntu-user>@<ubuntu-host>:~/migration-incoming/

scp $Checksum `
  <ubuntu-user>@<ubuntu-host>:~/migration-incoming/
```

Не распаковывайте и не изменяйте bundle вручную.

## 4. Часть 2 — Ubuntu: чистое развёртывание

### 4.1. Требования

- поддерживаемая Ubuntu x86_64;
- свободное место не меньше максимума из `10 GiB` и
  `3 × размер tar.gz + 2 GiB`; это же условие автоматически проверяет bootstrap;
- Git, curl, rsync, SQLite, tar, Python 3, `sha256sum`;
- Docker Engine и Docker Compose plugin из официального Docker repository. Актуальная инструкция: `https://docs.docker.com/engine/install/ubuntu/`;
- рабочий reverse proxy/TLS настраивается отдельно и этим rollout не изменяется.

Сначала установите системные утилиты:

```bash
sudo apt-get update
sudo apt-get install -y git curl rsync sqlite3 tar python3 coreutils ca-certificates
```

Установите Docker Engine и Compose plugin по указанной выше официальной
инструкции, если они ещё не установлены. Затем проверьте весь набор:

```bash
git --version
python3 --version
sqlite3 --version
sudo docker version
sudo docker compose version
sudo docker run --rm hello-world
```

### 4.2. Получение production checkout

```bash
cd "$HOME"
git clone https://github.com/gbvolkov/temple.git
cd "$HOME/temple"
git checkout main
git pull --ff-only origin main
git status --short
```

Вывод `git status --short` должен быть пустым. Каталог `data/` должен содержать только `.gitkeep`:

```bash
find data -mindepth 1 -maxdepth 2 -print
```

Если существует `data/cms.sqlite3`, файлы медиатеки или уже создан контейнер CMS, clean bootstrap завершится отказом. Для такого сервера применяется upgrade/restore workflow, а не эта инструкция.

### 4.3. Production `.env`

```bash
cd "$HOME/temple"
umask 077
cp .env.example .env
nano .env
chmod 600 .env
```

Обязательно замените placeholder-значения:

- `CMS_BOOTSTRAP_PASSWORD` — новый уникальный пароль не короче 16 символов;
- `SUBMISSION_IP_HASH_SECRET` — случайный секрет не короче 32 символов;
- `PUBLIC_BASE_URL` — реальный HTTPS origin без пути;
- `SUBMISSION_TRUSTED_PROXY_NETWORKS` — реальные доверенные proxy CIDR;
- SMTP: заполнить все семь SMTP/recipient полей либо оставить все пустыми.

Чтобы полностью отключить SMTP на первом запуске, явно очистите все семь строк:

```dotenv
SMTP_HOST=
SMTP_PORT=
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_SECURITY=
SUBMISSION_NOTIFY_TO=
```

Нельзя оставлять примерные адреса `example.org` и значения `replace-with-*`:
валидатор считает их placeholder и останавливает rollout.

Пути должны остаться контейнерными:

```dotenv
CMS_DATABASE=/data/cms.sqlite3
CMS_MEDIA_DIR=/data/media
CMS_MEDIA_DERIVATIVES_DIR=/data/media-derivatives
CMS_LEGACY_CRAWL=/data/legacy-crawl-checkpoint.json
CMS_MEDIA_MANIFEST=/data/legacy-media-manifest.json
CMS_MISSING_MEDIA_REPORT=/data/missing-legacy-media.csv
```

Проверка не выводит секреты:

```bash
python3 scripts/validate_production_env.py .env
```

Продолжайте только при `"ok": true`.

### 4.4. Проверка переданного архива

```bash
cd "$HOME/migration-incoming"
sha256sum -c public-source-YYYYMMDDTHHMMSSZ.tar.gz.sha256
```

Ожидается `OK`.

### 4.5. Preflight непосредственно перед запуском

```bash
cd "$HOME/temple"
git status --short
git rev-parse HEAD
git rev-parse origin/main
sudo docker compose ps
find data -mindepth 1 ! -name .gitkeep -print
df -h "$HOME"
```

Условия запуска:

- checkout чистый;
- `HEAD` равен `origin/main`;
- CMS-контейнер отсутствует;
- последняя команда `find` ничего не выводит;
- свободно не меньше максимума из `10 GiB` и
  `3 × размер переданного tar.gz + 2 GiB`;
- старый публичный сайт доступен до завершения переключения.

### 4.6. Clean bootstrap

```bash
cd "$HOME/temple"
bash scripts/production_bootstrap_clean.sh \
  "$HOME/migration-incoming/public-source-YYYYMMDDTHHMMSSZ.tar.gz" \
  "$HOME/migration-incoming/public-source-YYYYMMDDTHHMMSSZ.tar.gz.sha256"
```

Скрипт не требует ручного ввода и выполняет:

1. повторную SHA-256 проверку архива;
2. проверку всех tar entries против path traversal, ссылок и device entries;
3. проверку manifest и каждого файла внутри bundle;
4. проверку совпадения Git SHA Windows и Ubuntu;
5. Docker build;
6. schema 13, структурный импорт и полный crawl-импорт в staging;
7. missing-media report и media reconciliation;
8. FTS reindex/verify;
9. migration audit с external-link evidence;
10. preparation preview/apply;
11. post-apply audit и fail-closed semantic verify;
12. SQLite quick/FK checks и сравнение количества материалов/редиректов с bundle;
13. атомарную активацию `data/`;
14. запуск CMS и локальные health/CMS/public API smoke checks.

При успехе вывод содержит:

```text
CLEAN_BOOTSTRAP_APPLIED=true
DEPLOYMENT_ID=public-source-YYYYMMDDTHHMMSSZ
DEPLOYMENT_ATTEMPT=attempt-YYYYMMDDTHHMMSSZ
IMPLEMENTATION_SHA=<sha>
IMAGE_ID=<image-id>
REPORTS_DIR=<path>
ROLLBACK_SNAPSHOT=<path>
```

Если любой gate падает до активации, production `data/` остаётся нетронутым.
Если ошибка возникает после активации, неуспешный контейнер удаляется,
исходный пустой каталог возвращается, а неуспешные данные сохраняются как
`failed-data`.

### 4.7. Повторная проверка после запуска

```bash
cd "$HOME/temple"
bash scripts/production_verify_clean.sh public-source-YYYYMMDDTHHMMSSZ
```

Ожидается:

```text
CLEAN_DEPLOYMENT_VERIFIED=true
```

Проверка повторяет bundle/database invariants, SQLite integrity, schema migrations, FTS, semantic preparation gate, health, CMS HTML и public API.

### 4.8. Проверка через внешний reverse proxy

После настройки маршрутизации:

```bash
cd "$HOME/temple"
VERIFY_EXTERNAL=1 bash scripts/production_verify_clean.sh public-source-YYYYMMDDTHHMMSSZ
```

Этот режим проверяет `PUBLIC_BASE_URL` и внешний `/api/health`. Сам скрипт не изменяет Nginx, DNS, NAT, VPN или TLS.

## 5. Переключение трафика

1. Не выключайте старый MODX до успешного `production_verify_clean.sh`.
2. Зафиксируйте время source-bundle. Изменения публичного сайта после этого времени в bundle не входят.
3. Если между crawl и переключением на старом сайте публиковались изменения, сформируйте новый bundle и повторите clean bootstrap на новой пустой цели либо отложите переключение.
4. Переключите reverse proxy/DNS на новую CMS.
5. Выполните внешний verify.
6. Сохраните bundle, checksum и весь `REPORTS_DIR` как релизное evidence.

## 6. Откат

До успешного завершения bootstrap откат автоматический.

После успешного переключения:

1. верните reverse proxy/DNS на старый MODX;
2. остановите новую CMS:

   ```bash
   cd "$HOME/temple"
   sudo docker compose stop cms
   ```

3. не удаляйте `data/`, source-bundle и `.clean-deploy/`;
4. сохраните логи и отчёты;
5. исправьте причину и выполняйте новый clean deployment только на отдельной пустой цели.

Откат не возвращает трафик автоматически, потому что Nginx/DNS/NAT находятся вне scope репозитория.

## 7. Критерии завершения

Миграция считается технически завершённой, когда одновременно выполнено следующее:

- bundle и архив прошли SHA-256 проверку;
- crawl queue пуста, crawler failures равны нулю;
- каждый mirrored manifest entry имеет физический файл с совпадающим SHA-256;
- schema БД равна 13;
- число импортированных материалов и редиректов совпадает с manifest bundle;
- все материалы остаются `draft` и `migration_review_required=1`;
- в БД нет source users, sessions, заявок, notification-outbox и пользовательской
  истории; до первого запуска пользователей нет, после него есть только новый
  bootstrap-admin;
- missing-media report согласован с CMS issues;
- media reconciliation имеет `status=ok`;
- SQLite quick/FK, migrations и FTS verify успешны;
- preparation имеет `technical_blocker=0` и semantic verify успешен;
- локальный и внешний health успешны;
- исходный MODX не изменён.

Редакционная проверка содержательных вопросов выполняется позднее на этапе 7 и не заменяет ни один технический gate этой инструкции.
