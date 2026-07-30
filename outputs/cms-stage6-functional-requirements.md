# Детальные функциональные требования этапа 6

> Реализация contract 1.1 / rules 1.1.0 / schema 13 завершена 30 июля 2026 года. Исторические AS-IS fingerprints, planned node names и GAP-статусы ниже сохранены как принятый baseline требований; актуальный execution status приведён в `outputs/cms-stage6-functional-test-conclusion.md`. `CMS-19/CMS-20` теперь fixed positive regressions, production entrypoint — `scripts/production_apply_stage13.sh`.

Дата: 30 июля 2026 года.  
Версия требований: 3.0, финальный кандидат для цикла согласования 3.  
Объект: этап 6 «Автоматическая подготовка мигрированных материалов».  
Статус реализации: требования содержат подтверждённый контракт и обязательные изменения; наличие требования не означает, что реализация уже ему соответствует.  
Gate: до устранения всех gaps с приоритетом Blocker этап 7 запускать нельзя. Возможный `ACCEPT` этого документа означает только принятие контракта требований и **не означает** готовность реализации или данных.

## 1. Назначение и нормативные правила

Цель — детерминированно подготовить каждый материал с `migration_review_required=1`, применить только однозначные технические изменения и передать человеку максимально поздно только конкретные материалы класса `editorial_review`, где требуется решение по содержанию.

В scope входят audit, preview/apply/verify/rollback, UI/API/CLI, фоновые состояния, данные, ревизии, аудит, FTS, media, source-data и production rollout. Публикация, снятие migration-флага и редакционные решения этапа 7 в scope не входят.

Маркеры:

- **AS-IS** — подтверждённое текущее поведение;
- **MUST** — нормативное поведение, обязательное до этапа 7;
- **GAP** — текущая реализация или автоматизация не соответствует MUST.

При конфликте документации с исполнимым кодом/данными факт фиксируется как AS-IS, но допуск определяется только MUST.

## 2. Журнал закрытия review

Статусы ниже относятся только к полноте текста версии 3.0. Они не закрывают соответствующие gaps реализации.

### 2.1. Все 14 замечаний цикла 1

| Review ID | Статус текста v3 | Зафиксированный контракт |
|---|---|---|
| C-01 | **CLOSED** | Фазы `1 756 raw → 1 757 post-schema`, migration function, content ID и raw immutability |
| C-02 | **CLOSED** | Fail-closed semantic verify с freshness, version/item/issue mapping, JSON и exit contract |
| C-03 | **CLOSED** | Versioned exhaustive classification, unknown fail-closed, чистый `auto_ready` |
| C-04 | **CLOSED** | Полностью видимое досье desktop/mobile: code, field, message, recommendation, consequence |
| C-05 | **CLOSED** | Одна retained-cache policy, непротиворечивый rollback и append-only provenance |
| C-06 | **CLOSED** | 78 построчных FR mappings и 12 gap mappings; фактические node IDs отделены от planned IDs |
| M-01 | **CLOSED** | URL, slug, type, section и relations — отдельные правила и no-op evidence |
| M-02 | **CLOSED** | Единый freshness predicate и fail-closed external evidence policy |
| M-03 | **CLOSED** | DB/file crash model, commit point и replay |
| M-04 | **CLOSED** | Display/actionable/rollback targets и recovery CJM разведены |
| M-05 | **CLOSED** | Rollback target и terminal/in-flight/restart replay едины для UI/API/CLI |
| M-06 | **CLOSED** | Полные endpoint/command success/error schemas и единый duplicate-active outcome |
| N-01 | **CLOSED** | Каждый `EXISTING` использует полный фактический pytest node ID; каждый planned branch — `MISSING` с gap и owner |
| N-02 | **CLOSED** | Evidence имеет immutable artifact paths, SHA-256, raw identity и run IDs |

### 2.2. Findings цикла 2

| Review ID | Статус текста v3 | Выполненная правка |
|---|---|---|
| S2-C-01 | **CLOSED** | Skipped/stale external evidence → `technical_blocker` и verify fail; editorial возможен только после фактической проверки |
| S2-M-01 | **CLOSED** | Добавлен crosswalk `CMS-19/CMS-20`; указаны реальные strict expected-XFAIL nodes и отдельные positive closure tests |
| S2-M-02 | **CLOSED** | Определены полные API/CLI schemas; viewer verify capability удалена; duplicate-active всегда возвращает существующий run |
| S2-M-03 | **CLOSED** | Зафиксирован полный lifecycle кнопки `Запустить аудит`, polling, restart, retry и точная CLI-команда |
| S2-M-04 | **CLOSED** | Terminal repeat, in-flight duplicate, restart и rollback commit point разведены; provenance append-only |
| S2-M-05 | **CLOSED** | §3.2 привязан к двум immutable evidence artifacts и точным fingerprints/run IDs |
| S2-N-01 | **CLOSED** | Успешный verify допускает только полностью mapped ненулевые editorial counts |
| S2-N-02 | **CLOSED** | §13 не содержит `same`, псевдо-existing или сокращённых фактических node IDs |

## 3. Фазовый baseline и timestamped evidence

### 3.1. Нормативный фазовый baseline

| Фаза | Ожидаемое состояние |
|---|---|
| Raw read-only source до schema | `migration_review_required=1`: **1 756** |
| Post-schema copy | `migration_review_required=1`: **1 757** |
| Объяснённый переход | `server/migrations.py::revert_accidental_publication` возвращает случайно опубликованный материал `660f8f7c-1183-464d-b39c-f4df2579fd45` в `draft`, устанавливает `migration_review_required=1`, очищает `published_at`, увеличивает version |
| Инвариант | Общее число `contents` не меняется; raw source DB и media остаются побайтно/метрически неизменными |

Миграция обязана fail-closed остановиться, если этот ID имеет неожиданный slug/status/version. Повторное применение на уже исправленном состоянии — no-op.

### 3.2. Immutable evidence текущего дефекта, не target

Снимок получен 30 июля 2026 года только на безопасной post-schema copy, rules `1.0.0`.

| Identity | Значение |
|---|---|
| Raw SQLite SHA-256 / size | `1E1FAE02D1350E5C7F0B23935474F76B9AAE93ED8AB4EE41BBAC5579ECC024EF` / `62 668 800` bytes |
| Raw media inventory SHA-256 | `69fe0173a626ca62c4509001297d64269f44eb49a5a685b1fa09c77984aec243` |
| Детальный snapshot artifact | `output/playwright/cms-regression/20260730T125608-707378Z` |
| Pre-audit run | `4a29bd77-494e-44e4-8858-5c7ebe27a22c` |
| Preview run | `b315e46a-3913-453a-a561-a88adc41ce77` |
| Apply run | `ba54efd5-9767-491d-b854-cf0740f58426` |
| Post-audit run | `6266abfc-a0e8-4d8b-acaa-8b524a682b8a` |
| Строгий source-data artifact | `output/playwright/cms-regression/20260730T134150-771044Z` |
| `summary.json` SHA-256 | `C18AE1D60C3B79B1A6A56A16A413B6D899A147E902B3F98261A9BF03D6AA278E` |
| `source-before.json` / `source-after.json` SHA-256 | оба `D1361E4C510AC1AE52421F9F6E3485E8D5D814E4180B648F17E522A8537E2CFE` |

Детальный fingerprint: preview/apply `contents=1757`, `changed=1757`, `auto_ready=496`, `editorial_review=1261`, `technical_blocker=0`; post-apply full audit `blocker=590`, `warning=1016`; blocker breakdown `fallback_date_2000=561`, `required_field_missing=28`, `legacy_navigation_text=1`; warning breakdown `unicode_not_nfkc=486`, `duplicate_title=476`, `missing_legacy_media=36`, `legacy_text=18`; `197 auto_ready` имеют `unicode_not_nfkc`; текущий `verify` ошибочно возвращает `ok=true`.

Строгий artifact подтверждает `source_unchanged=true`, 7 tests, 3 ожидаемых XFAIL, включая `CMS-19/CMS-20`. Числа — characterization fingerprint, а не нормативные targets.

### 3.3. Crosswalk дефектов и gaps

| Defect | Реальный strict characterization node | Связанные gaps | Состояние до исправления |
|---|---|---|---|
| `CMS-19` | `tests/e2e/test_source_data.py::test_cms_19_auto_ready_is_not_semantically_clean` | `GAP-S6-02`, `GAP-S6-04` | **EXISTING — strict expected XFAIL** |
| `CMS-20` | `tests/e2e/test_source_data.py::test_cms_20_verify_fails_closed_on_post_apply_violations` | `GAP-S6-01`, `GAP-S6-04` | **EXISTING — strict expected XFAIL** |

После исправления эти же characterization nodes обязаны пройти как обычные positive regression без `XPASS`; дополнительные positive closure tests из §13 также должны пройти.

## 4. Роли

Каждый mutation требует активную session cookie и корректный CSRF.

| Возможность | Anonymous | Viewer | Editor | Publisher | Admin | Server operator |
|---|---:|---:|---:|---:|---:|---:|
| Dashboard/runs/items/dossier/filter/pages | 401 | Да | Да | Да | Да | CLI status |
| `Запустить аудит` | Нет | Нет | Нет | Да | Да | CLI audit |
| `Сформировать preview` | Нет | Нет | Нет | Да | Да | CLI preview |
| `Применить preview` | Нет | Нет | Нет | Нет | Да | CLI apply |
| `Откатить применение` | Нет | Нет | Нет | Нет | Да | CLI rollback |
| Semantic verify | Нет | Нет | Нет | Нет | Нет в UI; только при operator access | `python -m server.migration_preparation verify` |

