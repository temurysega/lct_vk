"""Complete the v3 bundle: scripts, licenses, QA, previews, docs and notebook.

Called by ``prepare_ru_v3`` after the data are written; can also be rerun alone::

    python -m training.package_ru_v3 --previous ../обучение --output ../обучение_ru_v3 --renders <pdf dir>
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = "BrandDeck_CPU_RU_v3_Training_Export.ipynb"
EPOCHS = 3
REASONS = {
    "picture_or_chart_driven": "смысл слайда в картинке, скриншоте или графике",
    "not_russian": "текст не на русском",
    "code": "код, SQL, консольный вывод",
    "no_editable_text": "нет редактируемого текста",
    "text_too_dense": "слишком плотный текст (заголовок > 160, > 12 пунктов или пункт > 400 символов)",
    "url_or_contacts_only": "только ссылки или контакты",
    "drawing_driven": "схема из фигур без текста пунктов",
    "source_not_russian": "вся презентация не на русском",
    "animation_build_duplicate": "промежуточный кадр анимации",
    "text_outside_slide": "текст за пределами слайда",
    "hidden_slide": "скрытый слайд",
    "unresolved_body_geometry": "не удалось определить область текста",
    "too_few_distinct_candidates": "меньше двух различимых кандидатов макета",
}


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def copy_support(previous: Path, output: Path, kept: list[dict]) -> None:
    for path in (previous / "scripts").iterdir():
        # rebuild_ru_split.py produced v2 and would overwrite this bundle.
        if path.is_file() and path.name != "rebuild_ru_split.py":
            shutil.copy2(path, output / "scripts" / path.name)
    for repo in sorted({s["repo"] for s in kept if s.get("repo")}):
        name = repo.replace("/", "__")
        shutil.copytree(
            previous / "licenses" / name, output / "licenses" / name, dirs_exist_ok=True
        )
    hashes = {s["sha256"] for s in kept}
    for source in kept:
        report = previous / "qa" / f"{source['sha256'][:12]}.json"
        if report.exists():
            shutil.copy2(report, output / "qa" / report.name)
    renders = [
        {k: v for k, v in item.items() if k != "preview"}
        for item in read(previous / "qa/new_sources_render.json")
        if item["sha256"] in hashes
    ]
    if renders:
        (output / "qa/new_sources_render.json").write_text(
            json.dumps(renders, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    readme = (ROOT / "training/CPU_README.md").read_text(encoding="utf-8")
    deploy = readme[readme.index("## Чекпоинты и результат") :]
    (output / "CPU_README.md").write_text(
        "# Colab → VPS для комплекта обучение_ru_v3\n\n"
        f"Обучение: ноутбук `{NOTEBOOK}` из этой папки, данные описаны в DATASET.md.\n\n"
        + deploy,
        encoding="utf-8",
    )


def previews(
    output: Path, renders: Path, kept: list[dict], review: list[dict]
) -> list[str]:
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        font = ImageFont.load_default(13)
    roles = {}
    for split in ("train", "validation"):
        for line in (
            (output / f"data/{split}.jsonl").read_text(encoding="utf-8").splitlines()
        ):
            record = json.loads(line)
            key = (
                record["provenance"]["template"],
                record["provenance"]["source_slide"],
            )
            roles[key] = json.loads(record["messages"][1]["content"])["requirements"][
                "role"
            ]
    decisions = defaultdict(dict)
    for item in review:
        if "source_slide" in item:
            decisions[item["template"]][item["source_slide"]] = item
    (output / "previews").mkdir(exist_ok=True)
    made = []
    for source in kept:
        pdf = renders / (Path(source["filename"]).stem + ".pdf")
        if source.get("split") == "test" or not pdf.exists():
            continue
        doc = pymupdf.open(pdf)
        tiles = []
        for number, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=36)
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            item = decisions[source["filename"]].get(number, {})
            kept_slide = item.get("decision") == "kept"
            if kept_slide:
                label = f"{number}: оставлен · {roles.get((source['filename'], number), '—')}"
            else:
                label = f"{number}: {item.get('reason') or 'нет решения'}"
            tile = Image.new(
                "RGB",
                (image.width + 8, image.height + 30),
                "#1f9d55" if kept_slide else "#c0392b",
            )
            tile.paste(image, (4, 4))
            ImageDraw.Draw(tile).text(
                (6, image.height + 8), label, fill="white", font=font
            )
            tiles.append(tile)
        columns = 5
        width, height = max(t.width for t in tiles), max(t.height for t in tiles)
        rows = (len(tiles) + columns - 1) // columns
        sheet = Image.new(
            "RGB", (columns * (width + 6), rows * (height + 6)), "#444444"
        )
        for index, tile in enumerate(tiles):
            sheet.paste(
                tile,
                ((index % columns) * (width + 6), (index // columns) * (height + 6)),
            )
        name = f"previews/{Path(source['filename']).stem}.jpg"
        sheet.save(output / name, quality=82)
        made.append(name)
    items = "\n".join(
        f'<h2>{Path(name).stem}</h2><img src="{Path(name).name}" loading="lazy">'
        for name in made
    )
    (output / "previews/index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Отбор слайдов v3</title>'
        "<style>body{font-family:sans-serif;background:#222;color:#eee;margin:16px}"
        "img{max-width:100%}</style>"
        "<h1>Отбор слайдов v3</h1><p>Зелёная рамка — слайд в обучении (роль), "
        "красная — удалён (причина).</p>\n" + items + "\n",
        encoding="utf-8",
    )
    return made


def docs(
    output: Path,
    manifest: dict,
    kept: list[dict],
    review: dict,
    previous_manifest: dict,
) -> None:
    stats = manifest["split_stats"]
    removed = manifest["review"]["removed_by_reason"]
    reviewed = manifest["review"]["slides_reviewed"]
    kept_total = sum(s["kept_examples"] for s in kept)
    unique_total = sum(stats[split]["unique_groups"] for split in stats)

    def roles(split: str) -> str:
        return ", ".join(f"{k} {v}" for k, v in sorted(stats[split]["roles"].items()))

    split_rows = "\n".join(
        f"| {split} | {stats[split]['rows']} | {stats[split]['unique_groups']} | "
        f"{stats[split]['templates']} | {', '.join(stats[split]['families'])} | {roles(split)} |"
        for split in ("train", "validation", "test")
    )
    reason_rows = "\n".join(
        f"| `{code}` | {count} | {REASONS.get(code, '')} |"
        for code, count in sorted(removed.items(), key=lambda item: -item[1])
    )
    per_source = defaultdict(Counter)
    for item in review["slides"]:
        if "source_slide" in item and item["decision"] == "removed":
            per_source[item["template"]][item["reason"]] += 1
    source_rows = "\n".join(
        f"| {s['filename']} | {s['split']} | {s['design_family']} | {s['kept_examples']} из {s['reviewed_slides']} | "
        + ", ".join(f"{k} {v}" for k, v in per_source[s["filename"]].most_common())
        + " |"
        for s in kept
    )
    old = previous_manifest.get("split_stats", {})
    old_train = old.get("train", {}).get(
        "unique_groups", previous_manifest.get("train_unique_groups", "—")
    )
    (output / "DATASET.md").write_text(
        f"""# Данные v3: только русские слайды после просмотра

