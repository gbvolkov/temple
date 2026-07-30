# Ревью автоматических тестов CMS

Дата актуализации: 30 июля 2026 года.

## Итог

Все функциональные группы исходного 111-шагового исследовательского аудита перенесены в постоянную Python/Playwright-регрессию. `tests/e2e/coverage.json` содержит 334 уникальных assertion ID и пустой `known_gaps`. Повторная приёмка этапа 6 расширила нормативный контракт: дополнительные negative/recovery/semantic/rollout ветки перечислены как `MISSING` в `outputs/cms-stage6-functional-requirements.md`; пустой старый `known_gaps` не означает их закрытие.

CMS-01–CMS-20 и GAP-02/03 переведены в положительную регрессию и имеют статус `fixed`. Full-профиль обязан выполнить все сценарии без функциональных `XFAIL`. Source-data профиль выполняет шесть положительных сценариев и один зарегистрированный expected `XFAIL`: содержательный `GAP-01`. `XPASS`, новый `FAIL`, лишний browser/HTTP error, изменение ожидаемого количества тестов или изменение источника считаются ошибкой контракта.

## Результаты приёмки

| Контур | Результат | Артефакт |
|---|---|---|
| Серверные тесты | 90/90, без failures и warnings | локальный полный pytest-прогон |
| Playwright smoke | 3/3, `contract_ok=true` | `output/playwright/cms-regression/20260730T134458-765507Z` |
| Playwright full | два последовательных прогона 42/42: 42 positive, 0 XFAIL, 334 assertions, `contract_ok=true` | `20260730T134511-428742Z`, `20260730T134734-459187Z` |
| Source-data schema 13 | 7/7: 6 positive + 1 expected `GAP-01`, `source_unchanged=true` | `output/playwright/cms-regression/20260730T153430-859018Z` |

Прежний `StarletteDeprecationWarning` закрыт положительным subprocess-тестом с warnings-as-errors.

## Что теперь автоматизировано

Перечень ниже описывает существующий регрессионный контур. Он не закрывает planned branches финальной матрицы требований этапа 6.

- все 10 типов контента, 9 типов блоков, составные relation/schedule/image/image-list поля;
- все операции блочного редактора: добавление, порядок, копирование, удаление, список, inline-форматирование и legacy conversion;
- полный UI workflow материала, расписание, checklist публикации, public URL, архив, корзина, восстановление и история;
- поиск, открытие, выбор страницы, cancel/confirm массовой публикации и архивирования;
- обе публичные формы, их клиентская валидация, успешная отправка и полный административный workflow заявок;
- media chooser и lifecycle медиатеки: upload, фильтры, detail, alt, постоянный URL, replacement, delete protection, missing-media replacement, pagination, reindex и итоговая сверка URL/файлов/ссылок;
- создание пользователей всех рабочих ролей, duplicate/validation, смена роли, disable/enable, смена пароля, повторный вход и отзыв сессий;
- полная capability-матрица viewer/editor/publisher/admin;
- migration dashboard, метрики, фильтры, пагинация, переход в редактор, постановка и завершение аудита;
- автоматическая подготовка: роли, preview/apply polling, классы, фильтр, пагинация, досье «до/после», сохранность флагов и rollback;
- pilot batch, source preview, сохранение всех решений, blocker disposition, индивидуальные и общие warning acknowledgement, submit, dismiss/confirm finalize, атомарный результат, cancel и read-only reopen;
- desktop, 390 px и 320 px, вход/save/no-op/logout и source-data целостность.

## Гарантии достоверности

1. Каждый browser-тест получает отдельную SQLite backup-копию и отдельные каталоги media/derivatives.
2. Runtime-сценарии используют `coverage_steps`: успешный тест падает, если хотя бы один заявленный assertion ID фактически не отмечен.
3. Позитивный сценарий падает при неожиданном `console.error`, `pageerror` или HTTP ≥400. Ожидаемые отрицательные ответы и известные console errors разрешаются только узкими маркерами status/path/text.
4. Отрицательные и терминальные ветки выполняются на независимых БД; confirm проверяется как в dismiss, так и в accept-ветке.
5. Ожидания привязаны к response, locator или фактическому состоянию DOM/API; фиксированные `wait_for_timeout` не используются.
6. Реестр дефектов валидирует обязательные поля и существование test node ID при collection.
7. Runner сам вычисляет ожидаемые числа тестов/XFAIL и coverage assertions, сохраняет JUnit/JSON и очищает изолированный workdir через `finally`.
8. Source-data профиль открывает исходную БД read-only, тестирует миграции на backup-копии, выполняет media reconciliation и полный этап preview/apply/verify на hardlink-копии медиатеки, затем сравнивает стабильный отчёт БД плюс fingerprint относительных путей, размеров и `mtime_ns` источника до/после.
9. Этап 6 добавляет 14 runtime assertion ID: роли preview/apply, ложный дубль cover/photo, очередь и завершение preview, метрики, досье, возврат, классификацию, пагинацию, отмену confirm, apply, сохранение migration-флагов, rollback и mobile CJM.
10. Сверка медиа отдельно проверяет dry-run без изменения байтов БД, идемпотентный apply, повреждённый manifest, URL-дубликаты, файлы вне manifest, missing issues, производные и ссылки текущих/исторических версий.

## Команды

```powershell
uv run python scripts/run_cms_regression.py --profile smoke
uv run python scripts/run_cms_regression.py --profile full
uv run python scripts/run_cms_regression.py --profile source-data `
  --source-db data/cms.sqlite3 `
  --source-media data/media
```

Финальная приёмка этапа 6 от 30.07.2026: server 101/101; smoke 3/3; два full-прогона 42/42 с 334 assertion ID; source-data 7/7 (6 positive и ожидаемый содержательный `GAP-01`). Все профили завершены с `contract_ok=true` и `source_unchanged=true`. Артефакты: `20260730T155727-829689Z`, `20260730T155740-660667Z`, `20260730T160006-281876Z`, `20260730T160241-094030Z` в `output/playwright/cms-regression/`.

Следующее изменение функциональности должно одновременно обновлять fixtures, assertion-матрицу, позитивные/негативные/ролевые/CJM-сценарии, реестр дефектов и документацию. После каждого этапа обязателен весь применимый контур, а не только новый тест.

## Дополнение: clean migration workflow

Добавлены 6 server/contract-тестов immutable source-bundle и чистого production
bootstrap. Они проверяют успешный bundle/database contract, повреждение файла,
незавершённый crawl, неверный archive checksum, порядок production gate,
запрет применения поверх существующего runtime и сохраняющий данные rollback.

Контрольный прогон после добавления workflow:

- server: 107/107;
- smoke: 3/3, artifact `20260730T200303-292959Z`;
- full: 42/42, 334 assertion ID, artifact `20260730T200322-973961Z`;
- source-data: 7/7, 6 positive + ожидаемый содержательный `GAP-01`,
  artifact `20260730T200600-961958Z`;
- во всех Playwright-профилях `contract_ok=true` и `source_unchanged=true`.