UI скрывает запрещённые действия; API независимо возвращает `401/403` без side effects. Viewer/editor/publisher/admin видят в dashboard только реально существующие status/history/count/error поля audit/preparation runs; отдельного verify result endpoint или UI нет. Verify является release gate для server operator (включая администратора только при отдельном host-level operator access). Технические, интерфейсные и тестовые решения человеку не передаются.

## 5. Состояния и выбор run

### 5.1. Run state

| Kind/mode | From | Event | To | MUST |
|---|---|---|---|---|
| audit | — | create | `queued` | journal создан; normalized scope сохранён |
| audit | `queued` | worker claim | `running` | FIFO `(created_at,id)` |
| audit | `running` | success/error | `completed`/`failed` | counts либо safe error; terminal |
| audit | `running` | app restart | `queued` | тот же ID, `recovered=true`, reason сохранён |
| preview/apply | — | create | `queued` | journal создан |
| preview/apply | `queued` | worker claim | `running` | FIFO `(created_at,id)` |
| preview/apply | `running` | success | `completed` | counts/items согласованы |
| preview/apply | `running` | error | `failed` | safe error, terminal |
| preview/apply | `running` | restart | `queued` | recovery reason; повтор безопасен |
| apply | `completed` | rollback complete | `rolled_back` | компенсации завершены |
| любое | terminal/invalid | invalid transition | без изменения | machine error/409 |

Item states: preview=`planned`; changed apply=`applied`; no-op apply=`unchanged`; compensated=`rolled_back`. `failed` не используется как частичный результат.

### 5.2. Lifecycle кнопки `Запустить аудит`

| Role/state | Видимость и enabled | Действие / результат |
|---|---|---|
| viewer/editor | кнопка скрыта | прямой POST → 403, строка не создаётся |
| publisher/admin, active audit отсутствует | видима и enabled | click → `POST /api/admin/migration/audits` → HTTP 202; button немедленно disabled; возвращён `queued` run |
| matching full audit `queued/running` | видима, disabled, `aria-describedby="Аудит уже выполняется"` | повторного UI request нет; прямой повтор API или CLI → тот же ID, `deduplicated=true`, HTTP 202/CLI exit 0, новая row не создаётся |
| active audit с другим normalized scope | disabled с причиной | прямой API/CLI → 409 `ACTIVE_AUDIT_SCOPE_CONFLICT`, без row |
| `completed` | снова enabled | polling остановлен; counts/history обновлены; новый click создаёт новый ID |
| `failed` | enabled с label `Повторить аудит` и safe error | retry создаёт новый ID с `retry_of_run_id`; старый остаётся terminal |
| app restart во время `running` | disabled после загрузки, пока recovered run active | тот же run requeue → running → terminal; новая row не создаётся |

UI наблюдает `queued→running→completed/failed` через `GET /api/admin/migration/audits/{id}` каждые 1,5 секунды; polling прекращается в terminal state, при уходе со страницы или уничтожении DOM. Точная операторская команда полного аудита: `python -m server.migration_acceptance scan`; без scope flags это full audit. `--skip-external` разрешает только использование свежего cache по §6.1 и не ослабляет semantic gate.

### 5.3. Однозначный selection и duplicate-active contract

- **display run** — самый новый run по `(created_at,id)`, показываемый в status/history, включая failed;
- **actionable preview** — самый новый `preview/completed`, прошедший freshness и не имеющий completed/active apply; failed более новый run его не скрывает;
- **rollback target** — самый новый `apply/completed`, после которого нет другого completed apply; более старый apply отклоняется во всех UI/API/CLI;
- одновременно разрешён один глобальный active preparation run;
- identity audit = canonical scope + external-policy version; identity preview = rules version + fresh full audit ID + отсортированный набор `(content_id,version)`; identity apply = preview run ID;
- повтор active operation с той же identity всегда возвращает существующий run: HTTP 202 или CLI exit 0, `deduplicated=true`, новая row не создаётся; другой active identity → 409 `ACTIVE_RUN_CONFLICT`;
- failed preview/apply не «возобновляется» кнопкой: пользователь видит error и нажимает существующую `Сформировать preview`; worker restart восстанавливает только прерванный `running`;
- UI polling: 1,5 с для active run, прекращается в terminal state, при уходе с dashboard и при открытии dossier.

## 6. Versioned audit-code classification

Classification map version является частью `rules_version`; изменение таблицы требует нового rules version и полного preview.

| Audit code | Класс/результат | Условие resolved или reason mapping |
|---|---|---|
| `required_field_missing` | editorial | → `required_field_requires_editor`; только конкретное содержательное поле |
| `fallback_date_2000` | editorial | resolved только доказанной единственной датой; иначе → `date_requires_editor` |
| `unicode_not_nfkc` | resolved/technical | resolved только если **финальный proposed tree**, включая сгенерированный summary, NFKC; иначе technical |
| `encoding_suspect` | technical | никогда не передаётся редактору |
| `control_character` | resolved/technical | resolved только повторным audit proposed; иначе technical |
| `html_in_content` | technical | требуется программно безопасная конверсия |
| `legacy_navigation_text` | resolved/technical | resolved только если post-proposal audit не находит marker |
| `legacy_text` | resolved/editorial | confident conversion → resolved; ambiguous boundary → `text_extraction_ambiguous` |
| `migration_provenance` | resolved/technical | resolved только после удаления service fields из proposed и сохранения journal |
| `media_missing_or_invalid` | technical | файл/registry/reference должны быть исправлены технически |
| `static_media_missing` | technical | отсутствующий asset не перекладывается на редактора |
| `image_alt_missing` | resolved/technical | resolved формальным alt rule; иначе technical |
| `missing_legacy_media` | editorial | только если нужна содержательная замена; option `choose_media` |
| `duplicate_media_reference` | resolved/technical | resolved после проверки каждого repeatable field; cover=first photo допустим; оставшийся structural duplicate technical |
| `duplicate_title` | editorial | reason+canonical-choice options |
| `duplicate_content` | editorial | reason+merge/archive options |
| `internal_link_missing` | technical | broken route/reference |
| `unpublished_internal_link` | editorial | решение о публикационной связности |
| `internal_link_cycle` | technical | структурный blocker |
| `external_check_missing_or_stale` | technical | online check не выполнен и свежего cache нет; verify fail |
| `external_check_inconclusive` | technical | timeout/DNS/TLS/429/5xx/redirect loop не является содержательным решением |
| `external_link_unavailable` | editorial | только подтверждённый completed check; опции заменить/удалить/оставить |
| `unsafe_link` | technical | небезопасная схема |
| `invalid_date` | technical | невалидный формат; отличается от содержательно спорной корректной даты |

Default: неизвестный code любой severity блокирует verify; неизвестный blocker/warning классифицируется `technical_blocker`. `auto_ready` допускается только при 0 открытых blocker и warning post-proposal/post-apply audit.

### 6.1. External evidence cache

Cache identity строится из canonical URL: scheme и IDNA hostname lower-case; trailing dot/default port удалены; empty path=`/`; dot segments удалены; unreserved percent-escapes декодированы, остальные escapes upper-case; fragment удалён; query order **не меняется**, чтобы не смешивать семантически разные URL. Ключ — SHA-256 UTF-8 canonical URL.

Каждая запись содержит `url_identity`, `normalized_url`, `checked_at` UTC, `final_url` в той же нормализации, `result` (`available`, `content_unavailable`, `timeout`, `dns_error`, `tls_error`, `rate_limited`, `server_error`, `redirect_loop`, `unsafe`), `status_code|null`, `checker_version` и evidence digest. В production TTL равен ровно 24 часам относительно начала audit и повторно проверяется semantic verify.

При `check_external=false` разрешено только reuse записи с `checked_at` не старше 24 часов и совпадающей `checker_version`. Отсутствующий/просроченный cache создаёт `external_check_missing_or_stale`, классификацию `technical_blocker` и `ok=false`. Timeout/DNS/TLS/429/5xx/redirect loop создают технический blocker. Только реально выполненный online check или свежий cache с `result=content_unavailable` может создать editorial `external_link_unavailable`, когда необходимо содержательное решение по конкретной ссылке.

## 7. Детальные функциональные требования

Во всех строках «Expected» включает отсутствие неописанных side effects. Столбец `Evidence note` ниже — только ненормативная инвентаризация уже замеченных проверок; он не задаёт статус покрытия. Единственный нормативный trace для каждого FR — одноимённая строка матрицы §13, где указаны стабильный test/node ID, уровень, тип сценария, ожидаемый результат, `EXISTING` либо `MISSING` с Blocker gap и owner.

### 7.1. UI, роли и overview

| ID | Status | Actor/precondition | Steps | Expected / negative branches | Evidence note |
|---|---|---|---|---|---|
| FR-S6-001 | AS-IS | Viewer+ | В меню нажать `Миграция` | Heading `Перенесённые материалы`, acceptance/preparation sections; load error → toast, экран остаётся управляемым | PW existing |
| FR-S6-002 | MUST | Viewer/editor | Открыть dashboard, dossier, filters/pages | Все read controls видны; audit/preview/apply/rollback скрыты; прямой mutation → 403 | PW/API role |
| FR-S6-003 | MUST | Publisher | Открыть dashboard | Видны `Запустить аудит`, `Сформировать preview`; apply/rollback скрыты; POST apply → 403 | PW/API role |
| FR-S6-004 | MUST | Admin | Открыть dashboard | Видны разрешённые действия; apply/rollback зависят от actionable targets | PW role |
| FR-S6-005 | MUST | Anonymous/expired session | Открыть/read/mutate | 401, login state; dirty editor не теряется; DB/media unchanged | MISSING |
| FR-S6-006 | AS-IS | Viewer+ | Дождаться refresh | 4 preparation metric cards: contents, auto_ready, editorial_review, technical_blocker | PW existing |
| FR-S6-007 | MUST | Viewer+ | Раскрыть `История автоматической подготовки` | Все последние runs: mode/status/time/counts/error/preview link; open/close keyboard accessible | MISSING |
| FR-S6-008 | MUST | 390×844 и 320×720 | Через `☰` → `Миграция`; filter/dossier/back | Menu закрывается; controls доступны; no horizontal overflow; desktop diff side-by-side, mobile stacked | PW partial |

