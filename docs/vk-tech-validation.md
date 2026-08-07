# Проверка на материалах VK Tech

## Статус референсов

Публичные материалы не включаются в git и загружаются только командой
`examples/fetch_vk_tech_references.py`. Публично доступны PDF, поэтому локальные
PPTX — это тестовые конверсии, а не оригинальные авторские шаблоны.

| Набор | Назначение проверки | Публичный источник | Исходник |
|---|---|---|---:|
| «ПАК как опорная точка цифрового суверенитета», Станислав Погоржельский, VK Cloud / VK Tech | Основная приёмка корпоративной визуальной системы VK Tech | [программа форума «ЦОД»](https://spb.dcforum.ru/archive/programm?day=1), [PDF](https://spb.dcforum.ru/sites/default/files/spb/15.00-15.20_pogorzhelskiy_2025_17_06_ivent_cod_v_spb_v2_short-1.pdf) | 28 страниц |
| «Как сделать линейку тренингов на основе матрицы компетенций», Павел Лапаев и Владислав Грищенко | Независимый stress-test адаптивности | [конференция «Цифровое образование. XXI век»](https://edu-forum.pro/), [публичный файл](https://disk.360.yandex.ru/i/aKyEeARywFax5g) | 12 страниц |

Второй набор не является корпоративным VK Tech-шаблоном. Это визуальная система
конференции «Цифровое образование. XXI век», где выступали представители VK Tech.
Она намеренно не используется как доказательство воспроизведения фирменного
стиля VK Tech.

## Почему прежний метод был неверным

PDF-конверсия создаёт сотни независимых `PDF text`, `PDF vector` и `PDF image`
объектов без нормальных placeholders. Клонирование такого слайда и подстановка
нового текста сохраняют фрагменты, но не композиционную логику: текст обрезается,
появляются пустые блоки, а сетка распадается.

Анализатор теперь измеряет долю PDF-фрагментов и присваивает таким источникам
`source_model.composition_mode = native_grid`. Для них исходные text/vector
fragments не переносятся. Из примеров извлекаются:

- фон, основные/акцентные цвета и контраст;
- основная и заголовочная гарнитуры, безопасные размеры текста;
- поля и доступная контентная область;
- повторяющиеся логотипы, фоновые и декоративные изображения;
- семантические паттерны cover, cards, list, split и closing.

Composer начинает с чистого layout и заново создаёт редактируемые PowerPoint
objects. QA отдельно запрещает утечку фигур с именами `PDF *`.

## Воспроизведение

```powershell
python -m pip install -e ".[dev,pdf-reference]"
python .\examples\fetch_vk_tech_references.py
$env:BRANDDECK_POWERPOINT_QA="1"

branddeck --workspace .\external-fixtures\vk-tech\workspace run `
  --template .\external-fixtures\vk-tech\vk-tech-private-cloud-2025.pptx `
  --content .\examples\vk_tech_case_content.md `
  --slides 10 --offline `
  --output .\external-fixtures\vk-tech\generated-private-cloud-native.pptx

branddeck --workspace .\external-fixtures\vk-tech\workspace run `
  --template .\external-fixtures\vk-tech\vk-tech-training-2026.pptx `
  --content .\examples\vk_tech_case_content.md `
  --slides 10 --offline `
  --output .\external-fixtures\vk-tech\generated-training-event-native.pptx
```

Удалите `--offline` и задайте `INFERENCE_BASE_URL`, `INFERENCE_API_KEY` и
`INFERENCE_MODEL`, чтобы прогнать тот же pipeline через организаторский
Inference API.

## Фактический результат 7 августа 2026 года

| Метрика | Корпоративный VK Cloud / VK Tech | Конференционный stress-test |
|---|---:|---:|
| Классификация источника | `fragmented`, PDF ratio `1.0` | `fragmented`, PDF ratio `1.0` |
| Режим композиции | `native_grid` | `native_grid` |
| Сгенерировано слайдов | 10 | 10 |
| Нативные паттерны | cover, cards, list, split, closing | cover, cards, list, split, closing |
| Утечки `PDF *` shapes | 0 | 0 |
| Structural QA | `passed`, 100/100 | `passed`, 100/100 |
| PowerPoint COM render | 10/10, `passed` | 10/10, `passed` |
| Число попыток | 1 | 1 |
| Размер результата | 66 025 байт | 62 882 байт |
| SHA-256 результата | `0cb54f75c22a847cfa93046fa1bfe78ae3b192c038b4d7bc343ff0b8213b5280` | `062da538e6be7623b43a893264cacad78000757d142f00defbdc950dfe14cf40` |

Корпоративный результат сохранил тёмный сине-чёрный фон, VK tech logo,
`VKSansDisplay-Light` для основного текста, `VKSansDisplay-Medium` для
заголовков и cyan `#00D3E3` как акцент. Конференционный результат независимо
воспроизвёл зелёный фон, Arial, светло-розовую типографику, золотой акцент,
эмблему события и спиральный декор. Это различие подтверждает, что стиль
извлекается из текущего источника, а не зашит в renderer.

`powerpoint.py` открывает итоговый PPTX через установленный Microsoft PowerPoint,
экспортирует каждый слайд в PNG 1600×900 и проверяет число кадров и визуально
пустые рендеры. Результат включается в `qa_report.json` до решения о повторной
сборке. Намеренный bleed повторяющихся брендовых изображений остаётся
информационным событием и не запускает ложный retry.

Проверка выполнена в offline-режиме, потому что учётные данные организаторского
Inference API в рабочей папке отсутствуют. Контракт OpenAI-compatible
`/chat/completions` покрыт отдельным mock-тестом.
