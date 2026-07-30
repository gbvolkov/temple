# Детальные функциональные требования: этап 6 «Автоматическая подготовка мигрированных материалов»

Дата: 30 июля 2026 года.  
Статус: черновик системного аналитика для строгого согласования.  
База анализа: реализация после этапа 6 (`server/migration_preparation.py`, API, CMS UI, server/Playwright/source-data тесты и production rollout).  
Назначение: определить полный проверяемый контракт этапа 6 до допуска к редакционному этапу 7.

## 1. Цель и границы

Цель этапа 6 — автоматически и воспроизводимо подготовить все мигрированные материалы, применить только однозначные технические преобразования и передать этапу 7 только содержательно неоднозначный остаток.

В scope:

- полный аудит как обязательная исходная точка;
- фоновые `preview` и `apply`;
- классификация каждого материала;
- досье «До/После»;
- автоматические правила нормализации;
- атомарность, version/hash gate, идемпотентность и rollback;
- роли, API, CLI, UI, polling и восстановление после перезапуска;
- БД, ревизии, аудит, FTS, медиа и source-data инварианты;
- проверка на восстановленной копии и production rollout;
- критерии допуска к этапу 7.

Вне scope:

- публикация, архивирование, удаление либо снятие `migration_review_required`;
- содержательное решение за редактора;
- создание и финализация редакционных партий этапа 7;
- выбор канонического дубля или содержательно подходящего отсутствующего медиа;
- изменение production-данных до успешной проверки восстановленной копии.

## 2. Термины и фактическая контрольная точка

| Термин | Определение |
|---|---|
| Полный аудит | Завершённый audit без `content_id`, `content_type` и `year`; `check_external` может быть явно выключен |
| Preview | Неизменяющий содержимое расчёт предложений и классификаций на зафиксированных version/hash |
| Apply | Применение конкретного завершённого preview с повторной проверкой всего набора до первой записи |
| Auto-ready | Техническая подготовка завершена, редакционное решение не требуется |
| Editorial-review | Осталась только содержательная неоднозначность, допустимая к передаче человеку |
| Technical-blocker | Программная, структурная, ссылочная или медиа-проблема; человеку не передаётся |
| Stale preview | Хотя бы один материал удалён, сменил version либо не совпадает по hash с `before` |
| Компенсирующий rollback | Новые ревизии, возвращающие содержимое `before`; история не переписывается |

Наблюдение на подготовленной копии, которое является обязательным предметом перепроверки:

- `1 757` preparation items;
- `496 auto_ready`;
- `1 261 editorial_review`;
- `0 technical_blocker`;
- post-apply audit: `590 blocker` и `1 016 warning`;
- у `197` материалов класса `auto_ready` остаётся warning `unicode_not_nfkc`;
- текущий `verify` возвращает `ok`, несмотря на указанное расхождение.

Число `1 757` расходится с ранее зафиксированными в плане и отчёте `1 756`. До согласования причины ни одно из чисел нельзя использовать как безусловный production baseline.

## 3. Роли и права

Все mutation-запросы требуют активную сессию и корректный `X-CSRF-Token`.

| Возможность | Не авторизован | Viewer | Editor | Publisher | Admin |
|---|---:|---:|---:|---:|---:|
| Открыть «Миграция», метрики, историю | Нет | Да | Да | Да | Да |
| Смотреть run, items, досье | Нет | Да | Да | Да | Да |
| Фильтр и пагинация | Нет | Да | Да | Да | Да |
| Запустить полный аудит | Нет | Нет | Нет | Да | Да |
| «Сформировать preview» | Нет | Нет | Нет | Да | Да |
| «Применить preview» | Нет | Нет | Нет | Нет | Да |
| «Откатить применение» | Нет | Нет | Нет | Нет | Да |

UI обязан не выводить недоступные кнопки. API обязан независимо от UI отвечать `401/403` и не менять БД/медиа.

## 4. Модель состояний

### 4.1. Preparation run