### 7.2. Audit freshness, background и recovery

| ID | Status | Actor/precondition | Steps | Expected / negative branches | Evidence note |
|---|---|---|---|---|---|
| FR-S6-009 | MUST | Publisher/admin, no active audit | Нажать `Запустить аудит` | POST 202 `queued`, toast, button disabled с причиной; polling показывает `queued→running→completed/failed` и останавливается; после terminal доступен новый запуск | PW basic + MISSING lifecycle |
| FR-S6-010 | MUST | Worker/direct duplicate | Claim или повтор audit/preparation | FIFO и single execution; matching active identity → тот же run, `deduplicated=true`, без новой row; другой active identity → 409; exception одного run не завершает scheduler | MISSING |
| FR-S6-011 | MUST | System | Найти latest full audit | Full = no content_id/type/year; order `(created_at,id)`; scope/check_external retained | MISSING |
| FR-S6-012 | MUST | Newer full audit queued/running | Нажать/POST preview | Disabled/409 `AUDIT_IN_PROGRESS`, active audit ID; старый completed не используется | MISSING |
| FR-S6-013 | MUST | Latest full audit failed | Нажать/POST preview | Disabled/409 `LATEST_FULL_AUDIT_FAILED`; требуется новый `Запустить аудит` | MISSING |
| FR-S6-014 | MUST | Latest full completed | Проверить audit items | Каждый flagged current content имеет ровно один audit item той же version; иначе `AUDIT_STALE` | MISSING |
| FR-S6-015 | MUST | `check_external=false` или cache старше 24h | Preview/verify | Только fresh cache §6.1 допустим; отсутствующее/просроченное evidence → `external_check_missing_or_stale`, `technical_blocker`, verify fail. Editorial возможен лишь после completed check с доказанным `content_unavailable` | MISSING |
| FR-S6-016 | MUST | App restart с running audit/preparation | Запустить приложение | Тот же run requeued, `recovered=true`, reason сохранён; UI продолжает polling; replay безопасен; terminal runs не меняются; retry failed audit создаёт новый ID | MISSING |
| FR-S6-017 | MUST | Completed apply | Запустить full post-apply audit и semantic verify | Выбран newest completed full audit после apply; newer active/failed, missing/current-version mismatch → fail closed | MISSING |
| FR-S6-018 | MUST | Failed newest display run, older eligible preview | Открыть dashboard | Failed виден; `Применить preview` использует actionable preview; `Сформировать preview` создаёт новый run; путь не потерян | MISSING |

### 7.3. Preview и детерминированные данные

| ID | Status | Actor/precondition | Steps | Expected / negative branches | Evidence note |
|---|---|---|---|---|---|
| FR-S6-019 | MUST | Publisher/admin, fresh audit | Нажать `Сформировать preview` | POST mode=preview, 202 queued, toast, offset=0, polling; кнопка disabled while active; duplicate API/CLI возвращает тот же run и `deduplicated=true` | PW basic + MISSING duplicate |
| FR-S6-020 | MUST | No fresh audit | Нажать/POST preview | UI disabled с причиной; API/CLI nonzero `AUDIT_REQUIRED/AUDIT_STALE`; queued shell не остаётся ложным completed | MISSING |
| FR-S6-021 | MUST | Worker preview | Рассчитать набор | Ровно один planned item для каждого post-schema `migration_review_required=1`; ни одного другого | SRC partial |
| FR-S6-022 | MUST | Unchanged DB, same rules/audit | Запустить два preview | before/proposed/hash/class/reasons/options/deterministic IDs идентичны; run IDs различны | MISSING |
| FR-S6-023 | AS-IS | Любой item | Сохранить dossier data | before/proposed, hashes, version, classification, transformations, reasons, recommendation, options | MP |
| FR-S6-024 | MUST | Любые строки | Нормализовать final proposal | NFKC/Cc rule выполняется **после всех generated values**; post-proposal `unicode_not_nfkc=0` | MISSING/GAP-02 |
| FR-S6-025 | MUST | Legacy body/biography/body_text | Извлечь crawl text | Breadcrumb/footer removed, deterministic paragraphs; ambiguous → editorial reason, raw retained in before | MP partial |
| FR-S6-026 | MUST | Empty summary + clean text | Generate summary | Schema max, word boundary, ellipsis; затем NFKC; empty source → no invention | MISSING |
| FR-S6-027 | MUST | `2000-*` date | Gather URL/title/text evidence | Exactly one valid calendar date → ISO; 0/multiple/invalid → editorial reason; no fabricated date | MP partial |
| FR-S6-028 | MUST | Legacy URL/redirect inventory | Validate/recover URL | Unique old_path→canonical route retained/recovered and journaled; missing/conflict → technical; ambiguous public meaning never guessed | MISSING/GAP-06 |
| FR-S6-029 | MUST | slug | Validate/recover slug | Unique schema-valid nonconflicting slug from canonical evidence → set; already valid → no-op; collision/ambiguity → technical | MISSING/GAP-06 |
| FR-S6-030 | MUST | content type | Validate/recover type | Unique importer/route/schema evidence → set/no-op; content-semantic ambiguity → editorial reason with options; structural mismatch → technical | MISSING/GAP-06 |
| FR-S6-031 | MUST | section/category | Validate/recover section | Unique existing taxonomy target → set/no-op; multiple meaningful targets → editorial; missing structural target → technical | MISSING/GAP-06 |
| FR-S6-032 | MUST | relations/internal links | Resolve relations | Unique redirect/content ID → relation; already valid → no-op; missing target → technical; multiple meaningful targets → editorial | MISSING/GAP-06 |
| FR-S6-033 | MUST | Each no-op field above | Run source-data proof | Evidence report states checked/valid/no-op counts and samples; absence of code alone is not proof | MISSING/GAP-06 |
| FR-S6-034 | MUST | cover/photo fields | Normalize | cover_alt/title, per-list dedup/alt/order; cover=photos[0] allowed; every repeatable field checked | MP/PW CMS-18 partial |
| FR-S6-035 | MUST | Leaflet no cover, ready PDF | Preview/apply | Deterministic first-page WebP path/media ID; unavailable PDF → editorial only if content replacement is needed, technical if corrupt/invalid | MP |
| FR-S6-036 | MUST | Service metadata | Preview | Known crawl fields removed only from proposed display data, provenance journaled; useful content retained | MP |
| FR-S6-037 | MUST | Required field empty | Classify | `required_field_requires_editor` with field/message; no guessed content | MP |
| FR-S6-038 | MUST | Audit codes | Apply map v1 | Technical priority > editorial > auto_ready; all codes/reasons mapped; unknown fail-closed | MISSING/GAP-04 |
| FR-S6-039 | MUST | Candidate auto_ready | Post-proposal audit | 0 blocker, 0 warning; info explicitly resolved/mapped; otherwise not auto_ready | MISSING/GAP-04 |

### 7.4. Filter, pagination и dossier

| ID | Status | Actor/precondition | Steps | Expected / negative branches | Evidence note |
|---|---|---|---|---|---|
| FR-S6-040 | AS-IS | Completed display run | Select class → `Применить фильтр` | offset=0; GET classification; all rows match; invalid class API 422 | PW/MP |
| FR-S6-041 | AS-IS | >50 items | `Дальше →`, `← Назад` | offsets 50/0; max 50; boundary disabled; beyond total empty stable view | PW |
| FR-S6-042 | MUST | Active/failed/no run | View list | Active → dossier pending; failed → safe error; no run/empty filter → explicit empty text | PW partial |
| FR-S6-043 | AS-IS | Item card | Нажать `Открыть досье` | Title/type/before/current version/class and `До/После` displayed | PW |
| FR-S6-044 | MUST | Reason exists | View dossier | Для **каждой** причины видны code, field_path (`Материал целиком` if empty), message; multiple reasons separate | MISSING/GAP-05 |
| FR-S6-045 | MUST | Recommendation/options | View dossier | Recommendation visible; each option shows label **and consequence**; no truncation hiding meaning | MISSING/GAP-05 |
| FR-S6-046 | MUST | Transformations | View dossier | Rule code, human label, affected field/value/evidence where applicable | MISSING/GAP-05 |
| FR-S6-047 | MUST | Long/missing values, desktop/mobile | Open dossier | Long text wraps/scrolls inside card; no viewport overflow; empty field explicitly labeled | MISSING/GAP-05 |
| FR-S6-048 | MUST | Keyboard/screen reader | Open/back/status update | Logical headings, labeled select/buttons, focus to dossier heading and back to opener, aria-busy/live status; `← К dashboard` reloads server state | MISSING/GAP-05 |

### 7.5. Apply, atomicity и public invariants

