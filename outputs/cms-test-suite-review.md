# Ревью автоматических тестов CMS

Дата: 21 июля 2026 года.

## Итог

Все функциональные группы исходного 111-шагового исследовательского аудита перенесены в постоянную Python/Playwright-регрессию. `tests/e2e/coverage.json` содержит 312 уникальных assertion ID и пустой `known_gaps`.

CMS-01–CMS-17 переведены в положительную регрессию и имеют статус `fixed`. Full-профиль обязан выполнить 41 положительный сценарий без функциональных `XFAIL`, source-data профиль — один положительный сценарий и три зарегистрированных `GAP-*`. `XPASS`, новый `FAIL`, лишний browser/HTTP error, изменение ожидаемого количества тестов или изменение источника считаются ошибкой контракта.

## Результаты приёмки

| Контур | Результат | Артефакт |
|---|---|---|
| Серверные тесты | 80/80, без failures и warnings | локальный `uv run pytest -q` |
| Playwright smoke | 3/3, `contract_ok=true` | `output/playwright/cms-regression/20260721T082254-602194Z` |
| Playwright full | два последовательных прогона 41/41: 41 positive, 0 XFAIL, 312 assertions, `contract_ok=true` | `20260721T082314-059980Z`, `20260721T082925-002787Z` |
| Source-data | 4/4: 1 positive + 3 XFAIL, `source_unchanged=true` | `output/playwright/cms-regression/20260721T082854-437851Z` |

Единственное предупреждение серверного контура — зарегистрированный `CMS-17` (`StarletteDeprecationWarning`). Оно контролируется отдельным characterization-тестом.

## Что теперь автоматизировано

- все 10 типов контента, 9 типов блоков, составные relation/schedule/image/image-list поля;
- все операции блочного редактора: добавление, порядок, копирование, удаление, список, inline-форматирование и legacy conversion;
- полный UI workflow материала, расписание, checklist публикации, public URL, архив, корзина, восстановление и история;
- поиск, открытие, выбор страницы, cancel/confirm массовой публикации и архивирования;
- обе публичные формы, их клиентская валидация, успешная отправка и полный административный workflow заявок;
- media chooser и lifecycle медиатеки: upload, фильтры, detail, alt, постоянный URL, replacement, delete protection, missing-media replacement, pagination и reindex;
- создание пользователей всех рабочих ролей, duplicate/validation, смена роли, disable/enable, смена пароля, повторный вход и отзыв сессий;
- полная capability-матрица viewer/editor/publisher/admin;
- migration dashboard, метрики, фильтры, пагинация, переход в редактор, постановка и завершение аудита;
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
8. Source-data профиль открывает исходную БД read-only, тестирует миграции на backup-копии и сравнивает стабильный отчёт БД плюс fingerprint относительных путей, размеров и `mtime_ns` медиатеки до/после.

## Команды

```powershell
uv run python scripts/run_cms_regression.py --profile smoke
uv run python scripts/run_cms_regression.py --profile full
uv run python scripts/run_cms_regression.py --profile source-data `
  --source-db data/cms.sqlite3 `
  --source-media data/media
```

Следующее изменение функциональности должно одновременно обновлять fixtures, assertion-матрицу, позитивные/негативные/ролевые/CJM-сценарии, реестр дефектов и документацию. После каждого этапа обязателен весь применимый контур, а не только новый тест.