| Режим | Текущее состояние | Событие | Следующее состояние | Условие/эффект |
|---|---|---|---|---|
| preview/apply | — | POST create | `queued` | Создан журнал запуска |
| preview/apply | `queued` | worker захватил | `running` | `started_at`, очищен `error` |
| preview | `running` | расчёт успешен | `completed` | Items и counts записаны |
| apply | `running` | gate и транзакция успешны | `completed` | Данные, ревизии и журнал согласованы |
| preview/apply | `running` | ошибка | `failed` | Ошибка до 500 символов, `finished_at` |
| preview/apply | `running` | рестарт приложения | `queued` | `started_at=NULL`, записана причина восстановления |
| apply | `completed` | rollback успешен | `rolled_back` | Applied items → `rolled_back`; созданы компенсации |
| прочее | любое | недопустимое действие | без изменения | `409` либо `404/422` |

Терминальные состояния: `completed`, `failed`, `rolled_back`. UI опрашивает активные `queued/running` каждые 1,5 секунды и прекращает polling при уходе из панели или открытии досье.

### 4.2. Preparation item

| Контекст | Состояние item | Значение |
|---|---|---|
| preview | `planned` | Предложение рассчитано, содержимое не менялось |
| apply, hash отличается | `applied` | Записана новая версия материала |
| apply, hash совпадает | `unchanged` | Новая версия не создавалась |
| rollback | `rolled_back` | Только ранее `applied` возвращён новой компенсационной версией |
| зарезервировано | `failed` | Состояние предусмотрено схемой, но текущий apply не сохраняет частично failed items |

## 5. Детальные функциональные требования

Сокращения трассировки:

- `MP` — `tests/test_migration_preparation.py`;
- `PW-S6` — `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow`;
- `PW-ROLE` — `tests/e2e/test_admin_migration.py::test_full_role_capabilities`;
- `SRC` — `tests/e2e/test_source_data.py::test_stage6_real_source_automatic_preparation`;
- `ROLLOUT` — `scripts/production_apply_stage12.sh` и stage-12 ветки `production_apply_stage4.sh`;
- `нет` — обязательная автоматизация отсутствует.

### 5.1. Вход, доступ и обзор

| ID | Actor / предусловия | Действия до конкретного клика или входные данные | Ожидаемый результат | Негативный вариант | Trace |
|---|---|---|---|---|---|
| FR-S6-001 | Любая роль, активная сессия | В левом меню нажать `Миграция` | Открыт heading `Перенесённые материалы`; видны acceptance и preparation секции | При ошибке загрузки показан toast, UI не зависает | PW-S6 |
| FR-S6-002 | Viewer/editor | Открыть `Миграция` | Видны метрики, items, фильтр, пагинация, история и кнопка `Открыть досье`; mutation-кнопок нет | Прямой POST preview/apply/rollback → `403`, без изменений | PW-ROLE, MP API |
| FR-S6-003 | Publisher | Открыть `Миграция` | Видны `Запустить аудит`, `Сформировать preview`; apply/rollback отсутствуют | POST apply → `403` | PW-ROLE, MP API |
| FR-S6-004 | Admin | Открыть `Миграция` | Видны audit, preview и apply; rollback видна только для последнего completed apply | Недопустимая кнопка disabled/hidden | PW-ROLE, PW-S6 |
| FR-S6-005 | Любая роль просмотра | Дождаться загрузки dashboard | Preparation содержит 4 метрики: items, auto-ready, editorial-review, technical-blocker | Нет run → нули и текст о необходимости полного аудита | PW-S6 |
| FR-S6-006 | Любая роль просмотра | Раскрыть `История автоматической подготовки` | Runs показаны от новых к старым: mode, status, дата, items, changed, связь с preview, error | Пусто → `Запусков пока нет` | PW-S6 частично |
| FR-S6-007 | Любая роль просмотра | Обновить страницу/повторно открыть `Миграция` | Текущее состояние восстанавливается с сервера; client state не является источником истины | Устаревший HTTP-ответ не перерисовывает новый запрос | нет |

### 5.2. Полный аудит и запуск preview

