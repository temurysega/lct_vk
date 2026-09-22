# Отчёт о расширении обучающего набора BrandDeck

Дата: 22 сентября 2026 года. Статус: данные и ноутбук подготовлены; обучение модели не запускалось.

## 1. Результат работы

Подготовлен автономный комплект для Colab A100 с последующим экспортом модели для CPU.
Скачаны и включены 24 новых редактируемых PPTX, отнесённых к 19 новым семействам дизайна.
С тремя исходными шаблонами VK набор содержит 27 файлов, 20 семейств, 496 уникальных
текстовых групп и 1116 строк JSONL. Новые источники добавили 381 уникальную текстовую группу.
Исходный ноутбук сохранён без изменений.

Подтверждённый результат — расширение данных и воспроизводимое разбиение.
Улучшение точности модели пока не измерено: новых обученных весов и метрик нет.

## 2. Исходная проблема

В исходном комплекте train содержал 297 строк, но только 88 уникальных текстовых групп;
validation — 27 групп и 27 строк. Перестановки кандидатов увеличивали число строк,
не создавая независимых примеров. Проверка по имени одного PPTX не учитывала, что
несколько файлов могут происходить из общей дизайн-системы.

Цель: расширить набор за счёт новых семейств и оценивать перенос на целиком отложенные
дизайн-системы. Цветовые версии и производные файлы одной системы должны оставаться
в одной выборке.

## 3. Состав и разбиение

| Выборка | Семейств | Файлов PPTX | Текстовых групп | Строк JSONL |
|---|---:|---:|---:|---:|
| Train | 14 | 16 | 310 | 930 |
| Validation | 3 | 8 | 143 | 143 |
| Test | 3 | 3 | 43 | 43 |
| Всего | 20 | 27 | 496 | 1116 |

Семейство — единица разбиения по происхождению и дизайну. Текстовая группа — уникальный
содержательный пример по правилам извлечения. Строка — пример с конкретным порядком кандидатов.
Число текстовых групп не является числом независимых дизайн-систем.

В train создаются три перестановки каждого примера; в validation/test — одна.
13 повторов внутри семейств удалены до аугментации. Три шаблона VK объединены
в одно семейство и целиком оставлены в train. Поэтому рост train с 88 до 310 групп
включает перенос прежних 27 validation-групп; чистый прирост train от новых источников — 195 групп.
Остальные 186 новых групп находятся в validation/test.

Старые и новые оценки нельзя напрямую сравнивать как результаты на одном holdout:
проверочный набор изменился. В test отложены Heatline Seed Pitch (15 примеров),
Container Shipping Editorial (12) и Fire Control Room Duty Manual (16).

## 4. Источники, лицензии и метод отбора


