# Ревью системного анализа этапа 6 — цикл 2

Дата ревью: 30 июля 2026 года.  
Объект: `outputs/cms-stage6-functional-requirements.md`, версия 2.0.  
Предыдущее решение: `outputs/cms-stage6-system-analysis-review-cycle-1.md`.  
Роль: team lead системных аналитиков.  
Метод: построчная сверка требований с 14 замечаниями цикла 1, каноническим планом, текущим кодом, реестром дефектов, тестами и свежими JUnit/JSON evidence.

## 1. Два независимых решения

### A. Качество и приемлемость требований

**VERDICT A: REWORK**

Документ существенно улучшен и уже правильно отделяет нормативный MUST от AS-IS и GAP. Однако финально согласованным нормативным контрактом он пока не является: одно правило прямо перекладывает непроведённую техническую проверку на человека; coverage matrix фактически неверно помечает уже существующие characterization-тесты `CMS-19/CMS-20` как `MISSING`; API/CLI и audit user flow остаются недоопределёнными; rollback содержит противоречивые ветки повтора.

`BLOCK` не выставляется: вся информация и полномочия для последней редакции требований имеются, внешнего или содержательного решения человека не требуется.

### B. Фактический допуск реализации и данных к этапу 7

**VERDICT B: НЕТ**

Причины достаточны каждая по отдельности:

- `CMS-19` и `CMS-20` имеют статус `open`, приоритет P0 и воспроизводятся строгими ожидаемыми `XFAIL`;
- текущий `verify` возвращает `ok=true` при post-apply audit `590 blocker / 1 016 warning`;
- у `197 auto_ready` остаётся `unicode_not_nfkc` (`486` таких issue в целом);
- все `GAP-S6-01`—`GAP-S6-12` в требованиях всё ещё Blocker;
- обязательные dossier, freshness, classification, generated-media, crash/recovery, source/rollout и error-contract тесты ещё обозначены как `MISSING`;
- канонический план и часть отчётных документов всё ещё преждевременно называют этап 6 выполненным.

Зелёные общие контуры не отменяют точечные P0 `XFAIL` и непокрытые MUST.

## 2. Проверенное фактическое evidence

| Источник | Подтверждённый факт | Значение для решений |
|---|---|---|
| `output/playwright/cms-regression/20260730T134150-771044Z/summary.json` | source-data: 7 tests, 0 failure/error, 3 expected XFAIL, `contract_ok=true`, `source_unchanged=true` | Контур честен, но не даёт допуска |
| JUnit того же прогона | XFAIL ровно `GAP-01`, `CMS-19`, `CMS-20` | Два технических P0 остаются открыты |
| `source-before.json` / `source-after.json` | DB и media fingerprints совпадают; raw `review_required=1756`, `contents=1757` | Raw источник не изменён |
| `20260730T134458-765507Z/summary.json` | smoke 3/3 | Общий smoke зелёный |
| `20260730T134511-428742Z` и `20260730T134734-459187Z` | два full 42/42, 334 assertions, 0 XFAIL | Существующая full-регрессия стабильна |
| Свежий серверный прогон | green | Базовый серверный контур стабилен |
| `tests/e2e/defects.json` | `CMS-19`, `CMS-20` зарегистрированы как open P0 с реальными node ID | Требуется точный cross-reference в требованиях |
| `server/migration_preparation.py::_summary` / `propose_content` | NFKC выполняется до добавления `summary` с `…` | Причина `CMS-19` подтверждается кодом |
| `server/migration_preparation.py::verify_preparation` | Проверяются только FK, отсутствие item и сохранённый `technical_blocker` | Причина `CMS-20` подтверждается кодом |
| `site/cms.js::renderPreparationDossier` | reason показывает только message; option — только label; field/code/consequence скрыты | `GAP-S6-05` фактически открыт |
| `scripts/production_apply_stage4.sh`, stage 12 | После apply вызывается слабый verify без нового full post-audit | `GAP-S6-11` и production block подтверждены |

## 3. Closure table замечаний цикла 1

Статус ниже относится к качеству **текста требований**. `CLOSED` не означает, что реализация уже соответствует MUST.

