# Проверка веб-интерфейса — 25.09.2026

Среда: Windows, собранный React frontend и локальный backend с отдельным
тестовым workspace. Playwright 1.63.0. Проверены установленный Google Chrome
153.0.8010.54, браузерные сборки Firefox и WebKit из Playwright.

| Движок | Полный сценарий | Вёрстка и axe |
|---|---|---|
| Chrome | пройден | пройден |
| Firefox | пройден | пройден |
| WebKit на Windows | пройден | пройден |

Полный сценарий (`tests/browser_smoke.cjs`): главная → регистрация → загрузка
нового PPTX → офлайн-генерация трёх вариантов → переключение варианта →
скачивание PPTX → история после обновления страницы → мобильная ширина → выход
→ ошибочный и верный повторный вход. В тесте использован небольшой новый
шаблон, созданный `examples.create_demo_assets`; пользовательские данные других
аккаунтов не затрагивались.

`tests/browser_layout.cjs` проверил главную и форму на ширинах 1440, 1366, 768,
390, 375 и 320 px: нет горизонтального переполнения или ошибок JavaScript;
axe-core не нашёл нарушений WCAG A/AA в проверенных состояниях. Мобильные
размеры включены как дополнительная проверка, хотя ТЗ требует только десктоп.

Команды воспроизведения после `npm run build --prefix frontend` и запуска API:

```powershell
$env:PREDEL_TEST_TEMPLATE=(Resolve-Path 'slide-workspace/browser-fixture/clean-blue.pptx').Path
$env:PREDEL_BROWSER_TYPE='chromium'
$env:PREDEL_BROWSER_CHANNEL='chrome'
node tests/browser_smoke.cjs
node tests/browser_layout.cjs
$env:PREDEL_BROWSER_TYPE='firefox'
Remove-Item Env:PREDEL_BROWSER_CHANNEL
node tests/browser_smoke.cjs
node tests/browser_layout.cjs
$env:PREDEL_BROWSER_TYPE='webkit'
node tests/browser_smoke.cjs
node tests/browser_layout.cjs
```

Настоящие Safari на macOS, Яндекс Браузер и предыдущие версии четырёх браузеров
не запускались. WebKit на Windows приближает движок Safari, но не подтверждает
поведение Safari в macOS. Для полного соответствия пункту 3 ТЗ нужна отдельная
матрица на этих системах и версиях.