| ID | Actor / предусловия | Действия / вход | Ожидаемый результат | Негативный вариант | Trace |
|---|---|---|---|---|---|
| FR-S6-008 | Publisher/admin | Нажать `Запустить аудит` | POST audit `202`; toast `Аудит поставлен в очередь`; run завершается worker-ом | Ошибка отображается toast; данные не меняются | PW migration audit |
| FR-S6-009 | Publisher/admin; нет полного completed audit | Открыть preparation | `Сформировать preview` disabled; причина понятна пользователю | POST preview → `409 Сначала выполните полный миграционный аудит` | MP косвенно |
| FR-S6-010 | Publisher/admin; полный audit completed | Нажать `Сформировать preview` | POST `mode=preview`, `202 queued`; offset сброшен; toast; начинается polling | Повторный клик во время active run disabled | PW-S6 |
| FR-S6-011 | Система; preview queued | Worker выбирает самый ранний queued run | Run: `queued→running→completed`; `audit_run_id` и `rules_version=1.0.0` зафиксированы | Исключение → `failed`, error и `finished_at` | MP happy; failed — нет |
| FR-S6-012 | Система; полный audit completed | Рассчитать preview | Обработан каждый `migration_review_required=1`; один item на content; counts суммируются в total | Материал без флага не включается | MP, SRC |
| FR-S6-013 | Система | Повторно создать preview на неизменной базе | Предложения, hashes, классификации и deterministic block IDs совпадают; создаётся отдельный run | Одновременный активный запуск не должен давать конфликтующих результатов | нет |
| FR-S6-014 | Publisher/admin; новый полный audit выполняется, старый completed существует | Открыть preparation | Preview запрещён до завершения актуального активного audit | Текущий UI может разрешить preview по старому audit — gap | нет |

### 5.3. Формирование досье и правила данных

| ID | Actor / предусловия | Действия / вход | Ожидаемый результат | Негативный вариант | Trace |
|---|---|---|---|---|---|
| FR-S6-015 | Система; item | Зафиксировать snapshot | `before`: content_type, slug, title, data; `before_hash`; version; `proposed` и hash; timestamps | Невалидный JSON не должен теряться молча | MP |
| FR-S6-016 | Система; любые строки дерева | Нормализовать proposal | Рекурсивный Unicode NFKC; удалены Cc кроме `\\n\\r\\t`; исходник сохранён в before | Post-audit `unicode_not_nfkc` после apply недопустим | MP частично; gap фактически |
| FR-S6-017 | Система; body/biography string, `body_text` или legacy blocks | Извлечь текст | Удалены известные breadcrumbs/footer; текст разбит на paragraphs с UUID5 | Неуверенная граница → editorial reason `text_extraction_ambiguous`, без реконструкции | MP |
| FR-S6-018 | Система; отсутствует summary, есть clean text | Сформировать summary | Текст сжат до schema max, без разрыва слова, с `…` при обрезке | Нет clean text → summary не выдумывается | MP |
| FR-S6-019 | Система; date начинается `2000-` | Найти единственную дату в URL/title/первых 4000 символах | Дата заменена на ISO; rule `date_from_evidence` | 0 или >1 кандидата → `date_requires_editor`; 2000 не считается доказательством | MP |
| FR-S6-020 | Система; cover есть, cover_alt пуст, поле поддерживается | Подготовить proposal | `cover_alt=title`; transformation зафиксирован | Нет cover → alt не создаётся | MP косвенно |
| FR-S6-021 | Система; `photos` | Нормализовать repeatable field | Дубли внутри `photos` удалены по image, пустой alt=title, order перенумерован | Одинаковый cover и первый photo не является дублем `CMS-18` | MP CMS-18, PW-S6 |
| FR-S6-022 | Система; leaflet без cover и с ready PDF | Рассчитать и применить | Proposal содержит deterministic WebP URL; apply создаёт первую страницу, media row/event и metadata | PDF отсутствует/не ready/не PDF → `leaflet_cover_requires_editor` | MP leaflet |
| FR-S6-023 | Система; service crawl fields присутствуют | Подготовить proposal | Удалены `body_text`, `headings`, `legacy_images`, `legacy_documents`, `migration_note`, `new_path`; provenance остаётся в журнале | Полезное содержимое не должно удаляться вместе со служебным | MP |
| FR-S6-024 | Система; required field пуст | Классифицировать | Reason `required_field_requires_editor`; поле не заполняется предположением | При полном наборе старый `required_field_missing` считается resolved | MP |
| FR-S6-025 | Система; открытые audit issues текущей version | Сопоставить коды | Resolved исключены; technical-коды → technical; duplicate/external/missing media/unpublished link и прочие blocker/warning → editorial | Неизвестный blocker/warning нельзя потерять | MP |
| FR-S6-026 | Система; причины рассчитаны | Присвоить один класс | technical имеет высший приоритет; иначе editorial; иначе auto-ready | Каждый item обязан иметь ровно один допустимый класс | MP, SRC |
| FR-S6-027 | Система; editorial item | Сформировать рекомендации | Причина, field/code/message, рекомендация, варианты и consequence каждого варианта сохранены | UI не должен скрывать code, field или consequences | MP хранение; UI gap |
| FR-S6-028 | Система; duplicate/title reason | Сформировать options | `accept_proposal`, `merge_or_archive`, `archive` с последствиями | При missing media добавляется `choose_media` | MP |
| FR-S6-029 | Система | Обработать slug/URL/type/section/relations | Однозначные значения восстанавливаются либо формально подтверждается отсутствие необходимости | Реализация не содержит явных правил для обещанных plan-полей — gap | нет |