| Review ID | Статус | Результат сверки |
|---|---|---|
| C-01 | **CLOSED** | §3 и `FR-S6-073` корректно разделяют `1756 raw → 1757 post-schema`, называют `revert_accidental_publication`, content ID и raw immutability; `GAP-S6-03` честно оставлен на автодоказательство |
| C-02 | **CLOSED в требованиях** | `FR-S6-017`, `FR-S6-070`, §10.1 задают freshness, version/item/issue mapping, `ok=false`, violations и CLI exit 2; реализация остаётся открытым `CMS-20/GAP-S6-01/04` |
| C-03 | **CLOSED в требованиях** | §6 содержит versioned исчерпывающую карту текущих audit codes, technical priority и unknown fail-closed; реализация остаётся `GAP-S6-04` |
| C-04 | **CLOSED в требованиях** | `FR-S6-044`—`048` задают видимые code/field/message/recommendation/consequence, long/mobile/a11y; реализация остаётся `GAP-S6-05` |
| C-05 | **PARTIAL** | Retained-cache policy выбрана и crash phases описаны, но `FR-S6-060` и `FR-S6-065` противоречат друг другу по повторному rollback; provenance повторно используемого cache asset не завершён |
| C-06 | **REOPENED** | §13 существует, но содержит фактически неверные статусы `MISSING` для уже существующих `CMS-19/CMS-20` characterization nodes и нестабильные ссылки `same`, `existing MP happy`, `PW dossier assertion` |
| M-01 | **CLOSED** | `FR-S6-028`—`034` разделяют URL, slug, type, section, relations и no-op evidence |
| M-02 | **PARTIAL** | Freshness predicate задан, но `FR-S6-015` ошибочно превращает отсутствие технической external-проверки в editorial reason |
| M-03 | **CLOSED в требованиях** | §8 и `FR-S6-052`—`058` задают file phases, semantic commit point, replay и conflict |
| M-04 | **CLOSED в требованиях** | §5.2, `FR-S6-018`, `FR-S6-049`, `FR-S6-061` различают display/actionable/rollback targets и recovery CJM |
| M-05 | **PARTIAL** | Latest rollback target унифицирован, но terminal repeat и interrupted retry не разведены непротиворечиво между `FR-S6-060/065` |
| M-06 | **REOPENED** | §10 полностью задаёт только verify и общий error envelope; success schemas остальных API/CLI, duplicate-active outcome и способ viewer читать verify result отсутствуют |
| N-01 | **REOPENED** | Заявление о стабильных node IDs не соответствует строкам §13 и свежему реестру `CMS-19/CMS-20` |
| N-02 | **PARTIAL** | Дата/phase/rules присутствуют, но §3.2 прямо признаёт отсутствие run IDs; database identity и ссылка на воспроизводимый evidence artifact не зафиксированы |

Итог closure: 7 замечаний закрыты на уровне требований, 4 закрыты частично, 3 переоткрыты.

## 4. Новые findings цикла 2

### S2-C-01 — Critical. Непроведённая external-проверка передаётся человеку

Ссылки: `FR-S6-015`, §6 `external_link_unavailable`, каноническое правило «технические решения не перекладываются на человека».

`check_external=false` — технический режим запуска, а не содержательная неоднозначность материала. Причина `external_check_skipped` не может переводить материал в `editorial_review`: редактору фактически предлагается компенсировать непроведённую машинную проверку.

Обязательное исправление: до получения актуального online/cached evidence материал должен быть `technical_blocker`, а verify — fail. В `editorial_review` допустим только результат уже выполненной проверки, где ссылка доказанно недоступна и требуется содержательное решение «заменить/удалить/оставить». Задать cache identity/TTL и production policy.

### S2-M-01 — Major. Factual trace расходится с реестром и свежим JUnit

Ссылки: §3.2, §13 строки `FR-S6-024`, `FR-S6-039`, `FR-S6-070`, Gap coverage 01/02/04, self-check §14.

Фактические characterization node ID уже существуют:

- `tests/e2e/test_source_data.py::test_cms_19_auto_ready_is_not_semantically_clean`;
- `tests/e2e/test_source_data.py::test_cms_20_verify_fails_closed_on_post_apply_violations`.

Они ожидаемо `XFAIL`, но матрица называет придуманные `CHAR-S6-UNICODE-197`, `CHAR-S6-AUTOREADY-197`, `CHAR-S6-VERIFY-590-1016-197` и помечает characterization как `MISSING`.

Обязательное исправление: использовать реальные node ID со статусом `EXISTING — strict expected XFAIL`; отдельно оставить `MISSING` только для положительных closure tests. Добавить crosswalk `CMS-19 ↔ GAP-S6-02/04`, `CMS-20 ↔ GAP-S6-01/04`. Не переименовывать фактический дефект аналитическим gap ID.

### S2-M-02 — Major. API/CLI machine contract не завершён

Ссылки: таблица ролей §4, `FR-S6-066`—`072`, §10.2.

Недостатки:

- нет полных request/success schemas для GET migration/run/items и POST preview/apply/rollback;
- нет success/error schemas команд `preview/apply/status/rollback`;
- `FR-S6-067` оставляет альтернативу «active ID/409», то есть не выбирает один outcome;
- viewer якобы может `Read result` semantic verify, но не задан ни endpoint, ни поле dashboard/status, ни UI-сценарий;
- не определено, какие codes относятся к FastAPI validation до доменного handler.

