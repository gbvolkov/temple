# Финальное заключение по функциональной приёмке этапа 6

Дата: 30 июля 2026 года.  
Объект: функциональность автоматической подготовки мигрированных материалов и подготовленные source-data.  
Основание: `outputs/cms-stage6-functional-requirements.md`, версия 3.0.  
Лимит согласования: использованы ровно 3 цикла.

## 1. Итоговое решение

Приняты два независимых решения:

1. **Детальные функциональные требования: ACCEPT.** Версия 3.0 является финально согласованным нормативным контрактом этапа 6.
2. **Фактический допуск реализации и данных к этапу 7: НЕТ.**

Этап 7 нельзя начинать из-за двух открытых технических P0-дефектов `CMS-19`, `CMS-20` и двенадцати Blocker gaps `GAP-S6-01`—`GAP-S6-12`.

Обязательное участие человека сейчас **не требуется и не допускается как способ обхода блокеров**. Все обнаруженные проблемы относятся к реализации, данным, интерфейсу, автоматическим проверкам или rollout. Человек привлекается только после прохождения технического gate и только для решения по содержанию конкретных материалов `editorial_review`.

## 2. Результат трёх циклов согласования

| Цикл | Объект | Решение | Основной результат |
|---|---|---|---|
| 1 | Первичный draft | `REWORK` | Выявлены неверный фазовый baseline, слабый semantic verify, fail-open classification, неполное досье, неопределённый rollback generated media и недостаточная trace-матрица |
| 2 | Версия 2.0 | `REWORK` | Найдены передача технической external-проверки человеку, неверные test traces, неполные API/CLI и audit lifecycle, конфликт retry rollback |
| 3 | Версия 3.0 | `ACCEPT` требований; допуск `НЕТ` | Закрыты 14/14 замечаний цикла 1 и 8/8 findings цикла 2; 78/78 FR и 12/12 gap mappings согласованы |

Финальное строгое заключение team lead: `outputs/cms-stage6-system-analysis-review-cycle-3.md`.

## 3. Выполненные тестовые контуры

Тестирование выполнялось на реальном Chromium через Playwright, серверными pytest-тестами и source-data сценарием на изолированной копии. Raw БД и медиатека использовались только для чтения и fingerprint-сверки.

| Контур | Результат | Артефакт |
|---|---|---|
| Server pytest | 90/90, ошибок нет | свежий запуск 30.07.2026 |
| Playwright smoke | 3/3, `XFAIL=0`, contract OK | `output/playwright/cms-regression/20260730T134458-765507Z` |
| Playwright full №1 | 42/42, `XFAIL=0`, 334 assertions, contract OK | `output/playwright/cms-regression/20260730T134511-428742Z` |
| Playwright full №2 | 42/42, `XFAIL=0`, 334 assertions, contract OK | `output/playwright/cms-regression/20260730T134734-459187Z` |
| Source-data | 7 collected: 4 passed, 3 strict expected XFAIL, failure/error 0, contract OK | `output/playwright/cms-regression/20260730T134150-771044Z` |
| Сохранность raw source | `source_unchanged=true`; before/after hashes совпадают | тот же source-data artifact |

Три ожидаемых source-data `XFAIL`:

- `GAP-01` — содержательная приёмка архива ещё не завершена;
- `CMS-19` — `auto_ready` содержит технически ненормализованные значения;
- `CMS-20` — preparation verify ложно подтверждает семантически несогласованный набор.

Зелёные smoke/full контуры подтверждают стабильность уже автоматизированного поведения. Они не закрывают нормативные ветки, отмеченные `MISSING` в матрице требований. Из 78 FR в 40 mappings есть существующие проверки; 64 mappings одновременно содержат ещё не реализованные planned branches. Все 16 уникальных node ID, названных `EXISTING`, проверены team lead и существуют в репозитории.

## 4. Проверенные пользовательские действия и workflow

### 4.1. UI и роли

Подтверждено:

- viewer/editor видят read-only migration dashboard, но не mutation-кнопки;
- publisher может запускать аудит и preview, но не apply/rollback;
- admin видит и использует apply/rollback;
- прямые запрещённые API-вызовы дают `403`;
- блок автоматической подготовки доступен на desktop и mobile.

Требуют реализации и отдельной регрессии:

- все состояния доступности кнопок при active/failed/stale runs;
- duplicate click/API identity;
- восстановление после рестарта;
- полные safe error и focus/a11y transitions.

### 4.2. «Запустить аудит»

Существующий happy path проверен: запуск создаётся, dashboard обновляет состояние и метрики.

Не доказаны текущей положительной регрессией и потому остаются Blocker:

- matching duplicate без новой row;
- конфликт другого active scope;
- `queued → running → completed/failed`;
- остановка polling;
- restart/requeue того же run;
- retry failed run с новым ID;
- единый UI/API/CLI contract.

