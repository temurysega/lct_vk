# Проверка двух внешних шаблонов — 28.09.2026

Прогон на ревизии `6942c7f0a9b4ec1397a6ea2835e595ebb4b9eecd`: два PPTX,
по три варианта и десять слайдов. Вход — готовый текст
[`examples/acceptance_content.md`](../../examples/acceptance_content.md) и изображения
из `examples/acceptance_images/`; генерация текста моделью отключена. Экспорт
PPTX, PDF и HTML прошёл во всех шести вариантах. Во всех вариантах размещено
по одному подходящему изображению.

| Шаблон | SHA-256 входного PPTX | Время на три варианта | QA balanced / columns / focus | Различия вариантов |
|---|---|---:|---|---|
| Onocom | `4bbb072ce87a0d82ebe1c7fcf63c694bc3c131cc1b904cddc242de3b544d88df` | 37,2 с | 100 / 100 / 100 | пройдены |
| Brutalism | `5c41309bddb02ddc09c3fcf0591dbf5d4f24648493bc1a72b6d45adefa6dcaa2` | 73,0 с | 100 / 100 / 100 | пройдены |

Рендеры сбалансированного варианта: [Onocom](onocom-powerpoint-template-balanced.png),
[Brutalism](pptmaster-brutalism_field_guide-balanced.png). Это регрессионные
шаблоны из [предыдущего аудита](../criteria-audit/ARTIFACTS.md), а не новый
скрытый шаблон организаторов. QA оценивает заданные структурные и рендерные
проверки; обзорные изображения позволяют отдельно оценить текст и дизайн.

Исходные внешние PPTX не входят в Git. Для повтора поместите файлы
`onocom-powerpoint-template.pptx` и `pptmaster-brutalism_field_guide.pptx`
с указанными SHA-256 в `slide-workspace/acceptance-unseen-2026-09-28/` и запустите
из корня репозитория:

```powershell
python examples/run_dataset.py --config examples/external_regression.json
```

Коммит исходников совпадал с ревизией Git в начале прогона, исходники и входы
не изменились до его конца. SHA-256 конфигурации:
`a0274fac0689af110ee272a50688366d2deccfda2322914dc43dff3b2cd0f80b`;
SHA-256 исходного текста:
`90549b837bd647f9617199d9fbe59046f9652b3b00e0835008b8aeabda53b444`.