### 5.4. Просмотр досье, фильтры, пагинация и mobile

| ID | Actor / предусловия | Действия / вход | Ожидаемый результат | Негативный вариант | Trace |
|---|---|---|---|---|---|
| FR-S6-030 | Любая роль; completed display run | Нажать `Открыть досье` у карточки | Открыто досье: title/type, before/current version, class, recommendation, reasons, rules, JSON `До/После`, options | Item отсутствует в client map → UI не падает, сообщает об обновлении | PW-S6 |
| FR-S6-031 | Любая роль; досье | Нажать `← К dashboard` | Dashboard заново загружен с сервера | Polling старого экрана не дублируется | PW-S6 |
| FR-S6-032 | Любая роль | В select выбрать класс, нажать `Применить фильтр` | offset=0; GET items с classification; все rows соответствуют классу | Неизвестный API classification → `422` без данных | PW-S6, MP |
| FR-S6-033 | Любая роль; >50 items | Нажать `Дальше →`, затем `← Назад` | Offset 50/0; не более 50 items; кнопки disabled на границах | Offset за total → пустой список, UI остаётся рабочим | PW-S6 |
| FR-S6-034 | Любая роль; 390×844 | Открыть `Миграция`, фильтр, досье и вернуться | Все контролы доступны, текст читаем, горизонтального overflow нет | Long JSON/reason не расширяет viewport | PW-S6 частично |
| FR-S6-035 | Любая роль | Просмотреть options в досье | Для каждого варианта показаны label и consequence | Текущий UI показывает label без consequence — gap | нет |
| FR-S6-036 | Любая роль | Просмотреть reason | Видны code, field_path и message, позволяющие принять решение | Текущий UI обычно выводит только message — gap | нет |

### 5.5. Apply, stale, идемпотентность и атомарность