Источники:
- [PPT Master Examples](https://github.com/hugohe3/ppt-master-examples) — MIT, автор Hugo He;
  отобрано 16 презентаций с разными спецификациями дизайна, один экспорт на проект.
- [wuhua2026/ppt-templates](https://github.com/wuhua2026/ppt-templates) — MIT;
  шесть полных презентаций считаются **одним** семейством, а не шестью системами.
- [onocom](https://github.com/onocom/powerpoint-template) — MIT, Ono Takashi;
  взят демонстрационный файл, двухстраничный вариант не добавляет независимое семейство.
- [Velis](https://github.com/lrkrol/powerpoint) — CC0, Laurens R. Krol.

Исходные лицензии, спецификации дизайна и доказательства происхождения сохранены в `licenses/`.
PPTX скопированы без изменений. Производные JSONL содержат извлечённые тексты,
признаки кандидатов и слабые метки наблюдаемого макета. Лицензии исходников сохраняются.
VK — предоставленные пользователем материалы для его обучения; разрешение на их публичное распространение не заявляется.

Семейства назначены по происхождению и спецификациям, затем объединены при совпадении
хешей файлов, геометрии презентаций без текста/цвета, характерных мастеров или
совпадении не менее трёх компоновок при покрытии >=65% меньшего набора.
Просмотрена контактная таблица схем 27 файлов (`families-schematic.jpg`): она служит
проверкой различий компоновки, не точным рендером PowerPoint.
Пороговый поиск похожих вариантов не доказывает отсутствие любой стилистической близости.
Одинаковые общие примитивы диаграмм сами по себе не считаются целой дизайн-системой.

## 5. Исключённые материалы

- Три проверенных файла ppttemplate.ai: слайды целиком растровые, нет редактируемого текста.
  Остальной каталог не скачивался и не объявляется проверенным.
- 231 одностраничный/цветовой вариант wuhua: не увеличивает число независимых систем;
  для текущей задачи нужны презентации с несколькими кандидатами макетов.
- Двухстраничная версия onocom: вариант уже включённого семейства.
- Два PPT Master примера (researchstudio и brutalism): в спецификациях указано копирование
  внешнего текста/рисунков, права на все вложения отдельно не подтверждены.
- 12 кандидатов Internet Archive: метки лицензий найдены, но связь загрузившего с автором
  и разрешение на включение всех материалов не подтверждены; в учебный комплект не включены.
  В двух случаях скачивание дополнительно не завершилось.
- SciFig: собственная лицензия ограничивает назначение и распространение; не включён.
- LibreOffice Impress: доступные исходники не в PPTX, преобразование не выполнялось.

Поимённые результаты и ссылки: `source_inventory.json`.

## 6. Защита от утечек и сценарий оценки

Разбиение сохраняется в `family_split.json` **до** извлечения/аугментации; seed=20260922.
После объединения семейств случайно выделено по три семейства для validation/test;
известный бренд VK закреплён за train. Общие тексты между выборками удаляются из всех
затронутых частей. Проверка останавливает запуск при пропусках идентификатора семейства
или пересечениях по любой паре частей.

Validation участвует в выборе адаптера; test не входит в Trainer и не меняет решение о
включении LoRA. После GGUF-экспорта test проверяется отдельно базовой моделью и адаптером.
Отчёт `cpu/test_report.json` внутри архива весов содержит accuracy, долю валидных ответов,
результаты по семействам и среднее accuracy с равным весом каждого семейства.
Решение о включении адаптера сохраняется из validation. Не подбирайте настройки по test.

Все 1116 строк проверены токенизатором закреплённой версии Qwen2.5-1.5B-Instruct:
максимум 1117 токенов вместе с ответом, без усечения. Префикс completion-only совпадает.
Локально выполнены тесты пайплайна/регрессий, проверка синтаксиса ячеек, контрольных сумм,
и сохранности оригинального ноутбука. Реальное обучение A100 и GGUF-экспорт не запускались;
готовы данные и исполняемый сценарий, обученные веса и новые модельные метрики не заявляются.

## 7. Выполненные проверки

| Проверка | Результат |
|---|---|
| Тесты репозитория | 76 пройдены, 1 пропущен |
| Повторная сборка | JSONL, разбиение и аудит дедупликации побайтно совпадают |
| Пересечения выборок | Нет по design_family, template_sha256 и group |
| Проверка токенизатором Qwen | 1116 строк, максимум 1117 токенов; усечения нет |
| Синтаксис ячеек | Проверен |
| Контрольные суммы комплекта | 104 файла совпадают с manifest |
| Архив | 109 файлов; контрольная сумма проверена |
| Исходный ноутбук | SHA256 совпадает с сохранённым исходным значением |
| Обучение A100 / реальный GGUF-экспорт | Не запускались |

Состояние файлов и отсутствие пересечений повторно проверены при сборке этого отчёта.
Статистика тестов взята из сохранённого результата проверки кода; тесты ради оформления
отчёта повторно не запускались.

## 8. Ограничения

22 из 24 новых файлов созданы программно/ИИ, 16 — одним генератором;
20 дизайн-систем не означают 20 независимых авторов. В test всего три семейства.
Слабая метка — исходный наблюдаемый макет, не предпочтение эксперта; метрики не доказывают
визуальное качество презентаций. Новые данные преимущественно китайские/английские;
русский материал представлен VK. Для дальнейшей оценки полезен отдельный русский
корпоративный набор от других авторов с подтверждёнными правами.


## 9. Передаваемые результаты и запуск

- `lct_cpu_expanded.zip` — полный комплект, 111 875 260 байт (около 112 МБ).
- `lct_cpu_expanded/BrandDeck_CPU_Expanded_Training_Export.ipynb` — новый ноутбук.
- `data/train.jsonl`, `validation.jsonl`, `test.jsonl` — подготовленные выборки.
- `data/source_inventory.json` — источники, лицензии, хеши и исключения.
- `data/family_split.json`, `dedup_audit.json`, `token_length_audit.json` — аудиты.
- `templates/` — включённые PPTX; `licenses/` — сохранённые сведения о происхождении.
- Изменения пайплайна и тесты — в репозитории `vk/lct_vk`.

Распаковать архив, загрузить папку `lct_cpu_expanded` целиком в Google Drive / MyDrive.
Открыть новый ноутбук в Colab A100 и выполнить ячейки по порядку. Использовать отдельную
папку для новых данных и чекпоинтов. После обучения и экспорта итоговая проверка новых
семейств появится в `cpu/test_report.json` внутри архива весов.

SHA256 передаваемого архива:

`ddddc83c3f958b710e812acf871b022f754bc5631aa3ea486b43a898095b0dbe`

## 10. Следующий этап

Запустить обучение с зафиксированным разбиением; сравнить адаптер с базовой моделью
и эвристикой на validation. После фиксации параметров выполнить test и отдельно
зафиксировать среднее качество по семействам. Просмотреть сгенерированные PPTX вручную:
слабая метка выбора макета не заменяет оценку визуального качества.

Для дальнейшего расширения нужны прежде всего русскоязычные корпоративные шаблоны
от новых авторов с подтверждёнными правами. Новый материал следует группировать до
аугментации и добавлять без подбора разбиения под полученные метрики.

## Приложение. Реестр включённых файлов

| Файл | Семейство | Выборка | Слайдов с примерами | Лицензия |
|---|---|---|---:|---|
| [onocom-powerpoint-template-sample.pptx](https://github.com/onocom/powerpoint-template/blob/2352e3fe338cd13b9a34cac9e824b2c4040c1c7a/powerpoint-template-sample.pptx) | onocom | train | 9 | [лицензия](https://opensource.org/license/mit) |
| [lrkrol-lrk-slides-velis-sample.pptx](https://github.com/lrkrol/powerpoint/blob/0f18f3f1fe2d76413c45b0106e7585d64beb920d/docs/lrk-slides-velis-sample.pptx) | lrkrol | train | 13 | [лицензия](https://creativecommons.org/publicdomain/zero/1.0/) |
| [wuhua2026-annual_report.pptx](https://github.com/wuhua2026/ppt-templates/blob/f0e14a5621764c605d0c5027bfab172ef1d1eb0f/templates/complete/annual_report.pptx) | wuhua2026 | validation | 22 | [лицензия](https://opensource.org/license/mit) |
| [wuhua2026-business_plan.pptx](https://github.com/wuhua2026/ppt-templates/blob/f0e14a5621764c605d0c5027bfab172ef1d1eb0f/templates/complete/business_plan.pptx) | wuhua2026 | validation | 18 | [лицензия](https://opensource.org/license/mit) |
| [wuhua2026-career_planning.pptx](https://github.com/wuhua2026/ppt-templates/blob/f0e14a5621764c605d0c5027bfab172ef1d1eb0f/templates/complete/career_planning.pptx) | wuhua2026 | validation | 16 | [лицензия](https://opensource.org/license/mit) |
| [wuhua2026-education_course.pptx](https://github.com/wuhua2026/ppt-templates/blob/f0e14a5621764c605d0c5027bfab172ef1d1eb0f/templates/complete/education_course.pptx) | wuhua2026 | validation | 18 | [лицензия](https://opensource.org/license/mit) |
| [wuhua2026-product_launch.pptx](https://github.com/wuhua2026/ppt-templates/blob/f0e14a5621764c605d0c5027bfab172ef1d1eb0f/templates/complete/product_launch.pptx) | wuhua2026 | validation | 20 | [лицензия](https://opensource.org/license/mit) |
| [wuhua2026-technology_theme.pptx](https://github.com/wuhua2026/ppt-templates/blob/f0e14a5621764c605d0c5027bfab172ef1d1eb0f/templates/complete/technology_theme.pptx) | wuhua2026 | validation | 16 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-glassmorphism_demo.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_glassmorphism_demo/) | pptmaster-glassmorphism_demo | train | 12 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-indie_bookstore_zine_guide.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_indie_bookstore_zine_guide/) | pptmaster-indie_bookstore_zine_guide | train | 18 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-how_color_works_workshop.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_how_color_works_workshop/) | pptmaster-how_color_works_workshop | train | 14 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-heatline_seed_pitch.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_heatline_seed_pitch/) | pptmaster-heatline_seed_pitch | test | 15 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-container_shipping_editorial.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_container_shipping_editorial/) | pptmaster-container_shipping_editorial | test | 12 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-night_shelf_creative_pitch.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_night_shelf_creative_pitch/) | pptmaster-night_shelf_creative_pitch | train | 13 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-pixel_breakfast_atlas.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_pixel_breakfast_atlas/) | pptmaster-pixel_breakfast_atlas | train | 16 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-sugar_rush_memphis.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_sugar_rush_memphis/) | pptmaster-sugar_rush_memphis | train | 14 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-swiss_grid_systems.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_swiss_grid_systems/) | pptmaster-swiss_grid_systems | train | 14 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-teachers_day_chalkboard.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_teachers_day_chalkboard/) | pptmaster-teachers_day_chalkboard | train | 13 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-every_day_counts_proposal.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_every_day_counts_proposal/) | pptmaster-every_day_counts_proposal | train | 14 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-fire_control_room_duty_manual.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_fire_control_room_duty_manual/) | pptmaster-fire_control_room_duty_manual | test | 16 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-solar_terms_year.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_solar_terms_year/) | pptmaster-solar_terms_year | train | 31 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-qianli_jiangshan_scroll.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_qianli_jiangshan_scroll/) | pptmaster-qianli_jiangshan_scroll | train | 14 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-mid_autumn_papercut.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_mid_autumn_papercut/) | pptmaster-mid_autumn_papercut | validation | 16 | [лицензия](https://opensource.org/license/mit) |
| [pptmaster-what_is_ppt.pptx](https://github.com/hugohe3/ppt-master-examples/tree/4513898f2a64c73db006dddaa4cbd525d95890f1/examples/ppt169_what_is_ppt/) | pptmaster-what_is_ppt | validation | 17 | [лицензия](https://opensource.org/license/mit) |
| Шаблон презентации VK Education.pptx | vk-brand | train | 45 | Материалы пользователя; публичное распространение не подтверждено |
| VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx | vk-brand | train | 29 | Материалы пользователя; публичное распространение не подтверждено |
| VK Tech шаблон.pptx | vk-brand | train | 54 | Материалы пользователя; публичное распространение не подтверждено |
