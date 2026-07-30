# Ревью системного анализа этапа 6 — цикл 3

Дата ревью: 30 июля 2026 года.  
Объект: `outputs/cms-stage6-functional-requirements.md`, версия 3.0.  
Предыдущие решения: `outputs/cms-stage6-system-analysis-review-cycle-1.md`, `outputs/cms-stage6-system-analysis-review-cycle-2.md`.  
Канонический gate: `outputs/cms-shortcomings-remediation-plan.md`.  
Роль: team lead системных аналитиков.  
Граница: финальная проверка полноты, однозначности, непротиворечивости, проверяемости и фактической трассируемости требований; отдельно — проверка готовности реализации и данных к этапу 7.

## 1. Два независимых решения

### A. Качество и приемлемость требований

**VERDICT A: ACCEPT**

Версия 3.0 принимается как **финально согласованный нормативный контракт этапа 6**. Все замечания циклов 1 и 2 закрыты на уровне текста: нормативное поведение отделено от AS-IS и gaps, пользовательские и машинные сценарии имеют однозначные outcomes, а существующее и отсутствующее тестовое покрытие отражено честно.

`ACCEPT` относится только к качеству требований. Он не подтверждает соответствие текущего кода или данных этому контракту и не понижает приоритет открытых gaps.

### B. Фактический допуск реализации и данных к этапу 7

**VERDICT B: НЕТ**

Реализация и данные к этапу 7 не допускаются. Достаточные независимые причины:

- `CMS-19` и `CMS-20` остаются открытыми P0-дефектами и воспроизводятся strict expected-XFAIL тестами;
- `197 auto_ready` материалов имеют `unicode_not_nfkc`;
- текущий semantic verify ошибочно возвращает `ok=true` при post-apply audit `590 blocker / 1 016 warning`;
- все `GAP-S6-01`—`GAP-S6-12` имеют приоритет Blocker и ещё не закрыты реализацией и полным покрытием;
- missing-сценарии §13 ещё не стали исполнимой положительной, негативной, ролевой и переходной регрессией;
- канонический план прямо устанавливает для этапа 7 статус `НЕ НАЧИНАТЬ`.

Общие зелёные контуры подтверждают стабильность существующего поведения, но не заменяют непройденный semantic gate и отсутствующие ветви контракта.

## 2. Проверенное фактическое evidence

| Проверка | Подтверждённый результат | Вывод |
|---|---|---|
| Структура требований | 78 уникальных FR definitions, 78 строк FR matrix, 12 Blocker gaps и 12 gap mappings | Self-check §14 арифметически согласован |
| EXISTING trace | 40 FR mappings содержат существующие проверки; все 16 уникальных фактических pytest node IDs разрешаются в репозитории | Ложных или сокращённых `EXISTING` ссылок не обнаружено |
| MISSING trace | 64 mappings содержат явно отделённые planned branches с gap и owner | Отсутствующее покрытие не выдано за существующее |
| Defect registry | `CMS-19` и `CMS-20`: `open`, P0, с теми же node IDs, что в §3.3 и §13 | Crosswalk defect ↔ gap ↔ characterization корректен |
| Strict source-data artifact | 7 tests, 0 failure/error, 3 expected XFAIL: `GAP-01`, `CMS-19`, `CMS-20`; `source_unchanged=true`, `contract_ok=true` | Контур честно фиксирует открытые дефекты, но не даёт допуск |
| Raw SQLite identity | SHA-256 `1E1FAE02D1350E5C7F0B23935474F76B9AAE93ED8AB4EE41BBAC5579ECC024EF`, размер `62 668 800` bytes | Identity §3.2 совпадает |
| Media identity | inventory SHA-256 `69fe0173a626ca62c4509001297d64269f44eb49a5a685b1fa09c77984aec243` | Identity §3.2 совпадает |
| Strict artifact hashes | `summary.json`: `C18AE1D60C3B79B1A6A56A16A413B6D899A147E902B3F98261A9BF03D6AA278E`; `source-before.json` и `source-after.json`: оба `D1361E4C510AC1AE52421F9F6E3485E8D5D814E4180B648F17E522A8537E2CFE` | Artifact и неизменность источника воспроизводимо привязаны |
| Run identity | В сохранённой SQLite-копии snapshot подтверждены audit `4a29bd77-494e-44e4-8858-5c7ebe27a22c`, preview `b315e46a-3913-453a-a561-a88adc41ce77`, apply `ba54efd5-9767-491d-b854-cf0740f58426`, post-audit `6266abfc-a0e8-4d8b-acaa-8b524a682b8a` | Run IDs §3.2 фактические, а не декларативные |
| Каноническая текущая приёмка | server 90/90; smoke 3/3; два full 42/42 без XFAIL, 334 assertion ID; source-data 7/7 с тремя ожидаемыми XFAIL; raw БД и media неизменны | Existing-контур стабилен; P0 semantic blockers остаются |