Обязательное исправление: выбрать единственный status/outcome для каждой ветки, привести endpoint/command, request, response, HTTP/exit, side effects и recovery. Либо определить read-only verify endpoint/UI, либо удалить неподтверждённую viewer capability.

### S2-M-03 — Major. Кнопка и lifecycle полного audit покрыты не полностью

Ссылки: `FR-S6-009`—`017`, таблица ролей §4, state model §5.

Для `Запустить аудит` отсутствуют:

- однозначное поведение повторного клика/POST при queued/running audit;
- enabled/disabled UI rule и причина;
- непрерывное наблюдение queued→running→completed/failed, остановка polling;
- restart/retry contract именно audit run;
- точная CLI-команда, хотя role table обещает `CLI audit`.

Обязательное исправление: добавить audit state/control matrix и реальные UI/API/CLI scenarios. Команду назвать фактически (`server.migration_acceptance scan` либо иной выбранный контракт), не вводить несуществующий псевдоним.

### S2-M-04 — Major. Повторный rollback имеет два несовместимых результата

Ссылки: `FR-S6-060`, `FR-S6-064`, `FR-S6-065`, §8.

`FR-S6-060` требует `409` для already rolled_back, а `FR-S6-065` обещает идемпотентное продолжение при повторе. Нужно развести:

- повтор запроса после terminal `rolled_back`;
- retry того же operation после неизвестного клиенту ответа;
- restart во время незавершённой компенсации;
- retry до/после semantic commit.

Для cache reuse также нужно определить append-only provenance нескольких apply/item IDs: один набор IDs в перезаписываемом metadata не является полным журналом.

### S2-M-05 — Major. Timestamped evidence не имеет воспроизводимой identity

Ссылки: §3.2, `FR-S6-075`, N-02.

В тексте есть дата и rules version, но нет database fingerprint, schema/migration fingerprint, audit/preview/apply IDs и ссылки на JSON evidence. Формулировка «должны быть добавлены» честна как gap, но не закрывает N-02 и не позволяет независимо воспроизвести конкретный снимок.

Обязательное исправление: сослаться на сохранённый артефакт и привести его immutable identity; если текущий source runner ещё не сохраняет run IDs, явно назвать это `MISSING` и не утверждать N-02 закрытым в журнале изменений.

### S2-N-01 — Minor. Success example semantic verify может трактоваться как глобальный ноль issue

Ссылки: §10.1 success JSON, критерии допуска 12.6—12.7.

Редакционные blocker/warning могут законно оставаться, если они полностью сопоставлены с `editorial_review`. Пример с `open_blocker=0/open_warning=0` не объясняет это и конфликтует по смыслу с editorial-only queue.

Исправление: явно определить, что эти counts могут быть ненулевыми при `ok=true`; добавить `open_editorial_blocker/open_editorial_warning` либо привести success example с ненулевым, полностью mapped editorial остатком.

### S2-N-02 — Minor. Coverage matrix использует неисполняемые ссылки

Ссылки: §13 строки 004, 025, 027, 036, 037, 041, 043, 049, 062, 063.

`same`, `existing MP happy`, `MP transformations test`, `PW dossier assertion` и подобные значения не являются node ID. Они не выдерживают автоматическую проверку существования, хотя §7 и §14 заявляют обратное.

Исправление: каждую ссылку заменить полным существующим pytest node ID; непроверенные branches пометить `MISSING` с gap/owner.

## 5. Полнота пользовательских и системных сценариев

| Область | Оценка требований | Комментарий |
|---|---|---|
| Роли UI/API | В основном достаточно | Viewer/editor/publisher/admin разделены; verify read capability требует endpoint |
| Dashboard/history/metrics | Достаточно после MISSING tests | Empty/active/failed/history/mobile заданы |
| Full audit | **Недостаточно** | Нет полного control/state/retry/CLI contract |
| Preview | В основном достаточно | Freshness и determinism заданы; duplicate-active outcome уточнить |
| Data rules/classification | Достаточно, кроме `FR-S6-015` | Unknown fail-closed; external skipped нельзя отдавать человеку |
| Filter/pages/dossier/back | Достаточно нормативно | Все основные кнопки, boundaries, mobile и a11y перечислены |
| Apply/confirm/stale/idempotency | Достаточно нормативно | Negative paths есть; machine schemas неполны |
| Rollback/confirm/conflict | **Противоречиво** | Terminal repeat и interrupted retry не разведены |
| Generated media | Политика определена | Нужен непротиворечивый repeat/provenance contract |
| API/CLI/errors | **Недостаточно** | Не все success schemas и команды заданы |
| Scheduler/restart/crash | Достаточно нормативно для preparation | Audit lifecycle дополнить |
| Source-data/rollout | Достаточно как MUST/GAP | Фактическая реализация пока не выполняет semantic post-audit gate |
| Участие человека | **Нарушено одной веткой** | `external_check_skipped` является технической работой |

