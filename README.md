# predel

Веб-сервис презентаций. Регистрация требует только имя пользователя, пароль и
должность. Email не нужен. Материалы и результаты изолированы по аккаунтам.

`frontend/` — React + TypeScript + Vite; `backend/` — FastAPI и аккаунты;
`slide_agent/` — движок презентаций.
Подробнее: [frontend](frontend/README.md), [backend](backend/README.md).

```bash
python3 -m venv .venv
source .venv/bin/activate
npm ci --prefix frontend
npm run build --prefix frontend
pip install -e ".[api,documents,export,dev]"
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Откройте `http://127.0.0.1:8000`. Модель подключается существующими переменными
`INFERENCE_*`; без них доступна сборка по полным готовым материалам.
Для сборки интерфейса и Python-пакета из исходников нужен Node.js
22.12+ (либо 20.19+); при запуске готовой сборки Node.js не требуется.
Docker собирает интерфейс автоматически отдельным этапом.

Сервис анализирует `.pptx`- или `.pdf`-шаблон и создаёт новую презентацию в его стиле. Система извлекает дизайн-токены и паттерны, планирует структуру через OpenAI-compatible Inference API, выбирает подходящий макет для каждого смыслового блока, собирает PowerPoint и запускает автоматический QA.

Исходные парсер OOXML, промпты и совместимый PPTXGenJS-раннер сохранены; поверх них добавлен автономный Python-сервис, которому не нужны внешние ИИ-агенты или Node.js.

## Как это работает

![Студия predel: шаблон, материалы, сборка трёх вариантов, аудит и скачивание](docs/demo/studio.gif)

1. **Шаблон.** PPTX или PDF разбирается один раз: мастера, макеты,
   плейсхолдеры, шрифты, шкала кеглей, палитра, поля и сетка. Результат —
   `design_system.json` и `pattern_catalog.json`, повторно шаблон не
   разбирается.
2. **Материалы.** Нужен краткий бриф с назначением (фича, продукт, проект,
   инициатива) или готовый текст. Картинки ставятся на подходящие слайды.
3. **План.** С моделью структура и текст пишутся по брифу, без модели
   раскладывается готовый текст. План один на все три варианта.
4. **Вёрстка.** Три варианта (`balanced`, `columns`, `focus`) собираются из
   макетов того же шаблона нативными объектами: текст, таблицы, диаграммы,
   схемы в стиле SmartArt, пиктограммы.
5. **Аудит.** Детерминированные проверки смотрят геометрию, шрифты, палитру,
   контраст и заполненность. Контекстуальные проверки модели оценивают смысл
   и рендер. Замечания видны на превью, пользователь сам выбирает, что
   исправить.
6. **Экспорт.** PPTX, PDF и HTML.