Новые тесты в рамках этого review не запускались: решение B опирается на сохранённые проверяемые артефакты и канонический статус, а не объявляет новый этап реализации завершённым.

## 3. Closure table замечаний цикла 1

Статус `CLOSED` ниже означает закрытие недостатка **в тексте требований**, а не готовность реализации.

| Review ID | Статус цикла 3 | Результат финальной сверки |
|---|---|---|
| C-01 | **CLOSED** | §3.1 и `FR-S6-073` задают `1 756 raw → 1 757 post-schema`, exact migration/content ID и raw immutability |
| C-02 | **CLOSED** | `FR-S6-017`, `FR-S6-070` и §10.4 задают freshness, version/item/issue mapping, fail-closed JSON и exit 2 |
| C-03 | **CLOSED** | §6 содержит versioned exhaustive classification, technical priority и unknown fail-closed; `auto_ready` требует нулевой остаток |
| C-04 | **CLOSED** | `FR-S6-044`—`FR-S6-048` требуют видимые code/field/message/recommendation/consequence, mobile и a11y |
| C-05 | **CLOSED** | `FR-S6-064`—`FR-S6-065` и §8 выбирают retained-cache policy, append-only provenance и единую crash/replay модель |
| C-06 | **CLOSED** | §13 даёт построчные 78 FR и 12 gap mappings; characterization и positive closure branches разведены |
| M-01 | **CLOSED** | `FR-S6-028`—`FR-S6-033` отдельно определяют URL, slug, type, section, relations и no-op evidence |
| M-02 | **CLOSED** | §§5.2, 6.1 и 10.4 задают единый freshness/external-evidence predicate для UI/API/CLI/verify |
| M-03 | **CLOSED** | §8 фиксирует DB/file phases, semantic commit point, recovery и безопасный replay |
| M-04 | **CLOSED** | §5.3 и `FR-S6-018` разводят display run, actionable preview, rollback target и recovery CJM |
| M-05 | **CLOSED** | `FR-S6-060`—`FR-S6-065` унифицируют rollback target, terminal repeat, in-flight duplicate и restart |
| M-06 | **CLOSED** | §10 задаёт resource, endpoint, CLI, success/error и exit contracts со стабильными machine codes |
| N-01 | **CLOSED** | Каждый фактический trace в §13 является полным существующим pytest node ID; planned nodes явно помечены `MISSING` |
| N-02 | **CLOSED** | §3.2 фиксирует timestamp, raw identities, artifact paths/hashes, rules version и четыре run ID |

Итог цикла 1: **14 из 14 замечаний CLOSED на уровне требований**.

## 4. Closure table findings цикла 2

| Review ID | Статус цикла 3 | Результат финальной сверки |
|---|---|---|
| S2-C-01 | **CLOSED** | `FR-S6-015` и §6.1 относят skipped/stale/inconclusive external evidence к technical blocker; editorial допустим только после подтверждённого `content_unavailable` |
| S2-M-01 | **CLOSED** | §3.3 и §13 используют реальные `CMS-19/CMS-20` nodes, связанные gaps и отдельные planned positive closure tests |
| S2-M-02 | **CLOSED** | §4 удаляет viewer verify capability; §10 задаёт API/CLI contracts и один outcome для matching active identity |
| S2-M-03 | **CLOSED** | §5.2 определяет роли, enabled/disabled states, 1,5-секундный polling, terminal stop, retry, restart и точную audit CLI-команду |
| S2-M-04 | **CLOSED** | `FR-S6-060`, `FR-S6-065` и §8 непротиворечиво разводят terminal replay, in-flight duplicate, restart и commit point; provenance append-only |
| S2-M-05 | **CLOSED** | §3.2 содержит raw/artifact identities, SHA-256 и exact run IDs; значения независимо совпали с файлами и сохранённой snapshot DB |
| S2-N-01 | **CLOSED** | §10.4 прямо допускает ненулевые, полностью mapped editorial blocker/warning при `ok=true` и требует нулевой technical/unknown/unmapped остаток |
| S2-N-02 | **CLOSED** | В §13 нет `same`, псевдо-existing или сокращённых test references; self-check пересчитан |