Нормативное поведение полностью определено в требованиях, включая точную CLI-команду `python -m server.migration_acceptance scan`.

### 4.3. «Сформировать preview»

Playwright подтвердил:

- нажатие кнопки и HTTP queue;
- переход `preview · queued → completed`;
- четыре metric cards;
- сохранение полного набора preparation items;
- фильтр `auto_ready | editorial_review | technical_blocker`;
- переходы следующая/предыдущая страница.

Server tests подтверждают детерминированные transformations, stale-version отказ без частичных изменений и базовую идемпотентность.

Не закрыты freshness race, duplicate-active identity, полный hash/version state matrix и все field-rule oracles URL/slug/type/section/relations.

### 4.4. «Открыть досье» и «К dashboard»

Playwright подтвердил открытие досье, две карточки «До/После» и возврат на dashboard.

Фактический CJM-дефект: текущий UI не показывает полный контекст решения — скрыты `code`, `field_path` и `consequence`; одна строка message/label недостаточна для осознанного редакционного выбора. Также не доказаны long text, несколько причин, mobile overflow, keyboard/focus и screen-reader сценарии.

Поэтому `GAP-S6-05` остаётся Blocker, а редакционные материалы человеку не передаются.

### 4.5. «Применить preview»

Playwright подтвердил:

- открытие confirm;
- отмена без HTTP-запроса;
- подтверждение и завершение apply;
- сохранение workflow-статусов и `migration_review_required`;
- отсутствие публикации.

Server tests подтвердили stale preview rejection, отсутствие частичных content/revision изменений, повторное применение того же preview и создание системных ревизий.

Не закрыты semantic prewrite gate, все invalid states, stale hash, fault injection DB/file, public/FTS/media invariants и полный API/CLI contract.

### 4.6. «Откатить применение»

Playwright и server tests подтвердили основной compensating rollback и конфликт при ручном изменении после apply без частичного отката.

Не доказаны:

- only-latest target для всех UI/API/CLI;
- terminal replay;
- concurrent/in-flight duplicate;
- restart до/после semantic commit;
- shared/generated/orphan media;
- append-only provenance и retained-cache recovery.

Политика требований определена однозначно: generated cache системный и immutable, rollback не удаляет media row/file, а каждое повторное использование добавляет provenance event.

### 4.7. External links

Выявленная во втором review опасная ветка устранена из требований:

- `check_external=false`, отсутствующий, просроченный или inconclusive cache — технический blocker и провал verify;
- редактору можно передать только содержательный выбор после реально выполненной проверки, доказавшей недоступность ссылки;
- production TTL evidence — 24 часа.

Текущая реализация этого fail-closed контракта ещё не доказана.

## 5. Результаты проверки подготовленных данных

### 5.1. Фазовый baseline

- Raw source: `contents=1 757`, `migration_review_required=1` у `1 756`.
- Post-schema copy: `migration_review_required=1` у `1 757`.
- Изменение объяснено migration `server/migrations.py::revert_accidental_publication`.
- Конкретный content ID: `660f8f7c-1183-464d-b39c-f4df2579fd45`.
- Raw запись: `published`, version 7, flag 0; post-schema ожидается `draft`, version 8, flag 1.
- Общее число материалов не меняется.

### 5.2. Preparation result

На post-schema копии:

- `contents=1 757`;
- `changed=1 757`;
- `auto_ready=496`;
- `editorial_review=1 261`;
- `technical_blocker=0`.

### 5.3. Post-apply audit

Актуальный post-apply audit обнаружил:

- `590 blocker`;
- `1 016 warning`;
- blocker: `fallback_date_2000=561`, `required_field_missing=28`, `legacy_navigation_text=1`;
- warning: `unicode_not_nfkc=486`, `duplicate_title=476`, `missing_legacy_media=36`, `legacy_text=18`.

У `197` материалов, классифицированных `auto_ready`, остаётся `unicode_not_nfkc`. Фактически чистыми являются только 299 из 496 записей `auto_ready`.

Текущий `verify_preparation` при этом возвращает `ok=true`, потому что проверяет foreign keys, отсутствие непокрытых items и сохранённый класс `technical_blocker`, но не сопоставляет актуальный post-audit с текущими versions и preparation classifications.

### 5.4. Сохранность источника

- Raw SQLite SHA-256: `1E1FAE02D1350E5C7F0B23935474F76B9AAE93ED8AB4EE41BBAC5579ECC024EF`.
- Размер raw SQLite: `62 668 800` bytes.
- Media inventory SHA-256: `69fe0173a626ca62c4509001297d64269f44eb49a5a685b1fa09c77984aec243`.
- `source-before.json` и `source-after.json` имеют одинаковый SHA-256: `D1361E4C510AC1AE52421F9F6E3485E8D5D814E4180B648F17E522A8537E2CFE`.
- SQLite quick check: `ok`; foreign key errors: 0.

