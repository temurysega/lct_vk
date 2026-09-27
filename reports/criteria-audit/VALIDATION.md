# Команды и результаты проверки

Проверена ветка `main`, `4330625b355f1516cf00c3e9b94437b074ca6c49`, 27.09.2026. Команды ниже воспроизводят проверки из сохранённых логов. Повторный запуск меняет только каталог аудита и временные данные; для сохранения первой выборки сначала скопируйте отчёт. Модель в этих командах не вызывается. Секреты, cookies и пароли в результаты не записывались.

## Подготовка без изменения основного окружения

```bash
cd '/Users/areluv/Downloads/путь в ML p.1/vk/lct_vk'
export AUDIT_ROOT="$PWD"
export AUDIT_TMP="$(mktemp -d /tmp/branddeck-criteria-audit.XXXXXX)"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$AUDIT_ROOT"
git branch --show-current
git rev-parse HEAD
git status --short
git rev-list --left-right --count HEAD...origin/main
```

Фактическая временная папка первой проверки — `/tmp/branddeck-criteria-audit.TZjsAv`. Данные приложения хранились в трёх отдельных workspaces внутри неё. `pip install`, `npm install`, обновление зависимостей основного окружения не выполнялись. Сборка wheel использовала отдельное временное PEP 517 build-окружение.

## Автоматические проверки

| Проверка | Результат | Доказательство и предел |
|---|---|---|
| Полный pytest без LibreOffice | 175 passed, 3 skipped, 7 warnings; 12,31 с | [pytest.log](evidence/pytest.log), [JUnit XML](evidence/pytest.xml). Пропуски обусловлены renderer; предупреждения Starlette/Swig, не падения |
| Три пропущенных теста с временным LibreOffice | 3 passed, 7 warnings; 9,60 с | [pytest-render.log](evidence/pytest-render.log). Отдельный запуск только этих тестов |
| Ruff | passed | [ruff.log](evidence/ruff.log) |
| TypeScript | passed | [typecheck.log](evidence/typecheck.log) |
| Production frontend build | passed | [frontend-build.log](evidence/frontend-build.log); output во временной папке |
| Импорт текущих Python-модулей | 35 успешных импортов | [imports.json](evidence/imports.json). Удалённые модули не подставлялись |
| Chrome smoke и layout/axe | passed | [smoke](evidence/browser-smoke.log), [layout](evidence/browser-layout.log); axe только на посещённых состояниях |
| Текущий UI, основной сценарий и repair | passed как взаимодействие | [без renderer](evidence/browser-current.json), [с renderer](with-renderer/evidence/browser-current.json). Успех взаимодействия не означает устранения выбранного дефекта |
| Firefox/WebKit | не запущены: отсутствуют ожидаемые бинарники | [Firefox](evidence/browser-layout-firefox.log), [WebKit](evidence/browser-layout-webkit.log); не засчитано как дефект продукта |
| Установка wheel вне checkout | **провал**: отсутствует extractor | [wheel-smoke.json](evidence/wheel-smoke.json), [сборка](evidence/wheel-build-isolated.log) |
| Документированный demo config | **провал**: нет шаблонов по заданному пути | [documented-demo.log](evidence/documented-demo.log) |
| Docker runtime | не проверено: daemon недоступен | [docker-availability.log](evidence/docker-availability.log); выводы о Docker ограничены анализом Dockerfile |

```bash
BRANDDECK_WORKSPACE="$AUDIT_TMP/test-workspace" .venv/bin/python -m pytest -q -p no:cacheprovider --basetemp "$AUDIT_TMP/pytest" --junitxml=reports/criteria-audit/evidence/pytest.xml
.venv/bin/ruff check --no-cache slide_agent backend tests
npm --prefix frontend run typecheck
cd "$AUDIT_ROOT/frontend"
./node_modules/.bin/vite build --outDir "$AUDIT_TMP/frontend-build" --emptyOutDir
cd "$AUDIT_ROOT"
.venv/bin/python -m examples.run_dataset --config examples/brief_demo.json
```

Последняя команда ожидаемо не находит `../task+data/Датасет` в данном окружении. Это отдельная проверка применимости документации.

## Настоящий офисный renderer во временной папке

