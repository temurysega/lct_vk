"""Build a portable local artifact index; no network, product changes, or models."""
from pathlib import Path
import html
import json

OUT = Path(__file__).resolve().parents[1]
records = []
lines = ['# Презентации и реальные превью', '',
         'Основной набор: **15 новых PPTX по 10 слайдов** — три официальных и два внешних шаблона, по три варианта на один полный текст. Все они получены через API без модели (`planner_mode=offline`). [Интерактивное сравнение](gallery.html) работает локально без сервера.', '',
         'PNG и contact sheets ниже построены из реального **LibreOffice PDF**. Они не являются схематическим рендером и не объявляются эквивалентом PowerPoint. Шрифты могут заменяться. По отдельным PowerPoint-экспортам приведены самостоятельные ссылки.', '',
         '| Шаблон / вариант | PPTX | PDF | HTML | Обзор слайдов | План / QA |',
         '|---|---|---|---|---|---|']

def rel(p):
    return str(p.relative_to(OUT))

for base in ['with-renderer/artifacts', 'additional-unseen/artifacts']:
    for folder in sorted((OUT/base).glob('*/*')):
        files = list(folder.glob('*.pptx'))
        if not files:
            continue
        pptx = files[0]
        pdf = next(folder.glob('*.pdf'))
        webpage = next(folder.glob('*.html'))
        sheet = folder/'libreoffice-contact-sheet.jpg'
        title = folder.parent.name+' / '+folder.name
        lines.append(f'| {title} | [PPTX]({rel(pptx)}) | [PDF]({rel(pdf)}) | [HTML]({rel(webpage)}) | [10 слайдов]({rel(sheet)}) | [план]({rel(folder/"deck_plan.final.json")}) · [QA]({rel(folder/"qa_report.json")}) |')
        records.append({'title':title,'template':folder.parent.name,'variant':folder.name,'renderer':'LibreOffice 26.2.6.3','folder':rel(folder),'prefix':'libreoffice','pptx':rel(pptx),'pdf':rel(pdf)})

lines += ['', '## Проверено именно в Microsoft PowerPoint/macOS', '',
          'Это **два baseline-файла**, созданные до подключения LibreOffice к сервису. Из-за отсутствовавших renderer checks они могут отличаться от позднейших одноимённых вариантов выше. Не подменяйте ими проверку всей выборки.', '',
          '| Колода | Исходный PPTX | PowerPoint PDF | Превью |', '|---|---|---|---|']
for slug in ['vk-tech','unseen-brutalism']:
    folder=OUT/'artifacts'/slug/'balanced'
    pptx=next(folder.glob('*.pptx'))
    lines.append(f'| {slug} / balanced | [PPTX]({rel(pptx)}) | [PDF]({rel(folder/"powerpoint.pdf")}) | [обзор]({rel(folder/"powerpoint-contact-sheet.jpg")}) |')
    records.append({'title':slug+' / baseline balanced','template':slug,'variant':'baseline balanced','renderer':'Microsoft PowerPoint / macOS','folder':rel(folder),'prefix':'powerpoint','pptx':rel(pptx),'pdf':rel(folder/'powerpoint.pdf')})

lines += ['', '## Компонентные проверки визуализаций', '',
          'Ручной контрольный план, **не генерация моделью и не UI end-to-end**. Обычный приёмочный source не содержал табличных рядов, поэтому наличие функций не засчитано вместо проверки charts/tables. Здесь отдельно собраны и просмотрены 2 charts, 2 tables, process, cycle, hierarchy, icon grid.', '',
          '| Сценарий | PPTX | PDF | Превью |', '|---|---|---|---|']
for name,label in [('component-visuals','Только composer'),('component-edited','Правка данных → сохранение → повторное чтение'),('component-service','Полный сервис: QA, retries, export')]:
    folder=OUT/'artifacts'/name/'vk-education'
    pptx=folder/(name+'.pptx');pdf=next(folder.glob('*.pdf'))
    lines.append(f'| {label} | [PPTX]({rel(pptx)}) | [PDF]({rel(pdf)}) | [обзор]({rel(folder/"libreoffice-contact-sheet.jpg")}) |')
    records.append({'title':name,'template':'component probe','variant':name,'renderer':'LibreOffice 26.2.6.3','folder':rel(folder),'prefix':'libreoffice','pptx':rel(pptx),'pdf':rel(pdf)})

lines += ['', 'Проверка редактирования: [edit-roundtrip.json](artifacts/component-visuals/vk-education/edit-roundtrip.json). Ключевой дефект: [pie chart после сервисного QA, слайд 3](artifacts/component-service/vk-education/libreoffice-slide-03.png). Исходные значения 60/90; в копии диаграмма изменена на 61/89, ячейка и заголовок также изменены. Это программное редактирование объектов, не запись ручного GUI-редактирования.', '',
          '## Слайды, на которые стоит обратить внимание', '',
          '- [Brutalism: узкая композиция](with-renderer/artifacts/unseen-brutalism/balanced/libreoffice-slide-03.png); [тот же тип проблемы в PowerPoint](artifacts/unseen-brutalism/balanced/powerpoint-slide-03.png).',
          '- [Onocom: текст выходит из карточек](additional-unseen/artifacts/unseen-onocom/balanced/libreoffice-slide-04.png); [вертикальное письмо](additional-unseen/artifacts/unseen-onocom/focus/libreoffice-slide-09.png).',
          '- [Текущий UI: результат и замечания](with-renderer/artifacts/browser-current/generated-deck.png); [после выбранного repair](with-renderer/artifacts/browser-current/after-selected-repair.png); [короткий бриф без модели](with-renderer/artifacts/browser-current/brief-without-model.png).', '',
          '## Исходные шаблоны и сохранённый анализ', '',
          'Оригинальные PPTX оставлены на прежнем месте, их SHA записаны; в отчёт не подменялись исправленные версии. Превью оригиналов также получены LibreOffice. У Brutalism часть типографических недостатков видна уже в исходнике; они не целиком приписаны генератору. Onocom — минимальный шаблон с почти пустыми исходными слайдами и дополнительными макетами.', '']