| ID | Actor / предусловия | Действия / вход | Ожидаемый результат | Негативный вариант | Trace |
|---|---|---|---|---|---|
| FR-S6-037 | Admin; latest completed preview | Нажать `Применить preview` | Открыт confirm с пояснением, что публикации/приёмки нет | Нажать Cancel → запрос не отправлен, кнопка доступна | PW-S6 |
| FR-S6-038 | Admin; confirm открыт | Нажать OK | POST apply с `preview_run_id`; `202 queued`; toast; polling | Нет preview/wrong mode/not completed/not found → `409/404` | PW-S6, MP |
| FR-S6-039 | Система; apply queued | До транзакции проверить все items | Для каждого content существует, version и before hash совпадают | Любой stale → весь run `failed`, ни одной ревизии/медиа/правки | MP stale |
| FR-S6-040 | Система; gate успешен | Применить changed proposal | В одной DB-транзакции обновлён content, version+1, system revision, audit event, media usages и FTS | Ошибка любого item откатывает все DB-записи и удаляет созданные этим run файлы | MP частично |
| FR-S6-041 | Система; proposal unchanged | Применить item | Content/version/revision не меняются; item=`unchanged` | Counts changed не увеличивается | MP косвенно |
| FR-S6-042 | Система; apply completed | Проверить бизнес-инварианты | status, published_version/published snapshot, scheduled_at и `migration_review_required` не меняются | Любое снятие флага/публикация блокирует релиз | PW-S6 flag; остальное нет |
| FR-S6-043 | Система; apply completed | Проверить журнал | Один preparation item на source item; changed имеет revision+audit; result_version точен | Actor для системного apply должен быть явно определён и трассируем | MP частично |
| FR-S6-044 | Admin/API; повторить apply того же preview | Нажать/отправить повторно | Для existing queued/running/completed возвращён тот же run ID; нет повторных версий | После failed разрешён новый apply после устранения причины | MP idempotency |
| FR-S6-045 | Система; leaflet generation | Повторить apply/idempotent путь | Deterministic stored_name/media_id; существующий ready файл не дублируется | Частично созданный файл очищается при ошибке | MP leaflet частично |
| FR-S6-046 | Любая роль | Во время active preparation открыть dashboard | Preview/apply disabled; status обновляется каждые 1,5 с до terminal | Failed виден с error; бесконечного polling нет | PW-S6 happy; failed — нет |

### 5.6. Rollback и конфликты

| ID | Actor / предусловия | Действия / вход | Ожидаемый результат | Негативный вариант | Trace |
|---|---|---|---|---|---|
| FR-S6-047 | Admin; latest completed apply | Нажать `Откатить применение` | Показан confirm о компенсирующих ревизиях и конфликте | Cancel → без HTTP и изменений | PW-S6 confirm |
| FR-S6-048 | Admin; confirm OK; нет subsequent edits | Подтвердить rollback | Все applied items возвращены к before новой version; revisions, audit, usage, FTS обновлены; run=`rolled_back` | Unchanged items не получают лишней версии | MP rollback, PW-S6 |
| FR-S6-049 | Admin; хотя бы один item изменён после apply | Подтвердить rollback | До первой записи обнаружен version conflict; `409`; ни один material не откатан; run остаётся completed | Частичный rollback запрещён | MP conflict |
| FR-S6-050 | Admin/API | Rollback preview/queued/running/failed/already rolled_back/nonexistent | Операция отклонена `409/404`, состояние неизменно | CSRF/role violation → `403` | MP API частично |
| FR-S6-051 | Система; apply создал media cover | Выполнить rollback | Политика generated media формально определена: удалить безопасный orphan либо оставить с явным journal/status | Текущая реализация возвращает content, но не компенсирует media side effects — gap | нет |

### 5.7. API, CLI, background и recovery

| ID | Actor / предусловия | Действия / вход | Ожидаемый результат | Негативный вариант | Trace |
|---|---|---|---|---|---|
| FR-S6-052 | API client | `GET /api/admin/migration` | В `preparation`: latest_applied, runs≤20, counts | Нет apply → нулевые counts | MP API частично |
| FR-S6-053 | API client viewer+ | GET run; GET items с limit 1..200, offset≥0, optional class | JSON содержит полный run/item контракт и total/limit/offset | Run 404; invalid class 422; invalid pagination 422 | MP API частично |
| FR-S6-054 | API publisher/admin | POST preparations preview | `202`, run queued, actor `started_by` | Без audit 409; CSRF/role 403 | MP API |
| FR-S6-055 | API admin | POST preparations apply / POST rollback | `202` для apply queue; `200` rollback | Publisher apply и любой non-admin rollback → 403 | MP API |
| FR-S6-056 | CLI operator; schema12 | `python -m server.migration_preparation preview [--database]` | Если не `--skip-audit`, выполнен полный audit; затем sync preview; JSON в stdout | Ошибка → non-zero и диагностичное сообщение, без частичных данных | ROLLOUT happy |
| FR-S6-057 | CLI operator | `apply --preview-run-id ID`, `status`, `verify`, `rollback --run-id ID [--actor-id]` | Каждая команда выдаёт JSON; обязательные args валидируются; status read-only | Неверный ID/state → non-zero; source не меняется до apply | ROLLOUT частично |
| FR-S6-058 | Система; приложение стартует | Lifespan запускает preparation scheduler | Running runs переводятся в queued; worker последовательно забирает очередь; shutdown cancel корректен | Исключение одного run не завершает scheduler; повтор через interval | нет |

