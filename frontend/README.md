# predel — React frontend

React 19 + TypeScript + Vite. Главная, авторизация и вся студия — React-компоненты,
без встраивания старых HTML-страниц. Backend — отдельный FastAPI в `backend/`.
Клиент не меняет обучение модели и API генерации.

## Запуск

Node.js 22.12+ и установленный Python backend:

```bash
npm ci --prefix frontend
npm run build --prefix frontend
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Откройте http://127.0.0.1:8000. Docker выполняет npm-сборку автоматически.
При упаковке Python wheel сначала соберите frontend: пакет включает `dist/`.
В разработке оставьте backend на 8000 и запустите `npm run dev --prefix frontend`;
Vite на http://127.0.0.1:5173 проксирует API на backend.
В production клиент и API работают с одного origin, отдельный Node-сервер не нужен.

## Структура

- `src/pages/Landing.tsx` — главная, примеры композиций, бриф.
- `src/pages/Studio.tsx` — шаблоны, материалы, результаты и история.
- `src/hooks/useStudio.ts` — загрузка, генерация, polling, восстановление задания.
- `src/components/AuthDialog.tsx` — вход и регистрация из трёх полей.
- `src/components/DeckResult.tsx` — превью, честные статусы QA, исправления, экспорт.
- `src/api.ts` — типизированные запросы, ошибки, черновик и идентификатор задания.
- `src/styles.css` — адаптивная дизайн-система.
- `public/assets/` — локальные шрифты, favicon, иллюстрация.

Визуальная система: белые и светло-серые поверхности, синий #0077FF в духе VK.
У [Butter](https://www.butter.video/) взяты идеи плавающей навигации, крупной
типографики, тактильного 3D-объекта и просторных секций, а не логотип или материалы.
Бренд остаётся predel; связи с VK или Butter не подразумевается.
Демонстрационные слайды на главной помечены как примеры, не результаты модели.

Motion обеспечивает появление hero, глубину при скролле и переключения/раскрытия.
Учтены prefers-reduced-motion, клавиатура, нативный modal dialog, фокус,
мобильное меню и отсутствие горизонтального переполнения.
Шрифт Manrope локальный, лицензия `public/assets/fonts/OFL.txt`.
Страница не использует внешние CDN и трекеры.
Происхождение иллюстрации: [assets/README.md](public/assets/README.md).

## Проверки

```bash
npm run typecheck --prefix frontend
npm run build --prefix frontend
python -m pytest
```

`tests/browser_smoke.cjs` проверяет регистрацию, перенос брифа, загрузку PPTX,
генерацию трёх вариантов, скачивание, историю, мобильную ширину, выход,
ошибочный пароль и повторный вход. Используйте отдельный тестовый workspace:
сценарий создаёт аккаунт и презентацию.

```bash
npm ci --prefix frontend
cd frontend
npx playwright install chromium
cd ..
PREDEL_BASE_URL=http://127.0.0.1:8766 \
PREDEL_TEST_TEMPLATE=/absolute/path/to/template.pptx node tests/browser_smoke.cjs
```

Для установленного Chrome задайте `PREDEL_BROWSER_CHANNEL=chrome`.
Скриншоты сохраняются во временный каталог, путь выводится после прогона.

`tests/browser_layout.cjs` проверяет шесть размеров экрана, положение CTA,
отсутствие пересечений hero, переключение примеров, мобильное меню, Escape
и axe-core WCAG A/AA. Не создаёт аккаунты и не меняет данные:

```bash
PREDEL_BASE_URL=http://127.0.0.1:8000 npm run test:layout --prefix frontend
```
