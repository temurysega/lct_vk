# BrandDeck AI

Open-source сервис, который анализирует произвольный `.pptx`-шаблон и создаёт новую презентацию в его стиле. Система извлекает дизайн-токены и паттерны, планирует структуру через OpenAI-compatible Inference API, выбирает подходящий реальный layout для каждого смыслового блока, собирает PowerPoint и запускает автоматический QA.

В основе лежит [anyideaz/pptx-skills](https://github.com/anyideaz/pptx-skills). Исходные парсер OOXML, промпты и совместимый PPTXGenJS-раннер сохранены; поверх них добавлен автономный Python-сервис, которому не нужны внешние ИИ-агенты или Node.js.

## Что уже работает

- Анализ masters, layouts, placeholders, геометрии, шрифтов, цветов, фонов, таблиц и изображений.
- Формирование `design_system.json` и `pattern_catalog.json`, пригодных для повторного использования.
- Контент-планирование через любой OpenAI-compatible `/chat/completions` endpoint.
- Детерминированный локальный planner для работы без API и CI-тестов.
- Семантический выбор паттерна под cover, section, content, comparison, data, image и closing.
- Ёмкостный layout mapping по геометрии текста, числу слотов, данным и изображениям с `why_fit`, рисками и альтернативами в `deck_plan.json`.
- Повторное использование исходных masters/layouts и клонирование репрезентативных слайдов вместе с media relationships.
- Удаление уникального исходного контента (скриншотов, клиентских логотипов и фото) с сохранением повторяющегося брендинга и полноэкранных фонов.
- Генерация текста, таблиц, bar/pie charts, metric cards и timeline.
- Проверка целостности OOXML, числа слайдов, границ элементов, переполнения, пересечений текста и шрифтовой консистентности; QA-feedback меняет layout при повторной сборке.
- CLI, REST API и Docker.
- Асинхронные persistent jobs с загрузкой контентного файла, стадиями, прогрессом и отдельным download endpoint.

## Быстрый старт

Требования: Python 3.10+.

```powershell
python -m pip install -e ".[api]"
branddeck --json doctor
```

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
PPTX template
   ├─ OOXML parser ──> context.json
   ├─ token extractor ──> design_system.json
   └─ pattern miner ──> pattern_catalog.json
                              │
Content ──> LLM planner ──> capacity-aware pattern scoring
                              │
                              v
              original masters/layouts + cloned examples
                              │
                              v
                    output.pptx ──> QA report ──> retry mapping
```

Здесь нет заранее зашитого фирменного шаблона: папка анализа имеет content-addressed id, а каждый слайд плана явно связан с layout/master текущего PPTX. Декор, изображения, фоны и отношения внутри OOXML переносятся из выбранного примера. Создаваемые визуализации используют извлечённые шрифты и theme colors.

## Артефакты

```text
slide-workspace/
  templates/{name}-{sha256[:8]}/
    original.pptx
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
    manifest.json
```

## Demo и тесты

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

Публичные референсы VK Tech загружаются по исходным ссылкам и локально
преобразуются в редактируемые PPTX-фикстуры:

```powershell
python -m pip install -e ".[dev,pdf-reference]"
python .\examples\fetch_vk_tech_references.py

branddeck --workspace .\external-fixtures\vk-tech\workspace run `
  --template .\external-fixtures\vk-tech\vk-tech-private-cloud-2025.pptx `
  --content .\examples\vk_tech_case_content.md `
  --slides 10 --offline `
  --output .\external-fixtures\vk-tech\generated-private-cloud-improved.pptx
```

Проверены два независимых стиля на 28- и 12-слайдовых материалах. Оба
10-слайдовых результата прошли QA с оценкой 100/100; облачный deck сохранил
семейство VK Sans и цианово-тёмную палитру, учебный — зелёно-золотую систему.
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
| `INFERENCE_VISION` | `1` включает multimodal-анализ PNG-превью, `0` оставляет только JSON |
| `BRANDDECK_WORKSPACE` | Альтернативная папка артефактов |

Подробности архитектуры и расширения: [docs/architecture.md](docs/architecture.md).
Сравнение с открытыми генераторами и принятые решения: [docs/reference-repo-audit.md](docs/reference-repo-audit.md).

## English

BrandDeck AI analyzes any PowerPoint template, extracts its design system and reusable slide patterns, plans a new narrative through an OpenAI-compatible Inference API, composes a native `.pptx` from the original masters/layouts, and validates the result. Run `branddeck --help` for CLI usage or `branddeck serve` for the REST API.

## License

MIT. See [LICENSE](LICENSE).