## 6. Инварианты БД, медиа, FTS и аудита

После preview:

- `contents`, `revisions`, `media`, FTS, workflow и физические файлы не изменены;
- появились только preparation run/items;
- `PRAGMA quick_check=ok`, `foreign_key_check` пуст;
- counts точно равны фактическим items.

После apply:

- число `contents` неизменно;
- число migration flags и распределение workflow status неизменны;
- public snapshot и public URL неизменны;
- changed content имеет ровно одну новую system revision и `migration_prepare` audit;
- FTS соответствует опубликованным версиям и не содержит stale rows;
- media usages соответствуют proposal;
- generated leaflet covers имеют ready media row, digest, dimensions, source и event;
- `PRAGMA quick_check=ok`, foreign keys пусты;
- полный повторный audit относится к применённым version;
- классификация и audit согласованы: ни один `auto_ready` не имеет открытой технической проблемы.

После rollback:

- before-данные восстановлены новыми версиями;
- журнал apply и rollback сохраняется;
- FTS/usages соответствуют восстановленной версии;
- политика generated media выполнена;
- foreign keys и quick check проходят.

## 7. Source-data и rollout

Обязательная последовательность:

1. Зафиксировать read-only baseline исходной БД и медиатеки.
2. Скопировать БД через SQLite backup; медиатеку — в отдельное дерево (допустимы hard links при запрете записи в существующие файлы).
3. Применить schema 12 и media reconciliation только к копии.
4. На копии выполнить полный audit → preview → проверку dossier/counts → apply → полный post-audit → усиленный verify.
5. Проверить quick check, FK, content/workflow/flag/public/FTS/media/audit инварианты.
6. Повторно вычислить baseline оригиналов и доказать их неизменность.
7. Production script обязан сделать backup+checksum, restore drill, выполнить те же stage-12 проверки в disposable container и только затем повторить в production.
8. Preview/apply JSON-отчёты сохраняются с правами `600`; ошибочный gate прекращает rollout.
9. Rollback procedure, run IDs, backup ID и контрольные показатели фиксируются в релизном журнале.

## 8. Выявленные разрывы

| Gap | Приоритет | Наблюдение | Требуемое устранение до этапа 7 |
|---|---:|---|---|
| GAP-S6-01 | Blocker | `verify` возвращает `ok`, хотя post-audit содержит 590 blockers и 1 016 warnings | Verify обязан требовать completed post-apply audit текущих versions и сверять все issues с классификацией |
| GAP-S6-02 | Blocker | 197 `auto_ready` продолжают иметь `unicode_not_nfkc` | Characterization-тест; устранить правило/аудит-расхождение; 0 technical issues у auto-ready |
| GAP-S6-03 | Blocker | Baseline `1 757` против документированных `1 756` | Идентифицировать конкретный content ID и формально утвердить единый baseline |
| GAP-S6-04 | High | `0 technical_blocker` не отражает post-audit технический остаток | Reclassification должна строиться/проверяться на post-proposal audit, а не только на pre-audit issue codes |
| GAP-S6-05 | High | Досье UI скрывает consequences, обычно code и field_path причин | Вывести полный контекст, требуемый для содержательного решения |
| GAP-S6-06 | High | Нет явной реализации обещанных правил URL/slug/type/section/relations | Добавить rules либо доказательство, что по каждому полю преобразование не требуется |
| GAP-S6-07 | High | Rollback не определяет компенсацию generated media | Реализовать безопасную очистку orphan или формальный retained-media lifecycle |
| GAP-S6-08 | Medium | Preview может опираться на старый completed audit при активном новом полном audit | Блокировать preview до terminal состояния последнего полного audit |
| GAP-S6-09 | Medium | История runs read-only; failed latest run может скрыть удобный путь к применимому preview | Добавить выбор run/retry/new preview и ясный recovery CJM |
| GAP-S6-10 | Medium | Нет постоянных тестов failed run, scheduler restart, concurrent queue, invalid states, FTS/public invariants | Дополнить server и Playwright матрицу |
| GAP-S6-11 | Medium | Source-data тест принимает текущий слабый `verify` и не утверждает post-audit counts/issues | Добавить post-audit semantic assertions |
| GAP-S6-12 | Medium | Документы ранее объявили этап завершённым при недоказанном semantic gate | После исправлений обновить план, отчёт, guide, shortcomings и evidence |