Запись сделана 29.09.2026 на ноутбуке без видеокарты (Intel Core Ultra 5 125H),
режим «Без модели». Шаблон — «Technology Consulting» от
[Slidesgo](https://slidesgo.com), сервис его раньше не видел. Материал —
[`examples/acceptance_content.md`](examples/acceptance_content.md) и две
картинки из `examples/acceptance_images`. Разбор шаблона занял около 12 с,
сборка трёх вариантов с аудитом и экспортом — 259 с. В записи ожидание
ускорено, а счётчик показывает настоящее время. Кадры «Вся колода» и
«Три варианта» — рендеры LibreOffice этих же колод. Технический аудит всех
трёх вариантов пройден без замечаний, поэтому шаг выбора исправлений на
записи не виден. Как он работает — в [ARCHITECTURE.md](ARCHITECTURE.md) и
[AUDIT.md](AUDIT.md).

## Результаты

Контрольный прогон 28.09.2026 на коммите `f96244b`: краткий бриф
[`examples/brief_feature.md`](examples/brief_feature.md) и две картинки →
Qwen3.5-9B Q4_K_M (llama.cpp) на RTX 2070 SUPER 8 ГБ → три предоставленных
шаблона VK × три варианта с экспортом PPTX/PDF/HTML. Конфиг —
[`examples/brief_demo.json`](examples/brief_demo.json). **Все девять презентаций
(PPTX и PDF) лежат в [`deliverables/`](deliverables/README.md).** Хэши входов,
кода и опубликованных файлов, а также машинные метрики — в
[`reports/final-dataset/evidence.json`](reports/final-dataset/evidence.json);
`python examples/export_run_evidence.py --verify` сверяет их с клоном. Прогон
прошёл все строгие критерии конфига: QA `passed` у каждой колоды, по две
картинки, различимые варианты, не больше 300 с на три варианта шаблона, код и
входы не менялись во время прогона. Как повторить и что было до этого —
[`reports/final-dataset/README.md`](reports/final-dataset/README.md). Колоды
собраны кодом `f96244b`; версии 1.0.0 и 1.1.0 новее (визуальный аудит,
исправления для незнакомых шаблонов, см. [CHANGELOG](CHANGELOG.md)), и прогона
3 × 3 по брифу с моделью на их коде пока нет: для него нужны исходные PPTX
организаторов и GPU. На такой машине колоды пересобирает и публикует одна
команда: `python examples/rebuild_deliverables.py` (см. «Быстрый старт»). Код
1.1.0 проверен офлайн-прогоном 3 × 3 на трёх шаблонах,
которых сервис раньше не видел ([отчёт 29.09.2026](reports/unseen-2026-09-29/README.md)).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/charts/time-dark.png">
  <img alt="Время полного сценария на три варианта шаблона: VK Tech 171 с, VK WorkSpace 200 с, VK Education 224 с при лимите ТЗ 300 с; из них LLM 31–60 с" src="docs/charts/time-light.png">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/charts/qa-dark.png">
  <img alt="Структурный QA девяти колод: до исправлений 28.09 — 96 у всех девяти, после — 100 у всех девяти" src="docs/charts/qa-light.png">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/charts/fill-dark.png">
  <img alt="Заполненность каждого контентного слайда девяти колод: все значения от 27 до 68 процентов, внутри нормы 25–75 процентов" src="docs/charts/fill-light.png">
</picture>

| Шаблон | Весь сценарий, с | LLM, с | QA до → после (balanced / columns / focus) | Картинки | Мин. заполненность |
|---|---:|---:|---|---|---:|
| VK Tech | 171 | 31 | 96 → 100 / 96 → 100 / 96 → 100 | 2 из 2 / 2 из 2 / 2 из 2 | 31 % |
| VK WorkSpace | 200 | 32 | 96 → 100 / 96 → 100 / 96 → 100 | 2 из 2 / 2 из 2 / 2 из 2 | 27 % |
| VK Education | 224 | 60 | 96 → 100 / 96 → 100 / 96 → 100 | 2 из 2 / 2 из 2 / 2 из 2 | 30 % |

- «До» — тот же бриф на коммите `76a3a7d` (начало доработок 28.09): в каждой
  колоде оставался полупустой слайд или английское слово. Модель
  недетерминирована, поэтому это не чистый A/B; все промежуточные прогоны —
  в [отчёте](reports/final-dataset/README.md).
- В столбце LLM — построение структуры и текста слайдов; смысловой аудит и одна
  перепись отмеченных слайдов входят во «Весь сценарий». Перепись исправила 6,
  9 и 5 слайдов (VK Tech, VK WorkSpace, VK Education); после неё текстовый аудит
  оставил 3, 6 и 1 подсказку редактору на колоду. Они в балл QA не входят и
  требуют просмотра перед показом.
- Автоповтор QA сработал там, где первая сборка нашла дефект: в VK WorkSpace —
  схемы поперёк декора образца (до трёх повторов, меняются макеты только этих
  слайдов), в VK Education — полупустой слайд 8, пересобранный карточками.
- Контент синтетический, не официальный контент-пакет организаторов. Графики и
  таблицу пересобирает
  `python examples/readme_charts.py --before <до> --after <после>` по двум
  `dataset_report.json`.
- Исходные PPTX организаторов лежат вне Git в `../task+data/Датасет`. Для
  повторного прогона их нужно разместить рядом с клоном и проверить SHA-256 по
  [отчёту](reports/final-dataset/README.md).

## Что уже работает

- Анализ masters, layouts, placeholders, геометрии, шрифтов, цветов, фонов, таблиц и изображений.
- Формирование `design_system.json` и `pattern_catalog.json`, пригодных для повторного использования.
- Контент-планирование через любой OpenAI-compatible `/chat/completions` endpoint.
- Генерация по краткому брифу и назначению (фича, продукт, проект, инициатива):
  модель сначала строит структуру колоды, затем пишет каждый слайд. Ответы
  ограничены JSON-схемой, число слайдов совпадает с запрошенным.
- Детерминированный локальный planner для работы без API и CI-тестов.
- Семантический выбор паттерна под cover, section, content, comparison, data, image и closing.
- Ёмкостный layout mapping по геометрии текста, числу слотов, данным и изображениям с `why_fit`, рисками и альтернативами в `deck_plan.json`.
- Два режима композиции: semantic layouts/exemplars для нормальных PPTX и `native_grid` для PDF-конверсий, где каждый слайд заново собирается из редактируемых объектов по извлечённой сетке.
- Прямой приём PDF в CLI и REST API с фильтрацией служебных масок и ограничением декоративных PDF-фрагментов.
- Удаление уникального исходного контента (скриншотов, клиентских логотипов и фото) с сохранением повторяющегося брендинга и полноэкранных фонов.
- Генерация текста, таблиц, bar/pie charts, metric cards и timeline.
- Нативные схемы в стиле SmartArt (процесс, цикл, иерархия, пирамида, воронка,
  матрица 2×2, сетка пиктограмм) и 135 векторных пиктограмм Lucide в цветах
  шаблона. Без модели схемы выбираются правилами `slide_agent/assets/visual_rules.json`.
- Изображения из загрузок и из материалов (PPTX, DOCX, PDF, Markdown) ставятся
  на подходящие слайды: в фото-слот примера, picture-плейсхолдер или рядом с
  текстом, с кадрированием без искажений. Иначе — векторная иллюстрация по теме.
  Генерация картинок моделью подключается через `IMAGE_*` (см. MODELS).
- Проверка целостности OOXML, числа слайдов, границ элементов, переполнения, пересечений текста и шрифтовой консистентности; в Windows результат дополнительно рендерится настоящим PowerPoint и возвращается в QA-feedback loop.
- CLI, REST API и Docker.
- Асинхронные persistent jobs с загрузкой контентного файла, стадиями, прогрессом и отдельным download endpoint.

## Быстрый старт

### Презентация по краткому брифу (Qwen3.5-9B, GPU или CPU)

Модель — [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) (Apache 2.0, 9,7B)
в GGUF Q4_K_M, 5,7 ГБ. Один и тот же файл весов работает на видеокарте с 8 ГБ
и на сервере без GPU (нужно около 8 ГБ RAM). Файл, ревизия и SHA-256 закреплены
в [deploy/llama/qwen3.5-9b.json](deploy/llama/qwen3.5-9b.json). Сервер модели —
llama.cpp b11053: CUDA- или CPU-сборка для Windows, Docker-образ собирается из той
же ревизии. Веса в git не хранятся (файл 5,7 ГБ больше лимита GitHub даже для LFS):
`deploy/llama/setup_local.py` скачивает модель в `models/gpu/` и сервер в
`external-fixtures/`, докачивает после обрыва и сверяет SHA-256 каждого файла.

```powershell
# Один раз: веса и llama.cpp (--device cpu без видеокарты)
python deploy/llama/setup_local.py
# Windows, видеокарта (или -Device cpu -Threads 8 без неё)
powershell -File deploy/llama/start-llama.ps1
# Три шаблона × три варианта по брифу; параметры модели — в самом конфиге
python examples/run_dataset.py --config examples/brief_demo.json
```

Пересобрать опубликованные колоды на текущем коммите — одна команда:
`python examples/rebuild_deliverables.py`. Сначала она проверяет, что код,
конфиг и входы закоммичены, шаблоны организаторов совпадают по SHA-256, а
LibreOffice и сервер модели доступны (`--check` — только эта проверка). Затем
она запускает прогон 3 × 3, копирует колоды в `deliverables/` (без
неиспользуемых макетов шаблона), выгружает и сверяет
`reports/final-dataset/evidence.json`. Если хоть один порог конфига не пройден,
ничего не публикуется. Подробности — в [отчёте](reports/final-dataset/README.md).
`python examples/recheck_plans.py <dataset_report.json> --output <папка>`
пересобирает сохранённые планы без нового вызова модели: так проверяются правки
вёрстки и QA на тех же текстах.

Одна колода из CLI и сервер без GPU:

```powershell
branddeck generate --template brand.pptx --content examples/brief_feature.md `
  --slides 12 --mode brief --purpose feature --export all
docker compose -f compose.llm-cpu.yaml up -d --build
```

`--mode auto` (по умолчанию) включает режим брифа для текста до 1500 знаков
без разделов, если модель подключена; `source` раскладывает готовый материал,
`brief` требует модель. В студии назначение выбирается в поле «Назначение».
Модель использует только факты брифа: числа, которых нет в брифе, аудит
помечает как `number_not_in_source`. Результаты прогона:
[reports/brief-llm-2026-09-23](reports/brief-llm-2026-09-23/README.md).

### VPS 4 ГБ: только веб-сервис

Модель на такой сервер не помещается: Qwen3.5-9B нужно около 8 ГБ RAM. На VPS
работает только веб-сервис из `compose.yaml`, а модель запускается на машине с
видеокартой (`deploy/llama/start-llama.ps1`) или у любого OpenAI-совместимого
провайдера. В `.env` на VPS указывается её адрес; остальные параметры модели —
как в блоке `inference` файла `examples/brief_demo.json`:

```dotenv
INFERENCE_BASE_URL=http://<адрес сервера модели>:8080/v1
INFERENCE_MODEL=qwen3.5-9b
BRANDDECK_CPU_MODE=1
```

`BRANDDECK_CPU_MODE=1` выполняет тяжёлые задачи (анализ шаблона, сборку и
экспорт) по одной, чтобы параллельные запросы не упирались в память сервера.

```bash
docker compose up -d --build
```

`start-llama.ps1` слушает только локальный адрес: до VPS модель доводится через
VPN или SSH-туннель, открывать порт модели в интернет не нужно.

### Локальная установка

Требования: Python 3.10+. Для сборки веб-интерфейса дополнительно Node.js 22.12+.

```powershell
python -m pip install -e ".[api]"
branddeck --json doctor
```

### Веб-интерфейс и три варианта

Для предпросмотра, PDF и HTML установите **LibreOffice** и зависимости экспорта:

```powershell
python -m pip install -e ".[api,export]"
npm ci --prefix frontend
npm run build --prefix frontend
python -m slide_agent serve
```

Откройте **http://127.0.0.1:8000**. Загрузите PPTX, добавьте материалы и создайте
три варианта. Вкладки меняют композицию при одинаковом содержании. Замечания
аудита показаны рядом со слайдами и рамками поверх проблемных блоков.
Выберите доступные исправления: сервис создаст новую версию, изменив макеты
только выбранных слайдов. Замечания, требующие редактуры содержания, отмечаются
отдельно и автоматически не исправляются. Исправленный результат снова проходит QA.

CLI для одного шаблона и всех форматов:

```powershell
python -m slide_agent variants --template "..\task+data\Датасет\VK Tech шаблон.pptx" --content examples/acceptance_content.md --images examples/acceptance_images --slides 10 --offline --export all
```

Изображения для слайдов: `--images папка_или_файлы` (PNG, JPG, WEBP). Картинка
попадает на слайд, чей текст совпадает с её подписью или именем файла; файлы
вроде `IMG_2041.jpg` ставятся по порядку и помечаются в манифесте. В студии
для этого есть кнопка «Добавить изображения». Прогон трёх шаблонов с
картинками и отчёт: [reports/visuals-2026-09-22](reports/visuals-2026-09-22/README.md).

Воспроизводимый прогон **3 шаблона × 3 варианта**:

```powershell
python examples/run_dataset.py --config examples/demo.json
```

Пути в конфиге задаются относительно корня репозитория. По умолчанию используются
три предоставленных PPTX из `../task+data/Датасет`, два изображения и **синтетический**
контент из `examples/acceptance_content.md`.
Официальный контент-пакет организаторов нужно указать в `content`.
Результаты: `slide-workspace/dataset/index.html`, `dataset_report.json`, а также
PPTX/PDF/HTML, изображения и отчёты каждой презентации. Для просмотра тех же
шаблонов в веб-интерфейсе загрузите их в свой аккаунт: CLI-workspace и личные
каталоги пользователей разделены.

PDF и HTML строятся из реального рендера PPTX через LibreOffice. Сам PPTX содержит
редактируемые объекты. HTML — автономный просмотр с SVG-страницами, а не редактор.
Установите шрифты шаблона в системе: при отсутствии шрифта LibreOffice заменит его,
что может изменить переносы строк. Ошибка экспорта и проваленный QA не считаются
успешной задачей; CLI возвращает ненулевой код, API сохраняет статус `failed`.

Ось вариантов — **выбор макетов**: `balanced` выбирает по соответствию содержанию,
`columns` предпочитает несколько текстовых блоков, `focus` — один крупный блок.
Повторение макетов между вариантами мягко штрафуется; текст не переписывается.
Если два варианта почти одинаковы на готовом рендере, сервис один раз пересобирает
часть слайдов одного варианта с другими паттернами того же шаблона и повторяет
пиксельную проверку. Если различия всё ещё малы, отчёт содержит
`diversity_status=needs_review`. Сравнение учитывает изменённые пиксели на
содержательных слайдах.
Разные последовательности макетов сами по себе не доказывают визуальное качество.
Для прогона с локальной Qwen3.5-9B используйте `examples/brief_demo.json`: там
зафиксированы три шаблона, краткий бриф, изображения, параметры инференса,
текстовый смысловой аудит и ограничения приёмки по QA, изображениям и времени.
Текстовый аудит предлагает замечания для редактора; он не проверяет рендеры как VLM.

Документация сдачи: [ARCHITECTURE](ARCHITECTURE.md), [MODELS](MODELS.md), [AUDIT](AUDIT.md),
[CHANGELOG](CHANGELOG.md) — версии сервиса и workflow (промптов, скиллов, агентов).
Проверка браузеров и её ограничения: [отчёт 28.09.2026](reports/browser-2026-09-28/README.md).

Настройте Inference API (названия переменных не привязаны к конкретному провайдеру):

```powershell
$env:INFERENCE_BASE_URL="https://inference.example/v1"
$env:INFERENCE_API_KEY="..."
$env:INFERENCE_MODEL="your-model"
```

Полный pipeline одной командой:

```powershell
branddeck run `
  --template .\templates\brand.pptx `
  --content .\content.md `
  --slides 8 `
  --output .\result.pptx
```

Без LLM, для локальной проверки:

```powershell
branddeck run --template .\brand.pptx --content .\content.md --slides 8 --offline
```

## Отдельные этапы

```powershell
# Один раз разобрать шаблон
branddeck --json analyze .\brand.pptx --name corporate

# Посмотреть доступные template id
branddeck --json templates

# Генерировать сколько угодно презентаций из кэша
branddeck --json generate --template corporate-<hash> --content .\content.md --slides 10

# Повторно проверить готовый файл
branddeck --json inspect .\result.pptx --template .\slide-workspace\templates\corporate-<hash>
```

`--content` принимает inline-текст, Markdown, TXT, JSON, CSV и PPTX. Word/PDF/Excel доступны после установки `pip install -e ".[documents]"`.

## REST API

```powershell
branddeck serve --host 0.0.0.0 --port 8000
```

Swagger UI: `http://localhost:8000/docs`.

Все `/v1/*` требуют сессию. Сначала зарегистрируйтесь или войдите и сохраните cookie:

```bash
curl -c cookies.txt -H 'Content-Type: application/json' \
  -d '{"username":"designer","password":"replace-with-a-strong-password","position":"Дизайнер"}' \
  http://localhost:8000/api/auth/register
```

```bash
curl -b cookies.txt -F "file=@brand.pptx" -F "name=corporate" \
  http://localhost:8000/v1/templates/analyze

curl -b cookies.txt -F "template_id=corporate-<hash>" \
  -F "content=Создай отчёт о результатах квартала..." \
  -F "slide_count=8" \
  http://localhost:8000/v1/presentations/generate

curl -b cookies.txt -o result.pptx \
  http://localhost:8000/v1/presentations/<presentation-id>/download
```

Для долгой генерации и загрузки документа:

```bash
curl -b cookies.txt -F "template_id=corporate-<hash>" \
  -F "content_file=@content.md" \
  -F "slide_count=8" \
  http://localhost:8000/v1/presentations/jobs

curl -b cookies.txt http://localhost:8000/v1/jobs/<job-id>
curl -b cookies.txt -o result.pptx http://localhost:8000/v1/jobs/<job-id>/download
```

Асинхронные записи API сохраняются в `slide-workspace/users/<user-id>/jobs/`. Текущая реализация
исполняет задачи в процессе API; для горизонтального масштабирования потребуется
внешняя очередь.

## Почему решение адаптируется к шаблону

```text
PPTX/PDF template
   ├─ OOXML parser ──> context.json
   ├─ source classifier ──> template_layout | native_grid
   ├─ token/grid extractor ──> design_system.json
   └─ semantic pattern miner ──> pattern_catalog.json
                              │
Content ──> LLM planner ──> capacity-aware pattern scoring
                              │
                              v
       native layouts/exemplars OR rebuilt native composition
                              │
                              v
          output.pptx ──> PowerPoint render ──> QA ──> retry
```

Здесь нет заранее зашитого фирменного шаблона: папка анализа имеет content-addressed id. Для семантического PPTX каждый слайд плана связан с layout/master текущего файла. Если анализатор обнаруживает PDF-фрагментацию, исходные text/vector fragments не клонируются: composer переносит только повторяющиеся брендовые assets и заново строит cover, cards, list, split и closing по извлечённым шрифтам, цветам, полям и сетке.

## Артефакты

Каждая генерация сохраняет `coverage_report.json`: буквальное сопоставление
исходных заголовков/предложений с планом и текстом, таблицами, диаграммами PPTX.
Несопоставленные фрагменты дают предупреждение QA и требуют проверки; перефраз
не считается доказанной потерей смысла. Заметки докладчика не засчитываются
как содержимое слайдов. Это контроль текстового покрытия, не проверка истинности
фактов или видимости текста на рендере.

Offline-планировщик объединяет соседние разделы при ограничении числа слайдов.
Нормализация и повторная попытка больше не обрезают текст/массивы диаграмм.
При недостатке материала для заданного числа слайдов возвращается понятная
ошибка вместо пустых слайдов. Слишком плотный материал сохраняется и проходит
QA; автоматическое смысловое сокращение пока не реализовано.

Каталог анализа версии 1.3 различает полноценные текстовые зоны и короткие
подписи, учитывает слоты картинок и изображения макета. Старый кэш при анализе
исходного файла пересоздается. Прямой выбор по старому template_id требует
повторного анализа исходника. Финальный использованный план записывается в
`deck_plan.final.json`, включая результат повторного выбора макета.

```text
slide-workspace/
  templates/{name}-{sha256[:8]}/
    original.pptx
    source.pdf             # если шаблон был загружен как PDF
    context.json
    extraction.log
    design_system.json
    pattern_catalog.json
    guideline.md
    previews/              # geometry-accurate PNGs for optional VLM analysis
    images/
  presentations/{topic}-{timestamp}/
    source-content.md
    deck_plan.json
    outline.md
    output.pptx
    qa_report.json
    powerpoint-render-N/     # PNG из PowerPoint COM, если доступен
    manifest.json
```

## Demo и тесты

### Baseline на предоставленных шаблонах

Из корня репозитория после установки зависимостей:

```bash
python examples/run_baseline.py
```

По умолчанию скрипт ищет PPTX в родительской папке `vk` и создает по одной
10-слайдовой презентации на одинаковом синтетическом материале
`examples/baseline_content.md`. Это технический offline-прогон, а не проверка
качества LLM или официальный контент-пакет. Параметры `--templates`, `--content`
и `--output` позволяют задать другие пути.

В `slide-workspace/baseline/<UTC-время>/` сохраняются PPTX, подробные отчеты,
`summary.json` и снимок зависимостей. Сводка содержит время, проблемы QA,
статус рендера и контроль неизменности исходников. Структурный QA не заменяет
визуальную приемку; при отсутствии PowerPoint COM рендер отмечен как skipped.
При провале QA скрипт возвращает ненулевой код завершения и сохраняет результаты.

Для ручной визуальной приемки на macOS с установленным PowerPoint:

```bash
osascript examples/export_powerpoint_macos.applescript /absolute/generated.pptx /tmp/new-output.pdf
```

Скрипт экспортирует открытый им файл и закрывает его без сохранения исходного
PPTX. Уже открытая презентация с тем же именем и существующий выходной PDF
отклоняются. Результат нужно проверить на наличие файла и ожидаемого числа
страниц. Это отдельная проверка приемки: Windows COM-аудит в API на macOS
по-прежнему отмечается как `skipped`. Автоматизация PowerPoint может зависать;
для пакетных прогонов задавайте внешний тайм-аут и не считайте отсутствие
PDF успешным экспортом. Для Linux предусмотрен экспорт через LibreOffice;
его необходимо установить в среду, где запускается сборка презентаций.

Создать три разных шаблона (dark tech, editorial, clean blue) и общий набор контента:

```powershell
python .\examples\create_demo_assets.py .\demo-assets
branddeck run --template .\demo-assets\tech-dark.pptx --content .\demo-assets\content.md --slides 7 --offline
```

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

Для полного набора проверок нужен **LibreOffice**. Тест
`tests/test_delivery.py::test_actual_office_export` действительно преобразует PPTX
в PDF, HTML и PNG-превью, проверяет число страниц и неизменность исходника.
Если LibreOffice отсутствует, только этот тест автоматически пропускается;
`python -m pytest -rs` показывает причину. Для нестандартной установки задайте
`BRANDDECK_LIBREOFFICE` — путь к `soffice.com` на Windows или `soffice` на Linux.
Отдельный запуск: `python -m pytest tests/test_delivery.py::test_actual_office_export -v -rs`.

### Проверка на VK Tech

Текущий основной набор три PPTX из папки `данные`; команда прогона приведена
выше. Дополнительная проверка из предыдущей версии проекта использует публичную
презентацию VK Tech за I квартал 2026 года на 14 страниц. Исходный PDF не включается в git и не переносится в
результат: он используется только для извлечения цветовых ролей, сетки и
повторяющихся композиционных правил.

```powershell
python -m pip install -e ".[dev,pdf-reference]"
$env:BRANDDECK_POWERPOINT_QA="1"

branddeck --workspace .\external-fixtures\vk-tech\q1-2026-workspace run `
  --template .\external-fixtures\vk-tech\VK_Tech_prezentacziya_za_1_Q2026_ae725c0cff.pdf `
  --content .\examples\vk_tech_case_content.md `
  --slides 10 --offline `
  --output .\external-fixtures\vk-tech\generated-vk-tech-q1-2026-native.pptx
```

Результат воспроизводит тёмную обложку, светлые контентные слайды, синюю
акцентную систему, повторяющуюся верхнюю навигацию и split-композицию финала.
Все композиции заново собираются нативными объектами; полноэкранных selectable
pictures в слайдах нет. Публичный синий набор и тёмный спикерский материал
используются только как дополнительные stress-tests.
Источники, хэши, второй прогон и ограничения проверки приведены в
[отчёте VK Tech](docs/vk-tech-validation.md).

## Конфигурация Inference API

| Переменная | Назначение |
|---|---|
| `INFERENCE_BASE_URL` | Base URL, например `https://host/v1`, либо полный URL `/chat/completions` |
| `INFERENCE_API_KEY` | Bearer token; может быть пустым для локального endpoint |
| `INFERENCE_MODEL` | Идентификатор модели |
| `INFERENCE_TIMEOUT` | Таймаут запроса, по умолчанию 120 секунд |
| `INFERENCE_MAX_RETRIES` | Число сетевых попыток, по умолчанию 3 |
| `INFERENCE_VISION` | `1` разрешает передавать PNG-превью модели, если endpoint принимает изображения; `0` оставляет только JSON |
| `INFERENCE_RENDER_AUDIT` | `1` включает визуальный аудит: модель смотрит PNG каждого итогового слайда и отвечает на вопросы Приложения 1 (требует `INFERENCE_VISION=1` и модель с изображениями); по умолчанию выключен |
| `INFERENCE_RENDER_AUDIT_TIMEOUT` | Таймаут одного запроса визуального аудита, по умолчанию 60 с |
| `INFERENCE_PARALLEL` | Сколько слайдов брифа писать одновременно; равно `--parallel` сервера llama.cpp (по умолчанию 1) |
| `INFERENCE_EXTRA_BODY` | JSON-объект, добавляемый в каждый запрос, например `{"chat_template_kwargs": {"enable_thinking": false}}` для Qwen3.5 |
| `BRANDDECK_WORKSPACE` | Альтернативная папка артефактов |
| `BRANDDECK_POWERPOINT_QA` | `auto` по умолчанию; `1` требует PowerPoint render-check в Windows, `0` отключает его |
| `IMAGE_BASE_URL`, `IMAGE_MODEL`, `IMAGE_API_KEY` | Необязательный OpenAI-совместимый `/images/generations`; без них картинки не генерируются |
| `IMAGE_MAX_PER_DECK`, `IMAGE_AUTO`, `IMAGE_SIZE` | Лимит генераций (3), генерация для слайдов без визуала (`0`/`1`), размер (`1024x768`) |
| `IMAGE_TIMEOUT` | Таймаут запроса генерации картинки, по умолчанию 180 с |
| `INFERENCE_CONTEXT_AUDIT` | `1` включает текстовый смысловой аудит плана и перепись отмеченных слайдов брифа |
| `INFERENCE_MAX_OUTPUT_TOKENS` | Верхняя граница `max_tokens` запроса; `0` (по умолчанию) — лимит задаёт вызывающий этап |
| `BRANDDECK_LIBREOFFICE` | Путь к `soffice.com` (Windows) или `soffice`, если LibreOffice установлен не в стандартное место |
| `BRANDDECK_CPU_MODE` | `1` выполняет тяжёлые задачи (анализ, сборку, экспорт) по одной — для слабого сервера |
| `PREDEL_SECURE_COOKIES` | `1` за HTTPS в публичном развёртывании; `0` для локального HTTP |

Подробности архитектуры и расширения: [docs/architecture.md](docs/architecture.md).
Сравнение с открытыми генераторами и принятые решения: [docs/reference-repo-audit.md](docs/reference-repo-audit.md).

## Ограничения

- **Время.** Лимит ТЗ (5 минут на колоду) подтверждён на GPU: RTX 2070 SUPER 8 ГБ,
  Qwen3.5-9B Q4_K_M в llama.cpp — полный сценарий трёх вариантов с экспортом
  укладывается в пределы из раздела «Результаты». Тот же сценарий на CPU занял
  около 722 с и в лимит не укладывается: без видеокарты модель подходит для
  офлайн-сборки. На VPS 4 ГБ работает только веб-сервис, модель остаётся на
  машине с GPU.
- **Модель.** Проверена локальная Qwen3.5-9B (Apache 2.0). Обязательный для топ-10
  инференс VK с Qwen 27B не запускался: клиент совместим с любым
  OpenAI-совместимым сервером, но время и качество с этой моделью не измерены.
- **Смысловой аудит.** Текстовая модель проверяет план (неподтверждённые
  утверждения, повторы, заголовки-рубрики) и один раз переписывает отмеченные
  слайды брифа. Визуальный аудит итоговых PNG (та же Qwen3.5-9B с проектором
  изображений) отвечает на вопросы Приложения 1 по каждому слайду и проверен
  на настоящей модели, но её ответы снисходительны: заголовок-рубрику она
  иногда отмечает, мелкие дефекты пропускает. Поэтому это подсказки, а не балл;
  в замер 3 × 3 аудит не входит (68–117 с на колоду). Релевантность картинок,
  опечатки и логику колоды перед показом подтверждает человек
  (см. [AUDIT](AUDIT.md), [MODELS](MODELS.md)).
- **Изображения.** В демо используются загруженные картинки. Генерация картинок
  моделью (`IMAGE_*`) проверена только на тестовом сервере; реальная
  text-to-image модель не запускалась.
- **Контент.** В выданном датасете только три шаблона; все демо-колоды собраны
  по синтетическому брифу `examples/brief_feature.md`, а не по официальному
  контент-пакету.
- **Неизвестные шаблоны.** Кроме прежних `mixed.pptx` и quarterly review,
  регрессионно проверены внешние Brutalism и Onocom: по три варианта из одного
  контента на каждом, все прошли QA и проверку различимости
  ([отчёт 28.09.2026](reports/external-regression-2026-09-28/README.md)). Эти два файла уже
  входили в исторический аудит, поэтому результат не заменяет новый слепой
  приёмочный прогон организаторов. В версии 1.1.0 добавлен прогон на двух
  публичных шаблонах Slidesgo и синтетическом шаблоне: он нашёл и закрыл
  дефекты, которые QA = 100 не отмечал (текст образца на финальном слайде,
  мелкие заголовки из-за неполной шкалы кеглей, служебные страницы шаблона
  как макеты, текст под фото) ([отчёт 29.09.2026](reports/unseen-2026-09-29/README.md)). QA = 100 означает отсутствие
  найденных дефектов по списку проверок, а не художественную оценку.
- **Схемы.** Схемы в стиле SmartArt — нативные группы фигур, а не объекты OOXML
  SmartArt: LibreOffice показывает SmartArt только из кэша.
- **Браузеры.** Проверены Chrome 153, Firefox 155 и WebKit 26.6 (Playwright) на Windows
  ([отчёт](reports/browser-2026-09-28/README.md)). Safari на macOS, Яндекс Браузер
  и предыдущие версии браузеров вживую не проверялись. Сборка Vite 8 нацелена на
  `baseline-widely-available` (Chrome и Edge 111+, Firefox 114+, Safari 16.4+;
  Яндекс Браузер построен на Chromium тех же версий), то есть текущие и
  предыдущие версии этих браузеров по синтаксису JavaScript и CSS поддерживаются.
- **Рендер PowerPoint.** Дополнительная проверка настоящим PowerPoint работает
  только на Windows с установленным Office; иначе используется LibreOffice.


MIT. See [LICENSE](LICENSE).