| ID | Status | Actor/precondition | Steps | Expected / negative branches | Evidence note |
|---|---|---|---|---|---|
| FR-S6-049 | MUST | Admin, actionable preview | Нажать `Применить preview` | Native confirm exact intent; Cancel → no HTTP; OK → POST preview ID/202/poll | PW |
| FR-S6-050 | MUST | Preview missing/wrong/noncompleted/not found | UI/API/CLI apply | Disabled or 409/404 stable machine code; no writes | MP partial |
| FR-S6-051 | MUST | Preview has technical_blocker or semantic mismatch | Apply | **Запрещён до first write**; `PREVIEW_NOT_SAFE`; UI disabled; rollout stops | MISSING/GAP-04 |
| FR-S6-052 | MUST | Valid preview | Preflight all items | Content exists, current version and normalized before_hash match, item count complete; any stale → whole run failed, no side effects | MP stale |
| FR-S6-053 | MUST | Hash changes without version change | Apply | `STALE_HASH`, no content/revision/media/file/audit/FTS side effects | MISSING/GAP-10 |
| FR-S6-054 | MUST | Changed/unchanged items | Apply | Changed: version+1, system revision, `migration_prepare` audit, result_version; unchanged: no version/revision | MP partial |
| FR-S6-055 | MUST | Apply completed | Verify business state | Content count, status histogram, migration flags, scheduled/review/deleted fields, published_version/slug/at and public responses unchanged | PW flag only/GAP-10 |
| FR-S6-056 | MUST | Apply completed | Verify indexes/media | media usages rebuilt; FTS continues to reflect published snapshots; generated media ready; quick/FK pass | MISSING/GAP-10 |
| FR-S6-057 | MUST | Repeat same apply | POST/CLI again | queued/running/completed same run returned, HTTP 202/CLI exit 0, `deduplicated=true`; no second row/versions/files/events | MP identity + MISSING flag |
| FR-S6-058 | MUST | Failure at each DB/file phase | Inject failure/restart | Recovery converges to fully completed or fully failed/compensated state; no partial visible set | MISSING/GAP-10 |

### 7.6. Rollback и generated-media retained-cache policy

| ID | Status | Actor/precondition | Steps | Expected / negative branches | Evidence note |
|---|---|---|---|---|---|
| FR-S6-059 | MUST | Admin, rollback target | Нажать `Откатить применение` | Confirm; Cancel no HTTP; OK POST target ID | PW confirm partial |
| FR-S6-060 | MUST | Rollback invalid target/role | nonlatest/wrong mode/queued/running/failed/notfound либо non-admin | 409/404/403 machine code, no writes. Terminal `rolled_back` **не ошибка**: 200/CLI exit 0, тот же run, `replayed=true`, no writes | MP role partial |
| FR-S6-061 | MUST | Multiple applies | UI/API/CLI target | Только newest completed apply без later completed apply; older ID → `ROLLBACK_TARGET_NOT_LATEST` | MISSING/GAP-09 |
| FR-S6-062 | MUST | No subsequent content edits | Rollback | Atomic preflight; applied items restored as new revisions/audit/usages/FTS; unchanged no version; run rolled_back | MP |
| FR-S6-063 | MUST | One item edited after apply | Rollback | 409 `ROLLBACK_CONFLICT` before write; all items/run unchanged | MP |
| FR-S6-064 | MUST | Generated media from apply | Rollback | **Retain policy:** immutable media row/file remain; usage удаляется; для каждого apply/item/media/digest сохраняется отдельное append-only provenance event; rollback добавляет event, старые IDs не перезаписываются; orphan — system cache, не задача человека | GAP-07 |
| FR-S6-065 | MUST | Terminal/in-flight/restart/before-after commit | Повторить rollback | terminal → 200/exit 0 same state `replayed=true`; in-flight duplicate ждёт ту же operation (timeout → 202 same ID); restart resumes same ID; до commit retry безопасен, после commit terminal; shared/preexisting/orphan retained | MISSING/GAP-07 |

## 8. DB/file commit and crash model

Generated assets use deterministic `(source_pdf_sha256, rules_version) → media_id/stored_name` and are owned by the system cache, not a single run.

Каждое создание или reuse фиксируется **новой** append-only provenance row/event с неизменяемым собственным ID и полями `event_id`, `event_type=generated|reused|retained_on_rollback`, `apply_run_id`, `preview_run_id`, `item_id`, `content_id`, `media_id`, `source_digest`, `generated_digest`, `rules_version`, `created_at`. Unique constraint не допускает повтор одного event identity при replay, но metadata media row не используется как перезаписываемый «последний владелец». Повторный apply того же digest создаёт provenance для нового apply/item; старые связи остаются доступными.

Apply phases:

1. preview already stores expected media ID/path/source;
2. render to run-specific temporary file and validate MIME/dimensions/checksum;
3. atomically promote to deterministic final path; an existing valid identical asset is reused;
4. one SQLite transaction writes/updates ready media metadata, content, revisions, audit, usages, FTS, items and completed run;
5. DB commit is the semantic commit point.

Failure/recovery:

- before promotion: temp deleted or safely ignored and removed on recovery;
- after promotion but before DB commit: recovered worker validates deterministic final file and reuses/registers it; it is an allowed retained cache orphan, never treated as content loss;
- DB rollback removes all DB changes for the attempt; final cache asset may remain by policy;
- after DB commit: run/items/media/content are complete together;
- restart changes running→queued; the same deterministic paths make replay idempotent;
- invalid existing final file/hash is `GENERATED_MEDIA_CONFLICT`, technical blocker, never overwritten blindly.

Rollback changes content only in one DB transaction; it never mutates generated files/media rows. Therefore there is no unsafe DB/file delete window. A separate future technical garbage collector may remove verified unused cache assets, but это не этап 7 и не решение редактора.

Rollback lifecycle:

1. первый запрос атомарно создаёт/claim-ит единственную operation для target run и переводит `completed→rolling_back`;
2. все versions/hashes проверяются до content writes;
3. одна транзакция создаёт компенсирующие revisions/audit, обновляет contents/usages/FTS/items, добавляет retained provenance events и ставит run=`rolled_back`; этот commit — semantic commit point;
4. crash до commit откатывает content transaction; restart продолжает ту же operation и ID;
5. crash после commit наблюдает terminal `rolled_back`;
6. повтор terminal возвращает тот же ресурс, `replayed=true`, без writes;
7. concurrent duplicate не создаёт operation: ждёт ту же operation до 15 минут; если она ещё active, возвращает HTTP 202 с тем же ID и `deduplicated=true`, иначе HTTP 200 terminal;
8. shared/preexisting/generated/orphan rows и files всегда retained; rollback удаляет только content usage.

## 9. Source-data и rollout requirements

| ID | Status | Actor/precondition | Steps | Expected / negative branches | Evidence note |
|---|---|---|---|---|---|
| FR-S6-066 | MUST | API viewer+ | GET migration/audit-run/preparation-run/items | Полные schemas §10; limits 1..200, offset≥0; viewer видит только status/history, не verify; 404/422 stable | MP partial |
| FR-S6-067 | MUST | Publisher/admin + CSRF | POST audit/preview/apply/rollback | Schemas §10; matching active → HTTP 202 same ID `deduplicated=true`; terminal rollback repeat → 200 `replayed=true`; role isolation | MP partial |
| FR-S6-068 | MUST | CLI operator | `migration_acceptance scan`; preparation `preview/apply/status/verify/rollback` | Точные команды §10; success JSON/exit 0, error JSON/exit 2; duplicate/replay flags; DB/media pairing explicit | MISSING |
| FR-S6-069 | MUST | CLI error | Invalid ID/state/stale/semantic failure | Stable error JSON stdout, no traceback/path/secret, exit code 2, no partial side effects | MISSING/GAP-10 |
| FR-S6-070 | MUST | Semantic verify | Run after apply+post-audit | Stable schema 1.0; fully mapped editorial counts могут быть ненулевыми; auto_ready/technical/unknown/unmapped чисты; violation → `ok=false`, exit 2, rollout stop | CMS-20 XFAIL + MISSING positive |
| FR-S6-071 | MUST | UI receives API error | Trigger 401/403/404/409/422/500 | Safe Russian message/toast/status; actionable run/audit ID retained; controls recover | MISSING/GAP-10 |
| FR-S6-072 | MUST | API/CLI/worker logs | Failure with filesystem/internal error | Machine code + sanitized message; no absolute path, secret, cookie, CSRF; server audit retains internal correlation ID | MISSING/GAP-10 |
| FR-S6-073 | MUST | Raw source | Measure→SQLite backup→schema | §3.2 identities совпадают; 1 756 flags raw; 1 757 post-schema due exact migration/content ID; contents unchanged; raw DB/media unchanged | SRC partial/GAP-03 |
| FR-S6-074 | MUST | Post-schema copy | reconcile→full audit→preview→apply→post-audit→semantic verify | Every flagged current version mapped; no technical residual; editorial-only queue; source untouched | SRC currently insufficient/GAP-11 |
| FR-S6-075 | MUST | Source-data evidence | Save JSON evidence | Includes database identity, timestamp, rules, schema, audit/preview/apply IDs, counts, issue/class mapping, invariants | MISSING/GAP-11 |
| FR-S6-076 | MUST | Production operator | Run `production_apply_stage12.sh BACKUP_ID` | Verify checksum, backup≤24h, clean main/origin, source schema11, restore drill, schema12, same semantic gates before production | ROLLOUT partial |
| FR-S6-077 | MUST | Restored/prod gate failure | Inject technical/post-audit mismatch | Nonzero stop before next environment; reports mode 600; production not touched if restore fails | MISSING/GAP-11 |
| FR-S6-078 | MUST | Successful rollout | Complete production checks | Counts/workflow/flags/public/FTS/revision/audit/media/quick/FK asserted; backup/run IDs and rollback command recorded | MISSING/GAP-11 |

## 10. API/CLI machine contracts

Все timestamps — RFC 3339 UTC или `null`; IDs — UUID strings; неизвестные response fields допустимо добавлять только в новой minor contract version, а удалять/переименовывать — только в новой major version.

### 10.1. Resource schemas

`AuditRun` содержит все поля: `contract_version="1.0"`, `ok=true`, `id`, `kind="audit"`, `rules_version`, `scope={content_id|null,content_type|null,year|null,check_external}`, `status`, `counts={contents,blocker,warning,info}`, `error={code,message,correlation_id|null}|null`, `started_by|null`, `created_at`, `started_at|null`, `finished_at|null`, `retry_of_run_id|null`, `recovered`, `recovery_reason|null`, `deduplicated`.