for slug in ['vk-tech','vk-workspace','vk-education','unseen-brutalism','unseen-onocom']:
    folder=OUT/'sources'/slug
    imgs=sorted(folder.glob('source-slide-*.png'))
    links=' · '.join(f'[{p.stem}]({rel(p)})' for p in imgs)
    lines.append(f'- {slug}: {links}')
lines += ['', 'Анализы лежат рядом с вариантами: `with-renderer/artifacts/<template>/analysis-*.json`, для Onocom — `additional-unseen/artifacts/unseen-onocom/analysis-*.json`. В них сохранены токены и паттерны. Схематические картинки анализатора не смешаны с реальными офисными превью в галерее.', '',
          '## Машиночитаемые доказательства', '',
          '- [Инвентаризация нативных объектов и SHA выходных PPTX](evidence/object-summary.json).',
          '- [Все 15 колод: содержание, PDF-текст, шрифты](evidence/content-comparison-all.json).',
          '- [Сохранение themes/masters/layouts](evidence/design-and-pdf-comparison.json).',
          '- [Сравнение трёх вариантов по реальным пикселям](evidence/rendered-diversity.json). Это внутренняя эвристика, не официальный порог жюри.',
          '- [Базовый запуск без renderer](evidence/api-summary.json), [12 колод с renderer](with-renderer/evidence/api-summary.json), [дополнительные 3 Onocom](additional-unseen/evidence/api-summary.json).',
          '- [Команды и пределы проверки](VALIDATION.md), [полный отчёт](REPORT.md).', '']
(OUT/'ARTIFACTS.md').write_text('\n'.join(lines))

template = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BrandDeck — артефакты аудита</title>
<style>body{font:16px system-ui,sans-serif;margin:24px;background:#f3f3f2;color:#18232c}h1{font-size:26px}p{max-width:1000px;line-height:1.5}label{display:inline-block;margin:0 18px 16px 0}select,input{font:inherit;padding:6px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:20px}article{background:white;padding:14px;border:1px solid #ccc}article img{display:block;width:100%;height:auto}h2{font-size:17px;margin:0 0 8px}small{display:block;color:#52606c;margin-bottom:10px}a{color:#174c9b}nav{margin:10px 0}footer{margin:28px 0}@media(max-width:500px){main{display:block}article{margin-bottom:16px}}</style>
<h1>BrandDeck · проверенные слайды</h1><p>27.09.2026 · main @ 4330625. Основные колоды: полный материал → offline-планирование. Настоящая модель не проверена. Превью получены из офисного PDF, рендерер подписан у каждого результата. PowerPoint доступен только для двух ранних колод; остальные превью — LibreOffice с возможной заменой шрифтов.</p>
<nav><a href="REPORT.md">Отчёт</a> · <a href="ARTIFACTS.md">Все файлы</a> · <a href="PROBLEMS.md">Проблемы</a></nav>
<label>Шаблон <select id="template"></select></label><label>Рендерер <select id="renderer"><option value="all">Все</option><option value="LibreOffice 26.2.6.3">LibreOffice</option><option value="Microsoft PowerPoint / macOS">PowerPoint</option></select></label><label>Слайд <input id="slide" type="number" min="1" max="10" value="3"></label><main id="decks"></main><footer>Сравнивайте три варианта одного слайда. Component probe использует ручной план и не доказывает способность модели выбирать визуализацию.</footer>
<script>const data=__DATA__;const sel=document.getElementById('template'),renderer=document.getElementById('renderer'),slide=document.getElementById('slide'),decks=document.getElementById('decks');
for(const t of [...new Set(data.map(d=>d.template))]){const o=document.createElement('option');o.value=t;o.textContent=t;sel.append(o)}sel.value='vk-tech';
function render(){decks.replaceChildren();const n=Math.min(10,Math.max(1,Number(slide.value)||1));for(const d of data.filter(d=>d.template===sel.value&&(renderer.value==='all'||d.renderer===renderer.value))){const a=document.createElement('article');const h=document.createElement('h2');h.textContent=d.title;a.append(h);const label=document.createElement('small');label.textContent=d.renderer+' · слайд '+n;a.append(label);const im=document.createElement('img');im.src=d.folder+'/'+d.prefix+'-slide-'+String(n).padStart(2,'0')+'.png';im.alt=d.title+' слайд '+n;a.append(im);const nav=document.createElement('nav');for(const [title,url] of [['PPTX',d.pptx],['PDF',d.pdf],['PNG',im.getAttribute('src')],['Все слайды',d.folder+'/'+d.prefix+'-contact-sheet.jpg']]){const link=document.createElement('a');link.textContent=title;link.href=url;nav.append(link,document.createTextNode(' · '))}a.append(nav);decks.append(a)}}[sel,renderer,slide].forEach(e=>e.addEventListener('change',render));render();</script></html>'''
(OUT/'gallery.html').write_text(template.replace('__DATA__',json.dumps(records,ensure_ascii=False)))
print(json.dumps({'gallery_records':len(records),'acceptance_decks':15}))
