# Чистый production-выкат CMS с полной публичной миграцией

## 1. Область переноса

Источник: `https://www.sv-innokenty.ru/`.

Переносятся:

- все страницы, достижимые read-only Playwright-crawl публичного сайта;
- публичные изображения и документы, найденные на страницах;
- старые URL и 301-редиректы;
- структурированные материалы новой CMS;
- зафиксированные отсутствующие на источнике файлы и связи с материалами.

Не переносятся:

- закрытые черновики MODX;
- пользователи MODX;
- внутренняя история MODX.

Импортированные материалы создаются как `draft`, получают
`migration_review_required=1` и не публикуются автоматически.

## 2. Состав процесса

| Скрипт | Где выполняется | Назначение |
|---|---|---|
| `scripts/build_public_migration_bundle.sh` | доверенная рабочая машина | свежий crawl, media mirror, import plan, missing-media report и immutable archive |
| `scripts/verify_public_migration_bundle.sh` | рабочая машина или production | независимая проверка архива, SHA-256 и семантических счётчиков |
| `scripts/production_bootstrap_from_public_bundle.sh` | чистый production host | staging-import, schema 13, media reconciliation, audit/preparation/verify и запуск CMS |
| `scripts/production_rollback_clean_bootstrap.sh` | production host | остановка чистой установки и перенос неудачных runtime-данных в сохраняемый каталог |

## 3. Требования

Рабочая машина:

- Git;
- `uv`;
- Chromium для Playwright;
- доступ по HTTPS к старому публичному сайту;
- свободное место для полной медиатеки и архива.

Production:

- чистый checkout `main`, совпадающий с `origin/main`;
- Docker и Docker Compose;
- `python3`, `rsync`, `tar`, `curl`, `sudo`;
- пустой `data/`, кроме `.gitkeep`;
- подготовленный `.env` без placeholder-значений.

Обязательные production-параметры:

- `CMS_ENV=production`;
- `CMS_BOOTSTRAP_USER`;
- `CMS_BOOTSTRAP_PASSWORD` длиной не менее 20 символов;
- `PUBLIC_BASE_URL`;
- `SUBMISSION_IP_HASH_SECRET` длиной не менее 32 символов;
- `SUBMISSION_TRUSTED_PROXY_NETWORKS`.

SMTP задаётся либо всеми семью параметрами, либо полностью отключается.

## 4. Формирование source-bundle

Из чистого checkout:

```bash
INSTALL_BROWSER=1 \
  scripts/build_public_migration_bundle.sh
```

Для последующих запусков с уже установленным Chromium:

```bash
scripts/build_public_migration_bundle.sh
```

Результат:

```text
work/migration-bundles/public-source-YYYYMMDDTHHMMSSZ.tar.gz
work/migration-bundles/public-source-YYYYMMDDTHHMMSSZ.tar.gz.sha256
```

Скрипт fail-closed останавливается, если:

- crawl не завершил очередь;
- возникли browser/transport failures;
- import plan пуст;
- manifest не соответствует физическим файлам;
- missing-media CSV расходится с ошибками manifest;
- изменён хотя бы один файл после формирования manifest.

HTTP 404 не скрываются: они сохраняются в crawl/import plan как обследованные
непереносимые страницы. Отсутствующие медиа сохраняются в отдельном реестре.

## 5. Независимая проверка bundle

```bash
scripts/verify_public_migration_bundle.sh \
  /path/public-source-YYYYMMDDTHHMMSSZ.tar.gz \
  /path/public-source-YYYYMMDDTHHMMSSZ.tar.gz.sha256
```

Проверяются внешний checksum архива, отсутствие небезопасных путей и ссылок,
внутренний `bundle-manifest.json`, SHA-256 каждого файла, завершённость crawl,
согласованность plan/manifest/CSV и физической медиатеки.

Архив и checksum следует передавать на production раздельно.

## 6. Чистый production bootstrap

```bash
cd ~/temple
scripts/production_bootstrap_from_public_bundle.sh \
  /secure-transfer/public-source-YYYYMMDDTHHMMSSZ.tar.gz \
  /secure-transfer/public-source-YYYYMMDDTHHMMSSZ.tar.gz.sha256
```

До записи в `data/` скрипт выполняет в изолированном staging:

1. проверку archive и bundle;
2. проверку совпадения SHA кода bundle с production checkout;
3. сборку Docker image;
4. создание чистой SQLite и применение schema 13;
5. полный импорт материалов и редиректов;
6. формирование missing-media report;
7. индексирование и reconciliation медиатеки;
8. reindex/verify FTS;
9. полный migration audit с внешней проверкой;
10. preparation preview и apply;
11. post-apply audit и fail-closed semantic verify;
12. повторную проверку БД и медиатеки.

Только после всех успешных gate staging-каталог копируется в пустой `data/`,
запускается Compose service и проверяются local health/public API.

Для проверки уже настроенного reverse proxy:

```bash
VERIFY_EXTERNAL=1 \
  scripts/production_bootstrap_from_public_bundle.sh \
  /secure-transfer/public-source-YYYYMMDDTHHMMSSZ.tar.gz \
  /secure-transfer/public-source-YYYYMMDDTHHMMSSZ.tar.gz.sha256
```

Отчёты сохраняются вне checkout:

```text
~/temple-clean-deployments/reports-clean-YYYYMMDDTHHMMSSZ/
```

## 7. Проверка результата

Успешный bootstrap обязан вывести:

```text
CLEAN_BOOTSTRAP_APPLIED=true
DEPLOYMENT_ID=clean-YYYYMMDDTHHMMSSZ
```

Обязательные признаки:

- schema 13;
- число `contents` равно числу записей source plan;
- все импортированные материалы остаются `draft`;
- каждый материал имеет `migration_review_required=1`;
- число redirects равно числу уникальных legacy URL;
- пользователи источника не импортированы;
- SQLite quick/FK check успешны;
- media reconciliation имеет `status=ok`;
- preparation semantic verify успешен;
- `/api/health` возвращает content schema `1.6.0`.

## 8. Rollback чистой установки

```bash
scripts/production_rollback_clean_bootstrap.sh \
  clean-YYYYMMDDTHHMMSSZ
```

Rollback:

- останавливает и удаляет только Compose service CMS;
- не удаляет БД или файлы;
- перемещает runtime в
  `~/temple-clean-deployments/rolled-back-clean-YYYYMMDDTHHMMSSZ/`;
- оставляет `data/` пустым для расследования или повторного bootstrap.

Скрипт предназначен только для установки, созданной clean-bootstrap. Для
обновления уже работающей CMS используются versioned production rollout и
baseline/restore scripts.