`PreparationRun` содержит: `contract_version`, `ok`, `id`, `kind="preparation"`, `rules_version`, `mode=preview|apply`, `status`, `preview_run_id|null`, `audit_run_id|null`, `counts={contents,changed,auto_ready,editorial_review,technical_blocker}`, `error|null`, `started_by|null`, `created_at`, `started_at|null`, `finished_at|null`, `rolled_back_at|null`, `recovered`, `recovery_reason|null`, `deduplicated`, `replayed`.

`PreparationItem` содержит: `id`, `run_id`, `content_id`, `content_version`, `current_content_version`, `result_version|null`, `state`, `classification`, `title`, `content_type`, `legacy_url|null`, `before`, `proposed`, `before_hash`, `proposed_hash`, `transformations[]`, `reasons[]`, `recommendation`, `options[]`. Каждый reason содержит `code`, `field_path|null`, `message`, `severity`, `classification`, `evidence`; каждая option — `code`, `label`, `consequence`.

`MigrationOverview` содержит: `contract_version`, `ok`, существующие `runs`, `totals`, `by_type`, `review_by_type`, `source`; `acceptance={latest_audit,active_audit_id,runs}`; `preparation={counts,latest_applied,display_run,actionable_preview_id,rollback_target_id,active_run_id,runs}`. Verify result в resource отсутствует.

### 10.2. Endpoint contracts

| Endpoint | Request | Success | Side effects / exact branches |
|---|---|---|---|
| `GET /api/admin/migration` | no body; viewer+ cookie | 200 `MigrationOverview` | read-only |
| `GET /api/admin/migration/audits/{run_id}` | path UUID; viewer+ | 200 `AuditRun` | 404 `RUN_NOT_FOUND`; read-only |
| `GET /api/admin/migration/preparations/{run_id}` | path UUID; viewer+ | 200 `PreparationRun` | 404 `RUN_NOT_FOUND`; read-only |
| `GET /api/admin/migration/preparations/{run_id}/items` | `classification?`, `limit=1..200`, `offset>=0`; viewer+ | 200 `{contract_version,ok,items:PreparationItem[],total,limit,offset}` | invalid query → 422; unknown run → 404; read-only |
| `POST /api/admin/migration/audits` | publisher/admin + CSRF; JSON `{content_type:null|string,year:null|1900..2100,check_external:true|false}` | 202 `AuditRun` | matching active → same ID, `deduplicated=true`, no row; conflicting active scope → 409 |
| `POST /api/admin/migration/preparations` preview | publisher/admin + CSRF; JSON `{mode:"preview",preview_run_id:null}` | 202 `PreparationRun` | matching active identity → same ID, `deduplicated=true`; stale/missing audit → 409, no row |
| `POST /api/admin/migration/preparations` apply | admin + CSRF; JSON `{mode:"apply",preview_run_id:"uuid"}` | 202 `PreparationRun` | same preview active/completed apply → same ID, `deduplicated=true`; invalid/unsafe preview → 404/409 before writes |
| `POST /api/admin/migration/preparations/{run_id}/rollback` | admin + CSRF; empty body | 200 terminal `PreparationRun` | first synchronous completion or terminal replay (`replayed=true`); active duplicate after 15-minute wait → 202 same ID `deduplicated=true`; invalid target → 404/409 |

`POST` без CSRF или допустимой роли не создаёт run/operation. FastAPI/Pydantic validation происходит до domain handler и возвращает HTTP 422 `VALIDATION_ERROR` с `violations=[{field_path,code,message}]`; domain 422 использует специализированные `INVALID_MODE`, `INVALID_CLASSIFICATION`, `INVALID_PAGINATION`.

Единственный duplicate-active outcome для audit/preview/apply: HTTP 202, существующий ID, `deduplicated=true`, новая row не создаётся. Никакой альтернативы «иногда 409 для того же identity» нет; 409 используется только для несовместимой active identity.

### 10.3. CLI contracts

| Command | Аргументы | Success data |
|---|---|---|
| `python -m server.migration_acceptance scan` | optional `--database PATH --content-id UUID --content-type TYPE --year YYYY --actor-id UUID --skip-external` | `AuditRun`; без scope flags — full audit |
| `python -m server.migration_preparation preview` | optional `--database PATH --actor-id UUID --skip-audit` | `PreparationRun`; `--skip-audit` требует fresh full audit и external evidence |
| `python -m server.migration_preparation apply` | required `--preview-run-id UUID`; optional `--database PATH --actor-id UUID` | `PreparationRun` |
| `python -m server.migration_preparation status` | optional `--database PATH --run-id UUID` | run при ID, иначе preparation summary по schema `MigrationOverview.preparation` |
| `python -m server.migration_preparation verify` | optional `--database PATH` | semantic result §10.4; только operator/admin с host-level access |
| `python -m server.migration_preparation rollback` | required `--run-id UUID`; optional `--database PATH --actor-id UUID` | `PreparationRun` |

Каждый CLI success печатает в stdout ровно один JSON:

```json
{"contract_version":"1.0","ok":true,"command":"preview","deduplicated":false,"replayed":false,"data":{}}
```

и завершает exit `0`. Matching active command возвращает тот же resource с `deduplicated=true`; terminal rollback repeat — `replayed=true`. Ошибка аргументов, domain state или verify печатает в stderr ровно один JSON без usage/traceback/path/secret и завершает exit `2`:

```json
{"contract_version":"1.0","ok":false,"command":"apply","code":"STALE_PREVIEW","message":"Preview устарел","run_id":"uuid-or-null","audit_run_id":"uuid-or-null","violations":[]}
```

### 10.4. Semantic verify

Freshness predicate:

1. выбирается newest completed apply `A`;
2. выбирается newest full completed post-audit `P` по `(finished_at,id)`;
3. `P.started_at` и `P.finished_at` позже `A.finished_at`;
4. нет более нового full audit в `queued/running/failed`;
5. каждый текущий flagged content ровно один раз сопоставлен `A.item`, `current version=result_version`, и ровно один раз `P.audit_item` той же version;
6. каждый open issue сопоставлен classification version, item и видимой reason;
7. у `auto_ready` ноль blocker/warning; technical/unknown/unmapped issues ноль;
8. внешний evidence fresh по §6.1; counts равны DB facts.

Успех **может** иметь ненулевые editorial blocker/warning, если каждый относится к `editorial_review` и полностью mapped:

```json
{
  "contract_version": "1.0",
  "ok": true,
  "code": "PREPARATION_VERIFIED",
  "rules_version": "1.0.0",
  "apply_run_id": "uuid",
  "audit_run_id": "uuid",
  "counts": {
    "contents": 10,
    "mapped_items": 10,
    "auto_ready": 4,
    "editorial_review": 6,
    "technical_blocker": 0,
    "open_blocker": 2,
    "open_warning": 3,
    "open_editorial_blocker": 2,
    "open_editorial_warning": 3,
    "open_technical_blocker": 0,
    "open_technical_warning": 0,
    "open_unknown": 0,
    "auto_ready_open_blocker": 0,
    "auto_ready_open_warning": 0,
    "unmapped_editorial_issue": 0,
    "violations": 0
  },
  "violations": []
}
```

Failure даёт CLI exit `2`, `ok=false`; rollout немедленно прекращается:

```json
{
  "contract_version": "1.0",
  "ok": false,
  "code": "SEMANTIC_VERIFY_FAILED",
  "message": "Проверка автоматической подготовки не пройдена",
  "rules_version": "1.0.0",
  "apply_run_id": "uuid-or-null",
  "audit_run_id": "uuid-or-null",
  "counts": {
    "contents": 10,
    "mapped_items": 10,
    "auto_ready": 4,
    "editorial_review": 6,
    "technical_blocker": 1,
    "open_blocker": 3,
    "open_warning": 3,
    "open_editorial_blocker": 2,
    "open_editorial_warning": 3,
    "open_technical_blocker": 1,
    "open_technical_warning": 0,
    "open_unknown": 0,
    "auto_ready_open_blocker": 1,
    "auto_ready_open_warning": 0,
    "unmapped_editorial_issue": 0,
    "violations": 1
  },
  "violations": [{
    "code": "AUTO_READY_HAS_OPEN_BLOCKER",
    "content_id": "uuid",
    "content_version": 1,
    "item_classification": "auto_ready",
    "audit_issue_code": "external_check_missing_or_stale",
    "field_path": "data.body[0].url",
    "message": "Нет свежего результата проверки внешней ссылки"
  }]
}
```

Порядок `violations`: `code,content_id,field_path`. Поля не удаляются в пределах contract version.

### 10.5. API error envelope

```json
{
  "detail": {
    "contract_version": "1.0",
    "code": "STALE_PREVIEW",
    "message": "Preview устарел",
    "run_id": "uuid-or-null",
    "audit_run_id": "uuid-or-null",
    "correlation_id": "uuid-or-null",
    "counts": {},
    "violations": []
  }
}
```

| HTTP | Machine codes |
|---|---|
| 401 | `AUTH_REQUIRED`, `SESSION_EXPIRED` |
| 403 | `ROLE_FORBIDDEN`, `CSRF_INVALID` |
| 404 | `RUN_NOT_FOUND`, `PREVIEW_NOT_FOUND`, `CONTENT_NOT_FOUND` |
| 409 | `ACTIVE_AUDIT_SCOPE_CONFLICT`, `ACTIVE_RUN_CONFLICT`, `AUDIT_REQUIRED`, `AUDIT_IN_PROGRESS`, `AUDIT_STALE`, `LATEST_FULL_AUDIT_FAILED`, `PREVIEW_NOT_SAFE`, `STALE_VERSION`, `STALE_HASH`, `ROLLBACK_CONFLICT`, `ROLLBACK_TARGET_NOT_LATEST`, `INVALID_RUN_STATE`, `GENERATED_MEDIA_CONFLICT`, `SEMANTIC_VERIFY_FAILED` |
| 422 | `INVALID_MODE`, `INVALID_CLASSIFICATION`, `INVALID_PAGINATION`, `VALIDATION_ERROR` |
| 500 | `INTERNAL_PREPARATION_ERROR` с correlation ID, без внутренних путей |

