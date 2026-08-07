# Проверка на материалах VK Tech

## Референсы и границы проверки

Публичные материалы не включаются в git. Скрипт
`examples/fetch_vk_tech_references.py` загружает их по исходным ссылкам и
создаёт локальные fixtures для воспроизводимой проверки.

| Набор | Назначение | Публичный источник |
|---|---|---|
| «VK Tech — ведущий российский разработчик корпоративного ПО» | Основная приёмка синей корпоративной визуальной системы | [страница презентации](https://ppt-online.org/1691195), 33 публичных изображения слайдов |
| «ПАК как опорная точка цифрового суверенитета», Станислав Погоржельский | Дополнительный stress-test тёмной продуктовой системы VK Cloud / VK Tech | [программа форума «ЦОД»](https://spb.dcforum.ru/archive/programm?day=1), [PDF](https://spb.dcforum.ru/sites/default/files/spb/15.00-15.20_pogorzhelskiy_2025_17_06_ivent_cod_v_spb_v2_short-1.pdf) |
| «Как сделать линейку тренингов на основе матрицы компетенций» | Независимый stress-test адаптивности | [конференция «Цифровое образование. XXI век»](https://edu-forum.pro/), [публичный файл](https://disk.360.yandex.ru/i/aKyEeARywFax5g) |

Тёмный спикерский deck не считается основным корпоративным VK Tech-шаблоном.
Второй training deck относится к визуальной системе конференции, где выступали
представители VK Tech, и также не используется как доказательство фирменного
стиля VK Tech.

## Исправленная модель композиции

Прежняя реализация переносила повторяющиеся крупные изображения как обычные
PowerPoint shapes. Поэтому фон и декоративная иллюстрация площадью более половины
холста оказывались selectable-слоями поверх слайда.

Теперь источники классифицируются по двум независимым признакам:

- `fragmented`: PDF-конверсия состоит из несвязанных `PDF text`, `PDF vector` и
  `PDF image` объектов;
- `flattened`: большинство слайдов представлено полноэкранным растром без
  семантического текста.

Оба класса переводятся в `source_model.composition_mode = native_grid`.
Анализатор извлекает палитру из растров, отдельно определяет цвета обложки и
контентных слайдов, выбирает контрастную типографику и формирует нативные
паттерны cover, cards, list, split и closing.

Composer переносит только небольшие повторяющиеся brand assets. Полноэкранный
растр при необходимости записывается в настоящий OOXML `<p:bg>`, а крупные
иллюстрации исходного контента не переносятся. QA запрещает `PDF *` fragments и
selectable background shapes.

## Воспроизведение основной проверки

```powershell
python -m pip install -e ".[dev,pdf-reference]"
python .\examples\fetch_vk_tech_references.py --only vk-tech-blue-corporate
$env:BRANDDECK_POWERPOINT_QA="1"

branddeck --workspace .\external-fixtures\vk-tech\blue-workspace run `
  --template .\external-fixtures\vk-tech\vk-tech-blue-corporate.pptx `
  --content .\examples\vk_tech_case_content.md `
  --slides 10 --offline `
  --output .\external-fixtures\vk-tech\generated-vk-tech-blue-native.pptx
```

Удалите `--offline` и задайте `INFERENCE_BASE_URL`, `INFERENCE_API_KEY` и
`INFERENCE_MODEL`, чтобы использовать организаторский Inference API.

## Фактический результат 7 августа 2026 года

| Метрика | Синий корпоративный референс | Тёмный дополнительный stress-test |
|---|---:|---:|
| Использовано исходных примеров | 6 | 28 |
| Классификация | `flattened`, ratio `1.0` | `fragmented`, PDF ratio `1.0` |
| Режим композиции | `native_grid` | `native_grid` |
| Сгенерировано слайдов | 10 | 10 |
| Нативные паттерны | cover, cards, list, split, closing | cover, cards, list, split, closing |
| Полноэкранные picture shapes | 0 | 0 |
| Structural QA | `passed`, 100/100 | `passed`, 100/100 |
| PowerPoint COM render | 10/10, `passed` | 10/10, `passed` |
| Число попыток | 1 | 1 |
| Размер результата | 58 009 байт | 76 244 байта |
| SHA-256 | `776b498c307caa51685468d39b339596273e795501c1ae3aef0e2f64a460a17d` | `19a85c7b41bdb329db48435f995757da61081ec07a4be6ed58a9afbe278d3635` |

Для синего набора автоматически извлечены тёмно-синий фон обложки `#091624`,
светло-голубой контентный фон `#DCE6F2` и основной синий акцент `#1C76DE`.
В generated PPTX на каждом слайде остаётся только небольшой wordmark; весь текст,
карточки, списки, линии и cover motif являются редактируемыми нативными объектами.

`powerpoint.py` открывает итоговый PPTX через установленный Microsoft PowerPoint,
экспортирует все слайды в PNG 1600×900 и включает результат в QA feedback loop.
Проверка выполнена в offline-режиме, потому что учётные данные организаторского
Inference API в рабочей папке отсутствуют. Контракт OpenAI-compatible
`/chat/completions` покрыт отдельным mock-тестом.