Эксперимент `{manifest["experiment"]}`. Источник — проверенные PPTX комплекта v2
(`ru_vk_heldout_v2`), те же файлы и контрольные суммы, те же семейства в тех же разбиениях.
Каждый слайд каждого источника просмотрен и получил решение в `data/review.json`;
контактные листы с решениями — `previews/index.html` (зелёный — оставлен, красный — удалён).

## Правила отбора

Модель видит только текст и структурные признаки кандидатов, но не картинки.
Поэтому удаляются слайды, где макет определяется изображением, а не текстом.

- Только русский текст. Английские и китайско-английские источники удалены целиком.
- Удаляются скрытые слайды, код/SQL/консольный вывод, слайды только со ссылками или контактами.
- Удаляются слайды, где картинка, скриншот или график занимает ≥ 50% площади,
  либо ≥ 25% при двух и менее пунктах или коротких подписях (≤ 30 символов в среднем).
  Фоны на весь слайд и повторяющиеся логотипы не считаются.
- Схемы из ≥ 6 фигур без пунктов удаляются (кроме обложек и финальных слайдов).
- Из последовательных кадров анимации (тот же заголовок, пункты — подмножество
  соседнего слайда) остаётся самый полный.
- Сквозные надписи (название доклада в углу на ≥ 3 слайдах) не считаются пунктами.
- Если «заголовок» — длинный абзац (> 90 символов), а над ним короткая строка,
  заголовком считается строка.
- Роль: обложка и финал — как в исходнике; слайд с пунктами — `content`;
  одинокий заголовок — `section`.
- Слишком плотный текст (заголовок > 160 символов, > 12 пунктов, пункт > 400) удалён.

Шаблоны VK полностью исключены из train/validation и используются только в test.

## Итог

Просмотрено слайдов: {reviewed}, оставлено: {kept_total}; после удаления
повторов одинакового текста — {unique_total} уникальных примеров.

| Разбиение | Строк | Уникальных | Шаблонов | Семейства | Роли (уникальные) |
|---|---:|---:|---:|---|---|
{split_rows}

В train три перестановки кандидатов на пример, в validation и test — одна.
Дубликаты содержимого между разбиениями исключены.
Для сравнения: в v2 было {old_train} уникальных примеров train, включая английские.
Метрики v3 и v2 несопоставимы: другие validation/test.

## Удалено

| Причина | Слайдов | Что это |
|---|---:|---|
{reason_rows}

## Источники

| Файл | Разбиение | Семейство | Оставлено | Удалено по причинам |
|---|---|---|---|---|
{source_rows}