## 11. Blocking gaps

| Gap | Priority | Owner | Closure condition |
|---|---|---|---|
| GAP-S6-01 | Blocker | Backend | `CMS-20`: текущий verify заменён stable fail-closed contract; strict XFAIL становится passing regression |
| GAP-S6-02 | Blocker | Data rules | `CMS-19`: final proposal NFKC; 0 auto_ready blocker/warning; strict XFAIL становится passing regression |
| GAP-S6-03 | Blocker | Migration QA | Автотест 1 756→1 757 по migration/content ID + raw immutability |
| GAP-S6-04 | **Blocker** | Backend/data rules | `CMS-19/CMS-20`: semantic post-audit mapping, unknown fail-closed, apply gate |
| GAP-S6-05 | **Blocker** | Frontend/UX QA | Полный visible dossier desktop/mobile + a11y tests |
| GAP-S6-06 | Blocker | Migration/data | Отдельные URL/slug/type/section/relation rules/evidence |
| GAP-S6-07 | **Blocker** | Media/backend | Retained-cache, append-only provenance и terminal/in-flight/restart rollback реализованы и протестированы |
| GAP-S6-08 | Blocker | Backend/frontend/data | Audit lifecycle, freshness и external-evidence TTL едины в UI/API/CLI |
| GAP-S6-09 | Blocker | Backend/frontend | Run identity/dedup, selection/recovery и rollback target/replay едины |
| GAP-S6-10 | **Blocker** | QA + component owners | Полные API/CLI schemas и characterization/regression для всех missing functional paths |
| GAP-S6-11 | **Blocker** | Source-data/release QA | Source/rollout не доверяют слабому verify, выполняют post-audit semantic gate |
| GAP-S6-12 | Blocker | Documentation | План/report/guide/registry/coverage синхронизированы после fixes |

Ни один gap не требует решения человека по материалам.

## 12. Критерии допуска к этапу 7

Этап 7 допускается только одновременно при:

1. требования версии 3.0 получили `ACCEPT` в review cycle 3; это само по себе не подтверждает реализацию;
2. всех Blocker gaps closed;
3. доказанном фазовом baseline и неизменном raw source;
4. fresh full post-apply audit текущих versions;
5. semantic verify `ok=true`, CLI exit 0 и нулевых violations;
6. `auto_ready`: 0 blocker/warning; technical/unknown issues: 0;
7. каждый editorial issue однозначно сопоставлен item/reason и полностью видим в dossier;
8. apply/rollback/crash/generated-media/public/FTS/media/revision/audit invariants пройдены;
9. `CMS-19/CMS-20` strict XFAIL стали passing regressions; server, Playwright smoke, два full regression и source-data прошли без необъяснённых FAIL/ERROR/XPASS, а оставшиеся ожидаемые XFAIL точно совпадают с реестром;
10. restore drill и fail-fast rollout воспроизводимы;
11. docs, defects registry и coverage соответствуют факту.

Человек после этого привлекается только на этапе 7 для решения по содержанию конкретных `editorial_review` материалов.

## 13. Coverage matrix

Правила: `EXISTING` указывает только полный фактический pytest node ID, проверенный в репозитории. `EXISTING — strict expected XFAIL` означает существующую честную characterization открытого дефекта. `planned:` обозначает будущий node ID и всегда имеет статус `MISSING — Blocker gap / owner`. Наличие общего зелёного теста не закрывает явно указанную missing branch.