## 6. Почему зелёные прогоны не дают допуск

Свежие server/smoke/full результаты подтверждают отсутствие регрессии в уже исполняемом контуре. Они не подтверждают MISSING-сценарии §13. Fresh source-data профиль тоже корректен именно потому, что ожидаемо фиксирует три открытых состояния:

1. `GAP-01` — содержательная миграционная приёмка;
2. `CMS-19` — технически нечистый `auto_ready`;
3. `CMS-20` — ложноположительный verify.

До исправления пунктов 2–3 запуск человека по пункту 1 нарушает каноническую последовательность. `source_unchanged=true` закрывает только инвариант сохранности raw-источника.

## 7. Обязательный пакет правок для цикла 3

Цикл 3 является последним возможным циклом согласования. Для него обязателен один согласованный revision документа:

1. Исправить `FR-S6-015`: skipped external check → technical blocker/fail; editorial только после доказанного результата проверки.
2. Ввести crosswalk реестра: `CMS-19/CMS-20` к соответствующим `GAP-S6`; использовать реальные characterization node ID и статус expected XFAIL.
3. Полностью очистить §13 от `same`, shorthand и ложных `MISSING/EXISTING`; каждый existing branch должен иметь точный исполняемый node ID.
4. Разделить для `CMS-19/CMS-20` существующий characterization test и отсутствующий positive closure test.
5. Дописать endpoint-level и command-level API/CLI schemas; выбрать один duplicate-active outcome.
6. Определить способ чтения semantic verify viewer-ом либо удалить capability.
7. Добавить полный lifecycle кнопки/API/CLI полного audit: duplicate, polling, failed, restart, retry, errors.
8. Устранить конфликт `FR-S6-060/065`; формально задать terminal repeat, in-flight retry, restart и commit point rollback.
9. Определить append-only provenance при повторном использовании generated cache asset.
10. Привязать §3.2 к immutable evidence identity или честно оставить N-02 открытым без заявления о выполненной правке.
11. Пояснить допустимые ненулевые editorial issue counts в успешном semantic verify.
12. Пересчитать self-check и журнал изменений: выполненным считать только фактически закрытое замечание.

Изменять реализацию для получения `ACCEPT` документа не требуется: допускаются честные Blocker gaps. Требуется, чтобы сам контракт был полным, однозначным, непротиворечивым и фактически трассируемым.

## 8. Критерии решения ACCEPT в цикле 3

Требования могут получить `ACCEPT`, если:

- все 14 замечаний цикла 1 имеют `CLOSED` на уровне текста;
- все findings цикла 2 устранены без новых Critical/Major противоречий;
- каждая UI-кнопка, роль, API/CLI operation, transition, error и recovery branch имеет однозначный ожидаемый результат;
- каждый EXISTING trace указывает на реальный node ID, каждый MISSING — на Blocker gap и owner;
- реестр `CMS-19/CMS-20`, expected XFAIL и coverage matrix согласованы;
- ни одна техническая, интерфейсная, программная или тестовая задача не передаётся человеку;
- документ явно сохраняет разделение `ACCEPT требований ≠ готовность реализации`.

## 9. Условия фактического допуска к этапу 7

Даже после будущего `ACCEPT` требований реализация допускается к этапу 7 только когда одновременно:

1. `CMS-19` и `CMS-20` fixed и их strict XFAIL превращены в положительную регрессию без XPASS;
2. закрыты все `GAP-S6-01`—`GAP-S6-12`;
3. fresh post-apply full audit относится к текущим versions;
4. semantic verify даёт `ok=true`, exit 0 и ноль violations;
5. у `auto_ready` ноль blocker/warning, технических/unknown issues нет;
6. каждый editorial issue полностью mapped и видим в dossier;
7. пройдены generated-media, rollback, crash, FTS/public/media/revision/audit invariants;
8. server, smoke, два full и source-data контуры прошли без необъяснённых FAIL/ERROR/XPASS/XFAIL;
9. raw DB/media снова доказанно неизменны;
10. restore drill и fail-fast production rollout воспроизводимы;
11. план, отчёт, guide, shortcomings, registry и coverage синхронизированы с фактом.

## 10. Итог

`outputs/cms-stage6-functional-requirements.md` **не принят в цикле 2** и направлен на последний `REWORK`. Реализация и данные **не допущены к этапу 7**. Причина отказа в допуске — не честное наличие gaps само по себе, а открытые P0 технические дефекты, неполное обязательное покрытие и фактически не пройденный semantic gate.
