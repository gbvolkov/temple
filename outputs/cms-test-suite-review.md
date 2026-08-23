# Ревью автоматических тестов CMS

Дата актуализации: 23 августа 2026 года.

## Итог

Все функциональные группы исходного 111-шагового исследовательского аудита перенесены в постоянную Python/Playwright-регрессию. `tests/e2e/coverage.json` содержит 352 уникальных assertion ID и пустой `known_gaps`. Повторная приёмка этапа 6 расширила нормативный контракт: дополнительные negative/recovery/semantic/rollout ветки перечислены как `MISSING` в `outputs/cms-stage6-functional-requirements.md`; пустой старый `known_gaps` не означает их закрытие.

CMS-01–CMS-37 и GAP-02/03 переведены в положительную регрессию и имеют статус `fixed`. Full-профиль обязан выполнить все сценарии без функциональных `XFAIL`. Source-data профиль выполняет шесть положительных сценариев и один зарегистрированный expected `XFAIL`: содержательный `GAP-01`. `XPASS`, новый `FAIL`, лишний browser/HTTP error, изменение ожидаемого количества тестов или изменение источника считаются ошибкой контракта.

## Результаты приёмки

| Контур | Результат | Артефакт |
|---|---|---|
| Серверные тесты | 142/142, без failures и warnings | локальный полный pytest-прогон 23.08.2026 |
| Playwright smoke | 3/3, `contract_ok=true` | `output/playwright/cms-regression/20260823T100130-420826Z` |
| Playwright full | 44/44: 44 positive, 0 XFAIL, 352 assertions, `contract_ok=true` | `output/playwright/cms-regression/20260823T100830-929449Z` |
| Source-data schema 14 | 7/7: 6 positive + 1 expected `GAP-01`, `source_unchanged=true` | `output/playwright/cms-regression/20260823T095308-579610Z` |

Прежний `StarletteDeprecationWarning` закрыт положительным subprocess-тестом с warnings-as-errors.

Параллельный диагностический full `20260823T100130-420902Z` не принят: при
одновременном серверном pytest один Uvicorn превысил teardown timeout, хотя
функциональных failures не было. Последовательный повтор выше прошёл полностью;
ошибочный JUnit сохранён для диагностики и не используется как evidence приёмки.

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

Clean migration охватывается 28 server/contract-тестами, из них 21 проверяет
актуальный Windows → Ubuntu workflow. Они проверяют свежую пересборку
структурных данных, seed всех 1 615 известных публичных маршрутов, запрет
небезопасных HTTP-методов, bundle/database contract, повреждение файла,
fail-closed production `.env`, checksum/tar guards, порядок production gate,
запрет применения поверх существующего runtime, сохраняющий данные rollback и
обязательную изоляцию Windows-preprod checkout/runtime/bundle от dev-каталога.
После реального сбоя добавлены regressions `CMS-21`–`CMS-24`: bounded retry
временной сети без retry постоянного 404/410, модульный запуск validator на
Windows/Ubuntu, resume только незапечатанного bundle и синхронизация
`remaining_queue` после удаления дублирующего хвоста очереди.

Два первых production cutover безопасно откатились и дали новые
characterization regressions. `CMS-25` подтверждает, что Ubuntu выполняет
source-bundle/database validator в уже собранном application image, поэтому
host Python не обязан содержать `pypdfium2` и другие зависимости проекта.
`CMS-26` проверяет реальный static legacy GIF: повторный reindex даёт `ready`,
но тот же формат по-прежнему отклоняется путём пользовательской загрузки;
анимированные и повреждённые GIF не разрешены. `CMS-27` воспроизводит
проверенный PNG payload с историческим `.jpg` и одновременно доказывает, что
user-upload mismatch остаётся запрещённым. `CMS-28` требует fail-closed
`status=ok`/`unexplained_count=0` как в bootstrap, так и в post-activation
reverify.

Контрольный прогон после добавления workflow:

- server: 130/130;
- smoke: 3/3, artifact `20260804T184426-667727Z`;
- full: 42/42, 334 assertion ID, artifact `20260804T184838-759593Z`;
- source-data: 7/7, 6 positive + ожидаемый содержательный `GAP-01`,
  artifact `20260804T185111-333706Z`;
- во всех Playwright-профилях `contract_ok=true` и `source_unchanged=true`.

Дополнительно реальный одностраничный smoke нового source-crawler против
`https://www.sv-innokenty.ru/` вернул HTTP 200, `failures=0` и пустой
`blocked_write_requests`; checkpoint:
`work/source-crawler-readonly-smoke-20260801.json`. Оставшаяся очередь 34
ожидаема, потому что smoke намеренно запущен с `--max-pages 1`; production
bundle всегда запускается без лимита и требует пустую очередь.