| FR | Test ID / planned node | Level | Scenario type | Expected | Status |
|---|---|---|---|---|---|
| 001 | `tests/e2e/test_admin_migration.py::test_migration_dashboard_and_audit` | PW | positive transition | dashboard opens | EXISTING |
| 002 | `tests/e2e/test_admin_migration.py::test_full_role_capabilities`; `tests/test_migration_preparation.py::test_preparation_api_roles_preview_apply_items_and_rollback`; planned: `tests/test_migration_preparation_contract.py::test_read_roles_and_mutation_denials` | PW/API | role positive/negative | read allowed; mutation 403 | EXISTING; MISSING — GAP-S6-10 / QA |
| 003 | `tests/e2e/test_admin_migration.py::test_full_role_capabilities`; `tests/test_migration_preparation.py::test_preparation_api_roles_preview_apply_items_and_rollback` | PW/API | role | publisher preview, not apply | EXISTING |
| 004 | `tests/e2e/test_admin_migration.py::test_full_role_capabilities`; `tests/test_migration_preparation.py::test_preparation_api_roles_preview_apply_items_and_rollback` | PW/API | role | admin controls | EXISTING |
| 005 | planned: `tests/e2e/test_stage6_contract.py::test_expired_session_preserves_state_and_denies_mutation` | PW/API | negative/recovery | 401, reauth, no loss/write | MISSING — GAP-S6-10 / QA |
| 006 | `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow` | PW | positive | four metrics | EXISTING |
| 007 | planned: `tests/e2e/test_stage6_contract.py::test_preparation_history_complete_and_accessible` | PW/a11y | positive | complete history | MISSING — GAP-S6-10 / Frontend QA |
| 008 | `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow`; planned: `tests/e2e/test_stage6_contract.py::test_stage6_full_mobile_cjm_at_320_and_390` | PW | viewport/transition | no overflow/full CJM | EXISTING; MISSING — GAP-S6-05 / UX QA |
| 009 | `tests/e2e/test_admin_migration.py::test_migration_dashboard_and_audit`; planned: `tests/e2e/test_stage6_audit_contract.py::test_audit_button_full_lifecycle` | PW/API | transition/recovery | queued→running→terminal, polling stop/retry | EXISTING basic; MISSING — GAP-S6-08 / Frontend+Backend QA |
| 010 | planned: `tests/test_stage6_contract.py::test_worker_fifo_and_duplicate_active_identity` | server | concurrency | single FIFO/dedup | MISSING — GAP-S6-10 / Backend QA |
| 011 | planned: `tests/test_stage6_contract.py::test_latest_full_audit_order_and_scope` | server | boundary | deterministic newest full | MISSING — GAP-S6-08 / Backend |
| 012 | planned: `tests/test_stage6_contract.py::test_active_audit_blocks_preview_and_duplicate_reuses_id` | server/PW | race negative | old audit rejected; duplicate reused | MISSING — GAP-S6-08 / Backend+Frontend QA |
| 013 | planned: `tests/test_stage6_contract.py::test_failed_latest_audit_blocks_and_retry_gets_new_id` | server/PW | negative/recovery | failed latest blocks/new retry | MISSING — GAP-S6-08 / Backend+Frontend QA |
| 014 | planned: `tests/test_stage6_contract.py::test_full_audit_covers_every_current_flagged_version_once` | server | negative | complete current mapping | MISSING — GAP-S6-08 / Backend |
| 015 | planned: `tests/test_stage6_external_evidence.py::test_skipped_or_stale_external_check_is_technical_blocker`; planned: `tests/test_stage6_external_evidence.py::test_fresh_confirmed_unavailable_link_is_editorial` | server/source | negative/positive | TTL fail-closed/editorial only after check | MISSING — GAP-S6-08 / Data+Backend QA |
| 016 | planned: `tests/test_stage6_contract.py::test_audit_and_preparation_restart_requeue_same_run` | server | transition | running→queued→terminal same ID | MISSING — GAP-S6-10 / Backend QA |
| 017 | `tests/e2e/test_source_data.py::test_cms_20_verify_fails_closed_on_post_apply_violations`; planned: `tests/test_stage6_semantic_verify.py::test_verify_requires_fresh_post_apply_audit_for_current_versions` | source/server | characterization/positive/negative | false verify fingerprint then strict freshness | EXISTING — strict expected XFAIL; MISSING — GAP-S6-04 / Backend |
| 018 | planned: `tests/e2e/test_stage6_contract.py::test_failed_display_run_does_not_hide_actionable_preview` | PW | recovery transition | valid action reachable | MISSING — GAP-S6-09 / Frontend |
| 019 | `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow`; planned: `tests/test_stage6_contract.py::test_duplicate_preview_returns_same_active_run` | PW/API | positive/dedup transition | preview queue/poll/dedup | EXISTING basic; MISSING — GAP-S6-09 / Backend QA |
| 020 | planned: `tests/test_stage6_contract.py::test_preview_without_fresh_audit_is_disabled_and_rejected` | server/PW | negative | disabled/409/no shell | MISSING — GAP-S6-08 / Backend+Frontend QA |
| 021 | `tests/e2e/test_source_data.py::test_stage6_real_source_automatic_preparation` | source | positive | one item per flag | EXISTING |
| 022 | planned: `tests/test_stage6_data_rules.py::test_preview_is_deterministic_for_same_audit_and_versions` | server | repeat/no-op | identical proposal | MISSING — GAP-S6-10 / Data QA |
| 023 | `tests/test_migration_preparation.py::test_preview_apply_is_idempotent_and_rollback_is_compensating` | server | positive | dossier persisted | EXISTING |
| 024 | `tests/e2e/test_source_data.py::test_cms_19_auto_ready_is_not_semantically_clean`; planned: `tests/test_stage6_data_rules.py::test_final_generated_tree_is_nfkc` | source/server | characterization/positive | 197 fingerprint then zero | EXISTING — strict expected XFAIL; MISSING — GAP-S6-02 / Data rules |
| 025 | `tests/test_migration_preparation.py::test_preview_apply_is_idempotent_and_rollback_is_compensating`; planned: `tests/test_stage6_data_rules.py::test_legacy_text_ambiguous_boundaries_are_not_guessed` | server | positive/negative/no-op | safe extraction | EXISTING positive; MISSING — GAP-S6-10 / Data QA |
| 026 | `tests/e2e/test_source_data.py::test_cms_19_auto_ready_is_not_semantically_clean`; planned: `tests/test_stage6_data_rules.py::test_summary_boundary_ellipsis_and_nfkc` | source/server | characterization/boundary | summary final NFKC | EXISTING — strict expected XFAIL; MISSING — GAP-S6-02 / Data rules |
| 027 | `tests/test_migration_preparation.py::test_unresolved_date_and_duplicate_are_editorial_not_technical`; planned: `tests/test_stage6_data_rules.py::test_unique_valid_calendar_date_is_recovered` | server | negative/positive | no fabricated date | EXISTING editorial branch; MISSING — GAP-S6-10 / Data QA |
| 028 | planned: `tests/test_stage6_field_rules.py::test_url_rule_positive_ambiguous_and_noop` | server/source | positive/negative/no-op | URL oracle | MISSING — GAP-S6-06 / Migration QA |
| 029 | planned: `tests/test_stage6_field_rules.py::test_slug_rule_positive_collision_and_noop` | server/source | positive/negative/no-op | slug oracle | MISSING — GAP-S6-06 / Migration QA |
| 030 | planned: `tests/test_stage6_field_rules.py::test_type_rule_positive_ambiguous_and_noop` | server/source | positive/negative/no-op | type oracle | MISSING — GAP-S6-06 / Data QA |
| 031 | planned: `tests/test_stage6_field_rules.py::test_section_rule_positive_ambiguous_and_noop` | server/source | positive/negative/no-op | section oracle | MISSING — GAP-S6-06 / Data QA |
| 032 | planned: `tests/test_stage6_field_rules.py::test_relation_rule_positive_missing_ambiguous_and_noop` | server/source | positive/negative/no-op | relation oracle | MISSING — GAP-S6-06 / Data QA |
| 033 | planned: `tests/e2e/test_source_data.py::test_stage6_noop_field_evidence_has_counts_and_samples` | source | evidence | checked/no-op proof | MISSING — GAP-S6-06 / Source QA |
| 034 | `tests/test_migration_preparation.py::test_cover_reused_as_first_photo_is_not_duplicate_media_issue`; `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow` | server/PW | regression/boundary | CMS-18 remains fixed | EXISTING |
| 035 | `tests/test_migration_preparation.py::test_leaflet_pdf_cover_is_generated_and_registered`; planned: `tests/test_stage6_generated_media.py::test_leaflet_invalid_pdf_fails_without_partial_asset` | server | positive/negative | deterministic cover/safe fail | EXISTING positive; MISSING — GAP-S6-10 / Backend+Media QA |
| 036 | `tests/test_migration_preparation.py::test_preview_apply_is_idempotent_and_rollback_is_compensating` | server | positive | provenance removed/journaled | EXISTING |
| 037 | `tests/test_migration_preparation.py::test_unresolved_date_and_duplicate_are_editorial_not_technical` | server | negative/content role | missing content editorial | EXISTING |
| 038 | planned: `tests/test_stage6_classification.py::test_every_known_and_unknown_audit_code_is_fail_closed` | server | exhaustive/negative | versioned map/unknown | MISSING — GAP-S6-04 / Data rules |
| 039 | `tests/e2e/test_source_data.py::test_cms_19_auto_ready_is_not_semantically_clean`; planned: `tests/test_stage6_semantic_verify.py::test_auto_ready_has_no_open_blocker_or_warning` | source/server | characterization/positive | 197 fingerprint then clean | EXISTING — strict expected XFAIL; MISSING — GAP-S6-04 / Backend |
| 040 | `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow`; planned: `tests/test_migration_preparation_contract.py::test_invalid_classification_returns_stable_422` | PW/API | positive/negative | filter and 422 | EXISTING positive; MISSING — GAP-S6-10 / Backend QA |
| 041 | `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow` | PW | boundary transition | offsets 0/50 | EXISTING |
| 042 | planned: `tests/e2e/test_stage6_contract.py::test_item_list_empty_active_and_failed_states` | PW | state matrix | explicit states | MISSING — GAP-S6-10 / Frontend QA |
| 043 | `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow` | PW | positive transition | before/after dossier | EXISTING |
| 044 | planned: `tests/e2e/test_stage6_dossier.py::test_every_reason_shows_code_field_and_message` | PW | positive/multiple/empty | visible reason dossier | MISSING — GAP-S6-05 / Frontend QA |
| 045 | planned: `tests/e2e/test_stage6_dossier.py::test_options_show_label_and_consequence` | PW | positive | full option meaning | MISSING — GAP-S6-05 / Frontend QA |
| 046 | planned: `tests/e2e/test_stage6_dossier.py::test_transformations_show_rule_field_value_and_evidence` | PW | positive | visible rule evidence | MISSING — GAP-S6-05 / Frontend QA |
| 047 | planned: `tests/e2e/test_stage6_dossier.py::test_long_and_empty_dossier_values_on_desktop_and_mobile` | PW | boundary/viewport | no hidden content | MISSING — GAP-S6-05 / UX QA |
| 048 | planned: `tests/e2e/test_stage6_dossier.py::test_dossier_keyboard_focus_labels_and_live_status` | PW/a11y | transition | focus/labels/live | MISSING — GAP-S6-05 / UX QA |
| 049 | `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow` | PW | cancel/confirm | cancel no request; confirm 202 | EXISTING |
| 050 | planned: `tests/test_migration_preparation_contract.py::test_apply_invalid_preview_states_have_stable_errors_and_no_writes` | server/API | negative states | 404/409/no writes | MISSING — GAP-S6-10 / Backend QA |
| 051 | `tests/e2e/test_source_data.py::test_cms_20_verify_fails_closed_on_post_apply_violations`; planned: `tests/test_stage6_semantic_verify.py::test_apply_rejects_technical_or_semantically_unsafe_preview_before_write` | source/server | characterization/negative | current unsafe result then prewrite reject | EXISTING — strict expected XFAIL; MISSING — GAP-S6-04 / Backend |
| 052 | `tests/test_migration_preparation.py::test_apply_rejects_stale_preview_without_partial_changes` | server | negative atomicity | no partial changes | EXISTING |
| 053 | planned: `tests/test_stage6_contract.py::test_apply_stale_hash_without_version_change_has_no_side_effects` | server | negative atomicity | hash reject | MISSING — GAP-S6-10 / Backend QA |
| 054 | `tests/test_migration_preparation.py::test_preview_apply_is_idempotent_and_rollback_is_compensating`; planned: `tests/test_stage6_contract.py::test_apply_unchanged_item_creates_no_revision` | server | positive/no-op | revision rules | EXISTING changed branch; MISSING — GAP-S6-10 / Backend QA |
| 055 | `tests/test_migration_preparation.py::test_preview_apply_is_idempotent_and_rollback_is_compensating`; `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow`; planned: `tests/test_stage6_invariants.py::test_apply_preserves_workflow_and_publication_state` | server/PW | invariant | flags/workflow/public unchanged | EXISTING flags; MISSING — GAP-S6-10 / Backend+Frontend QA |
| 056 | planned: `tests/test_stage6_invariants.py::test_apply_preserves_fts_media_usages_quick_check_and_foreign_keys` | server | invariant | FTS/media/DB clean | MISSING — GAP-S6-10 / Search+Media QA |
| 057 | `tests/test_migration_preparation.py::test_preview_apply_is_idempotent_and_rollback_is_compensating`; planned: `tests/test_migration_preparation_contract.py::test_duplicate_apply_returns_same_run_with_deduplicated_flag` | server/API | repeat | same run/no duplicate | EXISTING identity; MISSING — GAP-S6-09 / Backend QA |
| 058 | planned: `tests/test_stage6_crash_recovery.py::test_apply_recovers_at_every_db_and_file_phase` | server | fault injection/restart | convergent state | MISSING — GAP-S6-10 / Backend QA |
| 059 | `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow`; planned: `tests/e2e/test_stage6_contract.py::test_rollback_cancel_sends_no_request` | PW | confirm/cancel | rollback/no request | EXISTING confirm; MISSING — GAP-S6-10 / Frontend QA |
| 060 | `tests/test_migration_preparation.py::test_preparation_api_roles_preview_apply_items_and_rollback`; planned: `tests/test_migration_preparation_contract.py::test_rollback_invalid_targets_roles_and_terminal_replay` | server/API | role/negative/replay | 403/404/409 or replay 200 | EXISTING role/basic; MISSING — GAP-S6-09 / Backend QA |
| 061 | planned: `tests/test_migration_preparation_contract.py::test_only_latest_completed_apply_is_rollback_target` | server/API | transition/conflict | older rejected | MISSING — GAP-S6-09 / Backend |
| 062 | `tests/test_migration_preparation.py::test_preview_apply_is_idempotent_and_rollback_is_compensating` | server | positive transition | compensating versions/state | EXISTING |
| 063 | `tests/test_migration_preparation.py::test_rollback_conflict_stops_before_changing_any_material` | server | negative atomicity | no item changed | EXISTING |
| 064 | `tests/test_migration_preparation.py::test_leaflet_pdf_cover_is_generated_and_registered`; planned: `tests/test_stage6_generated_media.py::test_rollback_retains_cache_and_appends_provenance` | server | generated/rollback invariant | row/file/events retained | EXISTING generation; MISSING — GAP-S6-07 / Media QA |
| 065 | planned: `tests/test_stage6_generated_media.py::test_rollback_terminal_inflight_restart_shared_and_orphan_replay` | server/API | shared/retry/restart | retained/idempotent | MISSING — GAP-S6-07 / Media+Backend QA |
| 066 | `tests/test_migration_preparation.py::test_preparation_api_roles_preview_apply_items_and_rollback`; planned: `tests/test_migration_preparation_contract.py::test_get_migration_run_and_items_full_schemas_and_limits` | API | positive/negative | stable schemas/limits | EXISTING basic; MISSING — GAP-S6-10 / Backend QA |
| 067 | `tests/test_migration_preparation.py::test_preparation_api_roles_preview_apply_items_and_rollback`; planned: `tests/test_migration_preparation_contract.py::test_post_audit_preview_apply_rollback_schemas_dedup_and_replay` | API | role/transition | exact 202/200/flags | EXISTING basic; MISSING — GAP-S6-09 / Backend+Frontend QA |
| 068 | planned: `tests/test_stage6_cli_contract.py::test_scan_preview_apply_status_verify_and_rollback_success_json` | CLI | positive | six exact commands/exit 0 | MISSING — GAP-S6-10 / Backend+Release QA |
| 069 | planned: `tests/test_stage6_cli_contract.py::test_cli_argument_and_domain_errors_are_sanitized_json_exit_2` | CLI | negative | JSON/exit 2/no leak | MISSING — GAP-S6-10 / Backend QA |
| 070 | `tests/e2e/test_source_data.py::test_cms_20_verify_fails_closed_on_post_apply_violations`; planned: `tests/test_stage6_semantic_verify.py::test_verify_accepts_only_fresh_clean_or_fully_mapped_editorial_result` | source/CLI/server | characterization/positive | fail fingerprint; mapped editorial pass | EXISTING — strict expected XFAIL; MISSING — GAP-S6-01 / Backend |
| 071 | planned: `tests/e2e/test_stage6_contract.py::test_api_error_envelopes_are_safe_and_ui_recovers` | PW/API | negative/recovery | actionable sanitized UI | MISSING — GAP-S6-10 / Frontend QA |
| 072 | planned: `tests/test_stage6_contract.py::test_internal_errors_are_sanitized_and_correlated` | server/API/CLI | security-negative | no path/secret | MISSING — GAP-S6-10 / Backend QA |
| 073 | `tests/e2e/test_source_data.py::test_source_database_is_copied_and_migrated_without_mutation`; `tests/test_migrations.py::test_accidental_publication_is_reverted_and_other_content_is_untouched`; planned: `tests/e2e/test_source_data.py::test_stage6_exact_phase_fingerprints_and_counts` | source/server | transition/invariant | 1756→1757/raw identity | EXISTING partial; MISSING — GAP-S6-03 / Migration QA |
| 074 | `tests/e2e/test_source_data.py::test_stage6_real_source_automatic_preparation`; `tests/e2e/test_source_data.py::test_cms_19_auto_ready_is_not_semantically_clean`; `tests/e2e/test_source_data.py::test_cms_20_verify_fails_closed_on_post_apply_violations`; planned: `tests/e2e/test_source_data.py::test_stage6_real_source_semantic_closure` | source | E2E characterization/closure | mapped technically clean set | EXISTING happy + strict expected XFAIL; MISSING — GAP-S6-11 / Source QA |
| 075 | `tests/e2e/test_source_data.py::test_source_database_is_copied_and_migrated_without_mutation`; planned: `tests/e2e/test_source_data.py::test_stage6_evidence_artifact_contains_immutable_identity_and_run_ids` | source | evidence | IDs/SHA/timestamp/counts | EXISTING immutability; MISSING — GAP-S6-11 / Source QA |
| 076 | planned: `tests/test_stage6_rollout_contract.py::test_stage12_restore_drill_precedes_production_mutation` | rollout | transition | restore before production | MISSING — GAP-S6-11 / Release QA |
| 077 | planned: `tests/test_stage6_rollout_contract.py::test_stage12_stops_on_semantic_or_restore_failure` | rollout | negative | production untouched | MISSING — GAP-S6-11 / Release QA |
| 078 | planned: `tests/test_stage6_rollout_contract.py::test_stage12_success_records_all_invariants_and_rollback_identity` | rollout | positive/invariant | all gates/evidence | MISSING — GAP-S6-11 / Release QA |

