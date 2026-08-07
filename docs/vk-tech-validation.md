# Проверка на материалах VK Tech

## Что использовано

Публичные материалы не включаются в git и загружаются только по команде. Это
позволяет воспроизвести проверку, не переиздавая чужие презентации в
open-source репозитории.

| Набор | Публичный источник | Исходник | SHA-256 PDF | Локальная модель шаблона |
|---|---|---:|---|---|
| «ПАК как опорная точка цифрового суверенитета», Станислав Погоржельский, VK Cloud / VK Tech | [программа форума «ЦОД»](https://spb.dcforum.ru/archive/programm?day=1), [PDF](https://spb.dcforum.ru/sites/default/files/spb/15.00-15.20_pogorzhelskiy_2025_17_06_ivent_cod_v_spb_v2_short-1.pdf) | 28 страниц | `9cdcf1bed51d20f48a5d0742e4d2b6b317a7e6f4169875e132144d50bc071f9b` | 28 слайдов, 733 vector shape, 123 изображения, 352 текстовых блока |
| «Как сделать линейку тренингов на основе матрицы компетенций», Павел Лапаев и Владислав Грищенко, VK Tech | [конференция «Цифровое образование. XXI век»](https://edu-forum.pro/), [публичный файл](https://disk.360.yandex.ru/i/aKyEeARywFax5g) | 12 страниц | `28909792dece370a12817475c30ae4486734f9231e7d0da70ab1863f2286d73f` | 12 слайдов, 22 vector shape, 34 изображения, 78 текстовых блоков |

Публично доступны PDF, а не свободно скачиваемые исходные PPTX. Скрипт
`fetch_vk_tech_references.py` поэтому преобразует каждую PDF-страницу в
редактируемый PPTX: текст остаётся текстом, прямоугольники — native shapes,
растровые элементы — картинками. Это тестовая модель реального визуального
шаблона, а не утверждение, что полученный PPTX является оригиналом автора.

## Воспроизведение

```powershell
python -m pip install -e ".[dev,pdf-reference]"
python .\examples\fetch_vk_tech_references.py

branddeck --workspace .\external-fixtures\vk-tech\workspace run `
  --template .\external-fixtures\vk-tech\vk-tech-private-cloud-2025.pptx `
  --content .\examples\vk_tech_case_content.md `
  --slides 10 --offline `
  --output .\external-fixtures\vk-tech\generated-private-cloud-improved.pptx

branddeck --workspace .\external-fixtures\vk-tech\workspace run `
  --template .\external-fixtures\vk-tech\vk-tech-training-2026.pptx `
  --content .\examples\vk_tech_case_content.md `
  --slides 10 --offline `
  --output .\external-fixtures\vk-tech\generated-training-improved.pptx
```

Удалите `--offline` и задайте `INFERENCE_BASE_URL`, `INFERENCE_API_KEY` и
`INFERENCE_MODEL`, чтобы проверить тот же pipeline через Inference API.

## Фактический результат проверки

| Метрика | VK Cloud / VK Tech | VK Tech training |
|---|---:|---:|
| Извлечено layout + slide-exemplar patterns | 39 | 23 |
| Сгенерировано слайдов | 10 | 10 |
| Использовано разных exemplar patterns | 5 | 6 |
| QA | `passed`, 100/100 | `passed`, 100/100 |
| Число попыток | 1 | 1 |
| Размер результата | 287 934 байта | 86 039 байт |
| SHA-256 результата | `61d3b7341f317536a8c180b4a695138b5f92fe8cf816ad8db74f9c380c9bc610` | `4af911cdcea5b1fca4b7b81b48ccf30250ca626ebfafb9405770f24fd06eaceb` |

Повтор layouts здесь намеренный: ёмкостный selector независимо ранжирует все
кандидаты и повторяет наиболее подходящий exemplar вместо последовательного
обхода исходной презентации. Для первого результата выбраны паттерны
`slide-1`, `slide-12`, `slide-21`, `slide-23`, `slide-28`; для второго —
`slide-1`, `slide-2`, `slide-3`, `slide-5`, `slide-10`, `slide-12`.

Визуальная приёмка стала частью доработки: ранний прогон структурно получал
100/100, но сохранял логотипы «АВТОВАЗ»/GitLab, фото исходного спикера и один
обрезанный текстовый блок. После разделения повторяющихся брендовых assets и
уникального исходного контента, явного text fitting и проверки пересечений эти
дефекты устранены. Все 20 финальных PNG-превью просмотрены повторно.

Первый анализ выделил семейства `VKSansDisplay`,
`VKSansDisplay-DemiBold`, `VKSansDisplay-Light` и
`VKSansDisplay-Medium`; они сохранились в созданном PPTX. Во втором наборе
система независимо воспроизвела Arial, зелёный фон, светло-розовый текст и
золотые акценты. Различие двух результатов подтверждает, что стиль не зашит
в генератор.

QA проверяет ZIP/OOXML, число слайдов, границы, переполнение, пересечения текста
и шрифты.
Диагностический renderer дополнительно строит PNG из реальных изображений,
vector shapes и текста; в acceptance-прогоне просмотрены все 10 слайдов обоих
результатов. Выход элементов за canvas отмечен только как `template_bleed`,
поскольку это намеренные полноэкранные фоновые изображения исходных макетов.

Проверка здесь выполнена в offline-режиме: учётные данные организаторского
Inference API в рабочей папке отсутствуют. Контракт OpenAI-compatible
`/chat/completions` покрыт отдельным mock-тестом; для финальной приёмки следует
повторить те же две команды с выданным endpoint и оригинальными PPTX
организаторов.
