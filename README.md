# BrandDeck AI

Open-source сервис, который анализирует произвольный `.pptx`- или `.pdf`-шаблон и создаёт новую презентацию в его стиле. Система извлекает дизайн-токены и паттерны, планирует структуру через OpenAI-compatible Inference API, выбирает подходящий реальный layout для каждого смыслового блока, собирает PowerPoint и запускает автоматический QA.

В основе лежит [anyideaz/pptx-skills](https://github.com/anyideaz/pptx-skills). Исходные парсер OOXML, промпты и совместимый PPTXGenJS-раннер сохранены; поверх них добавлен автономный Python-сервис, которому не нужны внешние ИИ-агенты или Node.js.

## Что уже работает

- Анализ masters, layouts, placeholders, геометрии, шрифтов, цветов, фонов, таблиц и изображений.
- Формирование `design_system.json` и `pattern_catalog.json`, пригодных для повторного использования.
- Контент-планирование через любой OpenAI-compatible `/chat/completions` endpoint.
- Детерминированный локальный planner для работы без API и CI-тестов.
- Семантический выбор паттерна под cover, section, content, comparison, data, image и closing.
- Ёмкостный layout mapping по геометрии текста, числу слотов, данным и изображениям с `why_fit`, рисками и альтернативами в `deck_plan.json`.
- Два режима композиции: semantic layouts/exemplars для нормальных PPTX и `native_grid` для PDF-конверсий, где каждый слайд заново собирается из редактируемых объектов по извлечённой сетке.
- Прямой приём PDF в CLI и REST API с фильтрацией служебных масок и ограничением декоративных PDF-фрагментов.
- Удаление уникального исходного контента (скриншотов, клиентских логотипов и фото) с сохранением повторяющегося брендинга и полноэкранных фонов.
- Генерация текста, таблиц, bar/pie charts, metric cards и timeline.
- Проверка целостности OOXML, числа слайдов, границ элементов, переполнения, пересечений текста и шрифтовой консистентности; в Windows результат дополнительно рендерится настоящим PowerPoint и возвращается в QA-feedback loop.
- CLI, REST API и Docker.
- Асинхронные persistent jobs с загрузкой контентного файла, стадиями, прогрессом и отдельным download endpoint.

## Быстрый старт

Требования: Python 3.10+.

```powershell
python -m pip install -e ".[api]"
branddeck --json doctor
```

### Веб-интерфейс и три варианта

Для предпросмотра, PDF и HTML установите **LibreOffice** и зависимости экспорта:

```powershell
python -m pip install -e ".[api,export]"
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
python -m slide_agent variants --template "..\данные\VK Tech шаблон.pptx" --content examples/baseline_content.md --slides 10 --offline --export all
```

Воспроизводимый прогон **3 шаблона × 3 варианта**:

```powershell
python examples/run_dataset.py --config examples/demo.json
```

Пути в конфиге задаются относительно корня репозитория. По умолчанию используются
файлы из `../данные` и **синтетический** контент из `examples/baseline_content.md`.
Официальный контент-пакет организаторов нужно указать в `content`.
Результаты: `slide-workspace/dataset/index.html`, `dataset_report.json`, а также
PPTX/PDF/HTML, изображения и отчёты каждой презентации. Для просмотра тех же
шаблонов в веб-интерфейсе запустите `python -m slide_agent --workspace slide-workspace/dataset serve`.

PDF и HTML строятся из реального рендера PPTX через LibreOffice. Сам PPTX содержит
редактируемые объекты. HTML — автономный просмотр с SVG-страницами, а не редактор.
Установите шрифты шаблона в системе: при отсутствии шрифта LibreOffice заменит его,
что может изменить переносы строк. Ошибка экспорта и проваленный QA не считаются
успешной задачей; CLI возвращает ненулевой код, API сохраняет статус `failed`.

Ось вариантов — **выбор макетов**: `balanced` выбирает по соответствию содержанию,
`columns` предпочитает несколько текстовых блоков, `focus` — один крупный блок.
Повторение макетов между вариантами мягко штрафуется; текст не переписывается.
При недостатке разных макетов отчёт содержит `diversity_status=needs_review`.
Разные последовательности макетов сами по себе не доказывают визуальное качество.

Документация сдачи: [ARCHITECTURE](ARCHITECTURE.md), [MODELS](MODELS.md), [AUDIT](AUDIT.md).

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

```bash
curl -F "file=@brand.pptx" -F "name=corporate" \
  http://localhost:8000/v1/templates/analyze

curl -F "template_id=corporate-<hash>" \
  -F "content=Создай отчёт о результатах квартала..." \
  -F "slide_count=8" \
  http://localhost:8000/v1/presentations/generate

curl -o result.pptx \
  http://localhost:8000/v1/presentations/<presentation-id>/download
```

Для долгой генерации и загрузки документа:

```bash
curl -F "template_id=corporate-<hash>" \
  -F "content_file=@content.md" \
  -F "slide_count=8" \
  http://localhost:8000/v1/presentations/jobs

curl http://localhost:8000/v1/jobs/<job-id>
curl -o result.pptx http://localhost:8000/v1/jobs/<job-id>/download
```

Асинхронные записи сохраняются в `slide-workspace/jobs/`. Текущая реализация
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
PDF успешным экспортом. Для Linux/Colab предусмотрен экспорт через LibreOffice;
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

### Проверка на VK Tech

Текущий основной набор — три PPTX из папки `данные`; команда прогона приведена
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

Для аккаунта с A100 **без GitHub** есть отдельный
[notebook обучения через Google Drive](examples/BrandDeck_A100_Training_Drive.ipynb).
`python -m training.prepare_drive` собирает `../gdrive/lct`: три шаблона, JSONL,
скрипт QLoRA и notebook. Загрузите папку в «Мой диск/lct» и откройте notebook
через загрузку файла в Colab. [Порядок запуска и расположение весов](training/DRIVE_README.md).
Обучается экспериментальный выбор макетов; базовая модель планирования текста
остаётся отдельной. Реальный GPU-прогон пока не выполнен.

Если доступна только A100 в Colab, откройте [готовый notebook](examples/BrandDeck_A100_Inference.ipynb).
Он запускает Qwen2.5-VL-7B-Instruct и выдаёт адрес для `INFERENCE_BASE_URL`.
OpenRouter не обязателен. GPU-запуск ещё не проверен; notebook содержит проверку
загрузки модели, ответа API и обязательности ключа. См. [MODELS](MODELS.md).

| Переменная | Назначение |
|---|---|
| `INFERENCE_BASE_URL` | Base URL, например `https://host/v1`, либо полный URL `/chat/completions` |
| `INFERENCE_API_KEY` | Bearer token; может быть пустым для локального endpoint |
| `INFERENCE_MODEL` | Идентификатор модели |
| `INFERENCE_LAYOUT_MODEL` | Необязательный LoRA выбора макетов, например `lct-layout`; пустое значение оставляет эвристики |
| `INFERENCE_TIMEOUT` | Таймаут запроса, по умолчанию 120 секунд |
| `INFERENCE_MAX_RETRIES` | Число сетевых попыток, по умолчанию 3 |
| `INFERENCE_VISION` | `1` включает multimodal-анализ PNG-превью, `0` оставляет только JSON |
| `BRANDDECK_WORKSPACE` | Альтернативная папка артефактов |
| `BRANDDECK_POWERPOINT_QA` | `auto` по умолчанию; `1` требует PowerPoint render-check в Windows, `0` отключает его |

Подробности архитектуры и расширения: [docs/architecture.md](docs/architecture.md).
Сравнение с открытыми генераторами и принятые решения: [docs/reference-repo-audit.md](docs/reference-repo-audit.md).

## English

BrandDeck AI analyzes any PowerPoint template, classifies semantic versus PDF-fragmented sources, extracts a design system and reusable patterns, plans a new narrative through an OpenAI-compatible Inference API, composes editable native slides, and validates the result with structural QA and an optional real PowerPoint render loop. Run `branddeck --help` for CLI usage or `branddeck serve` for the REST API.

## License

MIT. See [LICENSE](LICENSE).