Лицензии открытых репозиториев — в `licenses/`; оригинальные PPTX VK предоставлены
организаторами для обучения и не публикуются. Метки слабые: целевой кандидат — макет
исходного слайда, а не экспертная оценка дизайна.
""",
        encoding="utf-8",
    )
    (output / "README.md").write_text(
        f"""# обучение_ru_v3

Комплект для обучения LoRA выбора макетов (Qwen2.5-1.5B-Instruct) только на русских
слайдах, просмотренных вручную. Отличия от v2 и правила отбора — в DATASET.md.

1. Загрузите папку **обучение_ru_v3** целиком в «Мой диск».
2. Откройте в Colab `{NOTEBOOK}`, выберите A100, выполните ячейки по порядку.
3. Скачайте `cpu_bundle.zip`; запуск на VPS — CPU_README.md.

Train: {stats["train"]["rows"]} строк / {stats["train"]["unique_groups"]} уникальных примеров;
validation: {stats["validation"]["rows"]}; test (шаблоны VK): {stats["test"]["rows"]}.

Содержимое: `data/` (jsonl, манифест, решения по слайдам), `templates/` (источники),
`test_templates/` (оригиналы VK со всеми медиа), `scripts/`, `licenses/`, `qa/`, `previews/`.
""",
        encoding="utf-8",
    )


def notebook(previous: Path, output: Path, manifest: dict) -> None:
    source = previous / "BrandDeck_CPU_RU_Clean_Training_Export.ipynb"
    nb = json.loads(source.read_text(encoding="utf-8"))
    stats = manifest["split_stats"]
    replacements = [
        (
            "# BrandDeck: русские данные, три шаблона VK только в test",
            "# BrandDeck v3: только русские слайды после просмотра, VK только в test",
        ),
        (
            "Загрузите папку **обучение** целиком",
            "Загрузите папку **обучение_ru_v3** целиком",
        ),
        (
            "`/content/drive/MyDrive/обучение`",
            "`/content/drive/MyDrive/обучение_ru_v3`",
        ),
        (
            "Train: 645 строк / 215 уникальных примеров; validation: 61; test: 99.",
            (
                f"Train: {stats['train']['rows']} строк / {stats['train']['unique_groups']} уникальных примеров; "
                f"validation: {stats['validation']['rows']}; test: {stats['test']['rows']}.\n"
                "Каждый слайд просмотрен: код, скриншоты, схемы без текста, кадры анимации и\n"
                "нерусский текст удалены (решения — data/review.json, превью — previews/index.html)."
            ),
        ),
        (
            "Обучение в Google Colab на A100: две эпохи.",
            f"Обучение в Google Colab на A100: {EPOCHS} эпохи (данных меньше, чем в v2).",
        ),
        (
            "Path('/content/drive/MyDrive/обучение')",
            "Path('/content/drive/MyDrive/обучение_ru_v3')",
        ),
        (
            "'ru_vk_heldout_v2', 'Нужен обновлённый комплект'",
            f"'{manifest['experiment']}', 'Нужен комплект обучение_ru_v3'",
        ),
        ("/content/lct-ru-v2-scripts", "/content/lct-ru-v3-scripts"),
        ("EPOCHS = 2", f"EPOCHS = {EPOCHS}"),
        ("/content/lct-ru-v2-work", "/content/lct-ru-v3-work"),
        ("/content/lct-ru-clean-export", "/content/lct-ru-v3-export"),
    ]
    for cell in nb["cells"]:
        if cell["cell_type"] == "code":
            cell.update(outputs=[], execution_count=None)
    for old, new in replacements:
        joined = ["".join(cell["source"]) for cell in nb["cells"]]
        if not any(old in src for src in joined):
            raise ValueError("Notebook text not found: " + old)
        for cell, src in zip(nb["cells"], joined):
            if old in src:
                cell["source"] = src.replace(old, new).splitlines(keepends=True)
    (output / NOTEBOOK).write_text(
        json.dumps(nb, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def package(previous: Path, output: Path, renders: Path | None = None) -> dict:
    manifest = read(output / "data/manifest.json")
    kept = read(output / "data/source_inventory.json")
    review = read(output / "data/review.json")
    copy_support(previous, output, kept)
    made = previews(output, renders, kept, review["slides"]) if renders else []
    docs(output, manifest, kept, review, read(previous / "data/manifest.json"))
    notebook(previous, output, manifest)
    sys.path.insert(0, str(output / "scripts"))
    sys.dont_write_bytecode = True  # keep __pycache__ out of the Drive bundle
    from refresh_manifest import refresh
    from verify_data import verify

    refresh(output)
    return {"previews": len(made), "verify": verify(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--renders", type=Path, help="PDF renders of templates/, named <stem>.pdf"
    )
    args = parser.parse_args()
    result = package(args.previous.resolve(), args.output.resolve(), args.renders)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