### 5.5. Run identity

- pre-audit: `4a29bd77-494e-44e4-8858-5c7ebe27a22c`;
- preview: `b315e46a-3913-453a-a561-a88adc41ce77`;
- apply: `ba54efd5-9767-491d-b854-cf0740f58426`;
- post-audit: `6266abfc-a0e8-4d8b-acaa-8b524a682b8a`.

## 6. Зарегистрированные новые дефекты

### CMS-19 — `auto_ready` не является технически чистым

Причина: финальная NFKC-нормализация выполняется до генерации summary; добавленный символ `…` снова создаёт `unicode_not_nfkc`.

Characterization:

`tests/e2e/test_source_data.py::test_cms_19_auto_ready_is_not_semantically_clean`

Ожидание после исправления: любой `auto_ready` имеет ровно 0 открытых blocker/warning; отдельно `unicode_not_nfkc=0`.

### CMS-20 — semantic verify работает fail-open

Причина: `verify_preparation` не требует свежий post-apply full audit и не сверяет issue, current version, preparation item и classification.

Characterization:

`tests/e2e/test_source_data.py::test_cms_20_verify_fails_closed_on_post_apply_violations`

Ожидание после исправления: отсутствующий, active, failed, stale или несогласованный post-audit даёт `ok=false`, CLI exit 2 и останавливает rollout.

Оба дефекта внесены в `tests/e2e/defects.json` как open P0 и в `outputs/cms-current-shortcomings.md`.

## 7. CJM и удобство использования

Текущий основной путь понятен: аудит → preview → метрики/список → досье → apply → при необходимости rollback. Фильтр, пагинация, back action, confirm/cancel и mobile visibility работают в проверенном happy path.

Однако путь ещё небезопасен для редактора:

- метрика `technical_blocker=0` создаёт ложное ощущение готовности;
- `verify=ok` не означает семантическую чистоту набора;
- досье скрывает технический код, поле и последствия вариантов;
- failed/active/stale run recovery не имеет полного проверенного UI contract;
- rollback generated media и crash recovery не доказаны;
- skipped external check ранее мог быть ошибочно передан человеку.

Итог CJM: технический оператор может исследовать подготовку на копии, но редактор не должен начинать принятие материалов до закрытия gate.

## 8. Обязательное участие человека

До этапа 7 обязательное вмешательство человека: **нет**.

После закрытия всех технических условий человек нужен только для решения по содержанию конкретного `editorial_review` материала, например:

- выбрать содержательно правильную версию дубля;
- выбрать замену доказанно отсутствующему изображению;
- подтвердить спорную дату, заголовок, раздел или тип;
- решить судьбу неполного исторического материала;
- заменить, удалить или осознанно оставить уже проверенную недоступную внешнюю ссылку.

Человеку нельзя передавать Unicode/encoding, schema, URL/slug mechanics, missing technical check, broken structural links, cache, crash/recovery, interface или test gaps.

## 9. Условия допуска к этапу 7

Решение может измениться на `ДА` только после одновременного выполнения:

1. `CMS-19` и `CMS-20` исправлены; strict XFAIL превращены в обычные passing regression без ложного XPASS.
2. Закрыты реализацией и тестами все `GAP-S6-01`—`GAP-S6-12`.
3. Fresh full post-apply audit относится к текущим versions.
4. Fail-closed semantic verify даёт `ok=true`, exit 0, `violations=0`.
5. У `auto_ready` ноль blocker/warning; technical/unknown/unmapped остаток равен нулю.
6. Каждый editorial issue полностью mapped и видим в досье.
7. Пройдены generated-media, rollback, crash/recovery, public, FTS, media, revision, audit и DB invariants.
8. Повторно пройдены server, Playwright smoke, два full regression и source-data контуры.
9. Каждый оставшийся `XFAIL` сверён с реестром; необъяснённых FAIL/ERROR/XPASS/XFAIL нет.
10. Raw SQLite и медиатека повторно доказаны неизменными.
11. Restore drill и fail-fast production rollout воспроизводимы.
12. План, полный отчёт, руководство, описание решения, shortcomings, defects registry и coverage синхронизированы с фактом.

## 10. Финальный вывод

Функциональные требования подготовлены, трижды строго согласованы и приняты. Они достаточно полны для последующей имплементации и автоматизации: фиксируют все роли, кнопки, переходы, states, UI/API/CLI операции, errors, retry/recovery, данные, media и rollout.

Текущая реализация и подготовленные данные требованиям полностью не соответствуют. Начало этапа 7 сейчас является нарушением канонического плана. Следующая допустимая работа — продолжение этапа 6: исправление `CMS-19/CMS-20`, реализация Blocker gaps и повторная автоматическая приёмка без участия редактора.