Итог цикла 2: **8 из 8 findings CLOSED на уровне требований**. Обязательный пакет из 12 правок выполнен; новых Critical/Major противоречий не обнаружено.

## 5. Полнота нормативного контракта

| Область | Решение по требованиям | Фактическое состояние |
|---|---|---|
| Роли, UI и доступ | Достаточно и однозначно | Часть ветвей MISSING |
| Audit lifecycle и external evidence | Достаточно, fail-closed | `GAP-S6-08` Blocker |
| Preview, data rules и classification | Достаточно, детерминированно | `CMS-19`, `GAP-S6-02/04/06/10` |
| Dossier desktop/mobile/a11y | Достаточно для содержательного решения | `GAP-S6-05` Blocker |
| Apply, atomicity и invariants | Достаточно, prewrite fail-closed | `GAP-S6-04/10` |
| Rollback и generated media | Достаточно, одна retained-cache/replay policy | `GAP-S6-07/09` |
| API/CLI/errors/verify | Достаточно для реализации и автоматической проверки | `CMS-20`, `GAP-S6-01/10` |
| Source-data и rollout | Достаточно как release gate | `GAP-S6-03/11/12` |

Таким образом, gaps являются уже не недостатками постановки, а **блокерами реализации, данных, тестов и синхронизации документации**.

## 6. Граница обязательного участия человека

Граница в версии 3.0 соответствует каноническому плану:

- до этапа 7 технические, программные, интерфейсные, миграционные и тестовые решения выполняются автоматически;
- missing/stale/inconclusive external evidence, unknown code, NFKC, structural links/media/schema, crash/recovery и verification mismatch не передаются редактору;
- человек привлекается только после прохождения технического gate и только для решения по содержанию конкретного материала `editorial_review`;
- пример допустимой человеческой задачи — выбрать содержательную замену, удалить или осознанно оставить уже проверенную недоступную внешнюю ссылку; выполнить отсутствующую техническую проверку вместо системы человек не должен.

Ни один `GAP-S6-01`—`GAP-S6-12` не требует человеческого решения по материалу.

## 7. Финальный gate этапа 7

`VERDICT A: ACCEPT` закрывает согласование требований, но **не открывает этап 7**. Для изменения решения B на `ДА` необходимо одновременно:

1. исправить `CMS-19` и `CMS-20`; их strict expected-XFAIL nodes должны стать обычными passing regressions без ложного `XPASS`;
2. закрыть реализацией и тестами все `GAP-S6-01`—`GAP-S6-12`;
3. получить fresh full post-apply audit текущих versions и semantic verify `ok=true`, exit 0, `violations=0`;
4. доказать нулевые blocker/warning у `auto_ready`, нулевой technical/unknown/unmapped остаток и полное item/reason mapping каждого editorial issue;
5. пройти generated-media, rollback, crash/recovery, public, FTS, media, revision, audit и DB invariants;
6. повторно пройти применимые server, Playwright smoke, два full regression и source-data контуры; сверить каждый оставшийся XFAIL с реестром;
7. повторно доказать неизменность raw SQLite и медиатеки;
8. воспроизвести restore drill и fail-fast production rollout;
9. синхронизировать план, отчёт, guide, shortcomings, defects registry и coverage с фактом.

До выполнения всех условий запуск редакционной проверки нарушает обязательную последовательность «функциональность и автоматическая очистка → редакционная проверка».

## 8. Итог

`outputs/cms-stage6-functional-requirements.md` версии 3.0 **принят как финально согласованный нормативный контракт этапа 6**. Дополнительный revision требований не назначается.

Реализация и данные **не допущены к этапу 7**. Открытые `CMS-19`, `CMS-20` и `GAP-S6-01`—`GAP-S6-12` сохраняются как обязательные implementation/data/test blockers; их наличие нельзя считать снятым или смягчённым результатом `ACCEPT` документа.