Каждый gap является функциональным/техническим и не требует решения человека по материалам. Он должен устраняться до передачи очереди редактору.

## 9. Матрица обязательного функционального тестирования

Минимальный набор до допуска:

- позитивные: preview, all classifications, changed/unchanged apply, generated media, rollback;
- негативные: нет audit, wrong preview, not found, stale version, stale hash, apply failure, rollback conflict, повторный rollback;
- ролевые: 5 actor classes × read/preview/apply/rollback × UI/API;
- переходные: каждая дуга state matrix, включая restart recovery;
- UI: каждая кнопка, Cancel/OK каждого confirm, filter empty/all/classes, first/middle/last page, dossier/back/history;
- mobile: dashboard, filters, dossier JSON, confirmations, toast/error;
- данные: каждое правило FR-S6-016—029 отдельными positive/ambiguous/no-op cases;
- persistence: revisions, audit events, FTS, usages, media rows/files/events;
- source-data: baseline, copy, post-audit semantic gate, unchanged originals;
- rollout: fail-fast на каждом gate и сохранённые отчёты.

## 10. Критерии допуска к этапу 7

Этап 7 можно начинать только при одновременном выполнении всех условий:

1. Причина расхождения `1 756/1 757` установлена; baseline зафиксирован.
2. Каждый migration-flagged material присутствует ровно в одном item последнего applied run.
3. `technical_blocker=0` подтверждён не только preparation counts, но и полным post-apply audit текущих versions.
4. Для `auto_ready` нет открытых blocker/warning технических кодов; в частности `unicode_not_nfkc=0`.
5. Каждый оставшийся blocker/warning однозначно сопоставлен с `editorial_review` и содержит достаточные reason/recommendation/options/consequences.
6. Усиленный `verify` падает на искусственно внесённом post-audit расхождении и проходит на чистом наборе.
7. Пройдены все server, Playwright smoke, два full regression и source-data профили; нет необъяснённых FAIL/ERROR/XPASS/XFAIL.
8. Пройдены quick check, FK, FTS, media, revision, audit, public/workflow/flag invariants.
9. Rollback проверен для content и generated-media side effects.
10. Исходные runtime БД и медиатека доказанно неизменны.
11. Production restore drill и fail-fast rollout воспроизводимы.
12. Реестр недостатков, coverage matrix, solution description, test report и user guide соответствуют фактическому поведению.

## 11. Предварительное заключение системного аналитика

Текущая реализация предоставляет основную архитектуру preview/apply/rollback и рабочий UI/CJM, однако к этапу 7 пока не готова. Положительный `verify` не подтверждает фактическую техническую чистоту набора: post-audit и `197 auto_ready` с Unicode-warning прямо противоречат классу «готово автоматически». До устранения `GAP-S6-01`—`GAP-S6-04`, согласования baseline и расширения тестов запуск редакционной проверки будет преждевременным и переложит технические дефекты на человека, что запрещено каноническим планом.
