# Проверка веб-приложения в Windows — 28.09.2026

Проверка выполнена на Windows с заново собранным production frontend и локальным
FastAPI в отдельном `BRANDDECK_WORKSPACE`. На момент проверки `HEAD` — `0eb2c20`,
рабочее дерево содержало незакоммиченные изменения; это не проверка фиксированного
релиза. Тестовый шаблон — `slide-workspace/browser-fixture/clean-blue.pptx`, пять
слайдов, режим без модели. Тест создаёт отдельную учётную запись и три варианта
колоды в изолированном workspace.

| Браузер на Windows | Точная версия | `browser_layout.cjs` | `browser_smoke.cjs` |
|---|---:|---|---|
| Установленный Google Chrome | 153.0.8010.54 | пройден | пройден |
| Firefox из Playwright 1.63.0 | 155.0 | пройден | пройден отдельно и после исправления одновременно с WebKit |
| WebKit из Playwright 1.63.0 | 26.6 | пройден | пройден |

`browser_layout.cjs` проверил ширины 1440, 1366, 768, 390, 375 и 320 px,
положение CTA и иллюстрации, переключение примера, регистрацию и мобильное меню.
Горизонтального переполнения, ошибок JavaScript и нарушений WCAG A/AA по axe-core
в проверенных состояниях не найдено. `browser_smoke.cjs` прошёл регистрацию,
загрузку PPTX, генерацию трёх вариантов, переключение вариантов, скачивание
PPTX, историю после обновления страницы, выход, ошибочный пароль и повторный
вход. Эти результаты не оценивают качество самих слайдов.

## Сбой при одновременной генерации

Firefox и WebKit были запущены параллельно против одного API; каждый использовал
свою учётную запись. WebKit завершил полный сценарий. Задание Firefox
`f3022a83afdd49f9a0056d5202c993d7` перешло в `failed` через 26 мс после
создания. В `job.json` записано:

```text
[WinError 5] Отказано в доступе: '.../jobs/f3022a83afdd49f9a0056d5202c993d7/.job.json.ft7x0xec' -> '.../jobs/f3022a83afdd49f9a0056d5202c993d7/job.json'
```

UI ждал варианты, а Playwright завершился через 120 с с `TimeoutError` на
`#variants button`. Повтор того же Firefox-сценария без одновременного второго
запуска прошёл. Наблюдение указывает на конфликт доступа к файлу задания при
записи/чтении в Windows; конкретный механизм ещё не подтверждён.

После изменений в `jobs.py` и `utils.py` тестовый API был перезапущен. Два
полных сценария Firefox + WebKit повторно запущены одновременно: оба завершились
с кодом 0 примерно за 42–43 с. Их задания
`e4890b4be7554036bf93afb0e652ba40` и
`49a5d8350d124844b1e98a5c60b62334` имеют `status=completed` и
`stage=completed`. Это подтверждает исправление на одном повторе данной нагрузки;
устойчивость при иной конкуренции и в нескольких API-процессах не измерялась.

## Как повторить

Из корня репозитория в PowerShell после установки зависимостей и LibreOffice:

```powershell
.\.venv\Scripts\python.exe -m examples.create_demo_assets slide-workspace/browser-fixture
npm.cmd run build --prefix frontend
$env:BRANDDECK_WORKSPACE=(Join-Path (Get-Location).Path 'slide-workspace/browser-audit-2026-09-28')
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8766
```

Во втором терминале для каждого движка задайте `PREDEL_BROWSER_TYPE` как
`chromium`, `firefox` или `webkit`. Только для установленного Chrome задайте
`PREDEL_BROWSER_CHANNEL=chrome`; для двух остальных удалите эту переменную.

```powershell
$env:PREDEL_BASE_URL='http://127.0.0.1:8766'
$env:PREDEL_TEST_TEMPLATE=(Resolve-Path 'slide-workspace/browser-fixture/clean-blue.pptx').Path
$env:PREDEL_BROWSER_TYPE='chromium'
$env:PREDEL_BROWSER_CHANNEL='chrome'
node tests/browser_layout.cjs
node tests/browser_smoke.cjs
```

Для проверки конфликта запустите `browser_smoke.cjs` одновременно в двух
терминалах с `PREDEL_BROWSER_TYPE=firefox` и `webkit` и разными учётными
записями, которые сценарий создаст сам.

## Пределы проверки

Установленный Яндекс Браузер и нативный Firefox на этой машине не найдены.
WebKit на Windows не доказывает работу Safari на macOS. macOS, актуальная и
предыдущая версии всех четырёх браузеров из ТЗ как матрица не проверены.
Проверка пяти слайдов без модели не подтверждает генерацию по брифу, время
инференса, работу VK endpoint или качество 10–15-слайдовых колод.