### Gap coverage

| Gap | Existing characterization/baseline node | Planned positive closure node | Status |
|---|---|---|---|
| GAP-S6-01 | `tests/e2e/test_source_data.py::test_cms_20_verify_fails_closed_on_post_apply_violations` | `tests/test_stage6_semantic_verify.py::test_verify_accepts_only_fresh_clean_or_fully_mapped_editorial_result` | EXISTING — strict expected XFAIL; MISSING — Backend |
| GAP-S6-02 | `tests/e2e/test_source_data.py::test_cms_19_auto_ready_is_not_semantically_clean` | `tests/test_stage6_data_rules.py::test_final_generated_tree_is_nfkc` | EXISTING — strict expected XFAIL; MISSING — Data rules |
| GAP-S6-03 | `tests/e2e/test_source_data.py::test_source_database_is_copied_and_migrated_without_mutation`; `tests/test_migrations.py::test_accidental_publication_is_reverted_and_other_content_is_untouched` | `tests/e2e/test_source_data.py::test_stage6_exact_phase_fingerprints_and_counts` | EXISTING partial; MISSING — Migration QA |
| GAP-S6-04 | `tests/e2e/test_source_data.py::test_cms_19_auto_ready_is_not_semantically_clean`; `tests/e2e/test_source_data.py::test_cms_20_verify_fails_closed_on_post_apply_violations` | `tests/test_stage6_classification.py::test_every_known_and_unknown_audit_code_is_fail_closed` | EXISTING — strict expected XFAIL; MISSING — Backend+Data rules |
| GAP-S6-05 | `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow` | `tests/e2e/test_stage6_dossier.py::test_every_reason_shows_code_field_and_message` | EXISTING minimal dossier; MISSING — Frontend+UX QA |
| GAP-S6-06 | `tests/e2e/test_source_data.py::test_stage6_real_source_automatic_preparation` | `tests/test_stage6_field_rules.py::test_url_rule_positive_ambiguous_and_noop` | EXISTING flow only; MISSING — Migration+Data QA |
| GAP-S6-07 | `tests/test_migration_preparation.py::test_leaflet_pdf_cover_is_generated_and_registered` | `tests/test_stage6_generated_media.py::test_rollback_terminal_inflight_restart_shared_and_orphan_replay` | EXISTING generation only; MISSING — Media+Backend QA |
| GAP-S6-08 | `tests/e2e/test_admin_migration.py::test_migration_dashboard_and_audit` | `tests/test_stage6_contract.py::test_active_audit_blocks_preview_and_duplicate_reuses_id` | EXISTING basic audit; MISSING — Backend+Frontend QA |
| GAP-S6-09 | `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow`; `tests/test_migration_preparation.py::test_preparation_api_roles_preview_apply_items_and_rollback` | `tests/test_migration_preparation_contract.py::test_only_latest_completed_apply_is_rollback_target` | EXISTING basic transitions; MISSING — Backend+Frontend QA |
| GAP-S6-10 | `tests/test_migration_preparation.py::test_preview_apply_is_idempotent_and_rollback_is_compensating`; `tests/e2e/test_admin_migration.py::test_stage6_automatic_preparation_workflow` | `tests/test_stage6_contract.py::test_worker_fifo_and_duplicate_active_identity` | EXISTING baseline; MISSING — QA/component owners |
| GAP-S6-11 | `tests/e2e/test_source_data.py::test_stage6_real_source_automatic_preparation`; `tests/e2e/test_source_data.py::test_cms_20_verify_fails_closed_on_post_apply_violations` | `tests/test_stage6_rollout_contract.py::test_stage12_stops_on_semantic_or_restore_failure` | EXISTING flow + strict expected XFAIL; MISSING — Source+Release QA |
| GAP-S6-12 | `tests/e2e/test_regression.py::test_coverage_contract` | `tests/test_stage6_docs_contract.py::test_plan_report_guide_registry_and_coverage_are_consistent` | EXISTING general contract; MISSING — Documentation |

## 14. Финальный self-check для review cycle 3

- Структура: **78** уникальных FR definitions; **12** Blocker gaps; **78** FR mappings; **12** gap mappings.
- Trace: 40 FR mappings содержат фактические `EXISTING` nodes, все их пути и test functions проверены в репозитории; 64 mappings содержат явно отделённые `MISSING` branches с Blocker gap и owner. Два уникальных `CMS-19/CMS-20` nodes отмечены strict expected XFAIL; positive closure tests не выданы за существующие.
- External policy: canonical URL identity, `checked_at/final_url/result`, production TTL 24h и fail-closed `technical_blocker` заданы; editorial разрешён только после фактически выполненной проверки.
- API/CLI: полные GET/POST resource, request, success, error и exit schemas заданы; duplicate-active имеет единственный outcome; viewer verify capability отсутствует.
- Audit: роль, видимость/enabled/disabled reason, duplicate, polling stop, `queued→running→completed/failed`, restart/requeue, retry с новым ID и точная команда `python -m server.migration_acceptance scan` заданы.
- Rollback/media: terminal replay, in-flight duplicate, restart, before/after commit, retained cache и append-only per-apply/item/media/digest provenance непротиворечивы.
- Evidence: raw DB/media identity, два artifact paths, run IDs и SHA-256 зафиксированы; числа дефекта остаются evidence, не targets.
- Verify: ненулевые fully mapped editorial blocker/warning допустимы; auto-ready, technical, unknown, unmapped и violations обязаны быть нулевыми.
- Все C-01—C-06, M-01—M-06, N-01—N-02 закрыты на уровне текста; все восемь findings cycle 2 отражены в журнале.
- `ACCEPT` требований явно отделён от готовности реализации и допуска к этапу 7.
- Ни одна техническая, программная, интерфейсная или тестовая задача не передана человеку; человек привлекается только после gates для содержательного решения по конкретному `editorial_review` материалу.