Был загружен [официальный LibreOffice 26.2.6 для macOS arm64](https://download.documentfoundation.org/libreoffice/stable/26.2.6/mac/aarch64/LibreOffice_26.2.6_MacOS_aarch64.dmg). SHA-256 сверён с опубликованным mirrorlist. Версия бинарника 26.2.6.3; [данные проверки](evidence/isolated-renderer.json). Образ примонтирован read-only, программа не установлена в `/Applications`, шрифты не устанавливались. Команда подходит именно для этой платформы.

```bash
curl -fL 'https://download.documentfoundation.org/libreoffice/stable/26.2.6/mac/aarch64/LibreOffice_26.2.6_MacOS_aarch64.dmg' -o "$AUDIT_TMP/LibreOffice.dmg"
shasum -a 256 "$AUDIT_TMP/LibreOffice.dmg"
# Проверенное значение: 94bb3248df074c225490a8a6d1d9dc87c7d6783dbb7a8e9f0d0c3d94348552af
mkdir -p "$AUDIT_TMP/lo-mount"
hdiutil attach -readonly -nobrowse -mountpoint "$AUDIT_TMP/lo-mount" "$AUDIT_TMP/LibreOffice.dmg"
export BRANDDECK_LIBREOFFICE="$AUDIT_TMP/lo-mount/LibreOffice.app/Contents/MacOS/soffice"
"$BRANDDECK_LIBREOFFICE" --version
.venv/bin/python -m pytest -q -p no:cacheprovider --basetemp "$AUDIT_TMP/pytest-render" tests/test_delivery.py::test_actual_office_export tests/test_exemplar_cleanup.py::test_rendered_fill_flags_sparse_content_slides tests/test_render_audit.py::test_low_contrast_is_found_and_repaired_on_office_render
```

## Приложение и HTTP-сценарий

Исходный `frontend/dist` отставал от TSX: в нём отсутствовали новые поля назначения и изображений. Он сначала был проверен как есть на 8765. Затем неизменённый backend обслуживал свежую временную сборку UI на 8766, а на 8767 — её же с LibreOffice. Wrapper меняет лишь путь статики в памяти процесса. [Сравнение UI](evidence/frontend-features.json), [сборок](evidence/frontend-build-comparison.json).

Запуск каждого сервера — в отдельном терминале, остановка Ctrl-C. Для baseline запускайте без `BRANDDECK_LIBREOFFICE`, если переменная уже задана в текущей оболочке.

```bash
BRANDDECK_WORKSPACE="$AUDIT_TMP/app-workspace" .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

```bash
AUDIT_FRONTEND_BUILD="$AUDIT_TMP/frontend-build" AUDIT_PORT=8766 BRANDDECK_WORKSPACE="$AUDIT_TMP/fresh-app-workspace" .venv/bin/python reports/criteria-audit/scripts/serve_fresh_frontend.py
```

```bash
AUDIT_FRONTEND_BUILD="$AUDIT_TMP/frontend-build" AUDIT_PORT=8767 BRANDDECK_WORKSPACE="$AUDIT_TMP/rendered-app-workspace" BRANDDECK_LIBREOFFICE="$BRANDDECK_LIBREOFFICE" .venv/bin/python reports/criteria-audit/scripts/serve_fresh_frontend.py
```

Из отдельного терминала в корне репозитория:

```bash
AUDIT_BASE_URL=http://127.0.0.1:8765 .venv/bin/python reports/criteria-audit/scripts/api_audit.py
AUDIT_BASE_URL=http://127.0.0.1:8767 AUDIT_OUTPUT="$AUDIT_ROOT/reports/criteria-audit/with-renderer" .venv/bin/python reports/criteria-audit/scripts/api_audit.py
AUDIT_BASE_URL=http://127.0.0.1:8767 AUDIT_OUTPUT="$AUDIT_ROOT/reports/criteria-audit/additional-unseen" AUDIT_TEMPLATE_FILTER=unseen-onocom .venv/bin/python reports/criteria-audit/scripts/api_audit.py
.venv/bin/python reports/criteria-audit/scripts/api_errors.py
```

Скрипт регистрирует отдельный локальный тестовый аккаунт, загружает PPTX, анализирует, проверяет повторный анализ, отправляет полный материал и 2 изображения, создаёт 3 варианта по 10 слайдов, опрашивает job и скачивает PPTX/PDF/HTML. Сохраняет планы, анализ, manifests, QA, coverage и события. API errors использует baseline 8765.

Результаты с renderer: **15 колод**, 5 шаблонов, 150 слайдов, все форматы доступны. Базовый прогон без renderer: 12 PPTX сформированы, но PDF/HTML/preview недоступны и batch/job отмечаются failed. Сами индивидуальные PPTX доступны для скачивания. Это различие не скрыто за HTTP 200.

Отрицательные сценарии: неавторизованный доступ 401; пустой/повреждённый/неподдерживаемый шаблон 400; отсутствие контента 400; count=2 — 422; чужой результат/неизвестный шаблон — 404. Некорректный JSON принимается как job, затем failed; корректный PDF-контент даёт failed из-за отсутствующих optional converters. Неподдерживаемый/пустой content file возвращает 400, оставляя queued-запись. [Все события](evidence/api-negative.json).

## Браузер

```bash
PREDEL_BASE_URL=http://127.0.0.1:8765 PREDEL_BROWSER_CHANNEL=chrome PREDEL_TEST_TEMPLATE="$AUDIT_ROOT/../VK Tech шаблон.pptx" node tests/browser_smoke.cjs
PREDEL_BASE_URL=http://127.0.0.1:8765 PREDEL_BROWSER_CHANNEL=chrome node tests/browser_layout.cjs
PREDEL_BASE_URL=http://127.0.0.1:8765 PREDEL_BROWSER_TYPE=firefox node tests/browser_layout.cjs
PREDEL_BASE_URL=http://127.0.0.1:8765 PREDEL_BROWSER_TYPE=webkit node tests/browser_layout.cjs
AUDIT_BROWSER_PORT=8766 node reports/criteria-audit/scripts/browser_audit.cjs
AUDIT_BROWSER_PORT=8767 AUDIT_BROWSER_OUTPUT="$AUDIT_ROOT/reports/criteria-audit/with-renderer" node reports/criteria-audit/scripts/browser_audit.cjs
```

Последний сценарий проверяет актуальные поля UI, сохранение шаблона после reload, понятный отказ короткого брифа без модели, генерацию неизвестного шаблона, скачивание и выбор конкретного замечания для исправления. Сравнение repair: baseline 18 → 18 замечаний, с renderer 25 → 25; исходная колода не меняется, меняется только выбранный слайд. `decor_overlap` остаётся. [Сравнение](with-renderer/evidence/repair-comparison.json).

## Слайды, нативные объекты и содержимое

```bash
.venv/bin/python reports/criteria-audit/scripts/component_visuals.py
.venv/bin/python reports/criteria-audit/scripts/component_service.py
.venv/bin/python reports/criteria-audit/scripts/inspect_artifacts.py
.venv/bin/python reports/criteria-audit/scripts/compare_content.py
```

Компонентные скрипты запускаются после baseline API: читают путь к сохранённому анализу из manifest. Их ручной план создаёт 2 editable charts, 2 tables, process/cycle/hierarchy/icon grid. Это **не** демонстрация выбора визуализации моделью. Первый script также меняет данные диаграммы, ячейку и заголовок, сохраняет и повторно читает PPTX. Второй пропускает тот же план через полный сервис с QA/retries/экспортом.

`inspect_artifacts.py` независимо читает ZIP/OOXML, рекурсивно перечисляет объекты и строит PNG/contact sheets **из офисных PDF**. Для плотности, контраста и различимости он повторно использует эвристики приложения — эти поля не являются независимой экспертной оценкой. `compare_content.py` отдельно проверяет буквальное присутствие исходных единиц в тексте PDF; ни этот скрипт, ни product coverage не доказывают смысловую истинность.

Два baseline PPTX дополнительно экспортированы **Microsoft PowerPoint/macOS** скриптом [render_powerpoint.py](scripts/render_powerpoint.py); [результат](evidence/powerpoint-exports.json). После двух успешных экспортов дальнейшую автоматизацию блокировал диалог PowerPoint. Неуспешные попытки не засчитаны за дефекты файлов. Скрипт не следует запускать при несохранённых пользовательских документах в PowerPoint; для оставшихся файлов достаточно открыть копию вручную и сравнить со слайдами отчёта. Изображения анализатора — схематика, в оценке реального рендера не использовались.

## Воспроизведение дефекта пакета

```bash
mkdir -p "$AUDIT_TMP/checkout" "$AUDIT_TMP/wheels" "$AUDIT_TMP/wheel-site"
git archive HEAD | tar -x -C "$AUDIT_TMP/checkout"
.venv/bin/python -m pip wheel --no-deps --wheel-dir "$AUDIT_TMP/wheels" "$AUDIT_TMP/checkout"
export AUDIT_WHEEL_SITE="$AUDIT_TMP/wheel-site"
.venv/bin/python - <<'PY'
import os, pathlib, zipfile
base=pathlib.Path(os.environ['AUDIT_TMP'])
wheel=next((base/'wheels').glob('*.whl'))
with zipfile.ZipFile(wheel) as z:
    z.extractall(base/'wheel-site')
    print('extractor_in_wheel', 'shared/scripts/extract_template.py' in z.namelist())
PY
cd "$AUDIT_TMP"
"$AUDIT_ROOT/.venv/bin/python" -S -c 'import sys;sys.path.insert(0,sys.argv[1]);sys.path.append(sys.argv[2]);from slide_agent.analyzer import _load_extractor;print(_load_extractor())' "$AUDIT_WHEEL_SITE" "$AUDIT_ROOT/.venv/lib/python3.13/site-packages"
```

Ожидается `FileNotFoundError: Template extractor is missing`. `-S` исключает editable-install из checkout; зависимости только читаются из существующего site-packages. Для другой версии Python скорректируйте `python3.13`. Изолированная сборка wheel завершилась успешно; ранняя попытка без build isolation не нашла setuptools и не использовалась как доказательство дефекта продукта.

## Завершение

После остановки серверов и экспортов:

```bash
hdiutil detach "$AUDIT_TMP/lo-mount"
cd "$AUDIT_ROOT"
git diff --exit-code
git status --short
```

В завершённом аудите должен оставаться только новый каталог отчёта. Тестовые пользователи и история живут во временных workspaces; исходные пользовательские данные не изменялись. Итоговое состояние записано в [final-state.json](evidence/final-state.json).
