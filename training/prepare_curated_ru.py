"""Build an immutable Russian-focused CPU training bundle from reviewed PPTX files."""

import argparse
import hashlib
import html
import json
import shutil
from collections import Counter
from pathlib import Path

from pptx import Presentation

from training.build_cpu_notebook import notebook
from training.curation import clean_text, is_page_marker
from training.data_utils import check_split, encode_record, load_records
from training.prepare_drive import ROOT, digest, extract_examples, make_record
from training.render_quality import audit_pdf, render_pdf


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def filtered_copy(source: Path, destination: Path, keep: list[int]) -> None:
    prs = Presentation(source)
    ids = prs.slides._sldIdLst
    for index in range(len(ids), 0, -1):
        if index not in keep:
            rel = ids[index - 1]
            prs.part.drop_rel(rel.rId)
            ids.remove(rel)
    prs.save(destination)


def build_notebook(folder: str) -> dict:
    document = notebook()
    for cell in document["cells"]:
        cell["source"] = [
            line.replace("lct_cpu", folder).replace(
                "/content/lct-cpu-scripts", "/content/lct-ru-clean-scripts"
            )
            for line in cell["source"]
        ]
        source = "".join(cell["source"])
        if "RESUME = 'auto'" in source:
            source = source.replace(
                "'--drive-root', str(ROOT), '--model'",
                "'--drive-root', str(ROOT), '--local-root', '/content/lct-ru-clean-work', '--model'",
            )
            source = source.replace(
                "print(json.dumps(metrics, ensure_ascii=False, indent=2))",
                "print(json.dumps({k: {a: b for a, b in v.items() if a != 'predictions'} if isinstance(v, dict) else v for k, v in metrics.items()}, ensure_ascii=False, indent=2))",
            )
        if "str(LOCAL_SCRIPTS / 'export_cpu.py')" in source:
            source = source.replace(
                "'--llama-root', str(LLAMA)",
                "'--llama-root', str(LLAMA), '--local-root', '/content/lct-ru-clean-export'",
            )
            source += "\nexported = json.loads((ROOT / 'exports/latest.json').read_text())\nreport_dir = ROOT / Path(exported['archive']).parent / 'cpu'\nfor name in ('cpu_report.json', 'test_report.json'):\n    result = json.loads((report_dir / name).read_text())\n    print(name)\n    for kind in ('baseline', 'adapted'):\n        print(kind, {k: v for k, v in result[kind].items() if k != 'predictions'})\n"
        cell["source"] = source.splitlines(keepends=True)
    document["cells"].insert(
        1,
        {
            "cell_type": "markdown",
            "id": "ru-clean-dataset",
            "metadata": {},
            "source": [
                "## Новый эксперимент: русские презентации и проверка рендеров\n",
                "Загрузите папку **"
                + folder
                + "** целиком в Мой диск. Выберите A100. Выполните все шаги по порядку.\n",
                "Этот запуск обучает новый адаптер от базовой модели. Старые веса не требуются. `RESUME='auto'` продолжает только запуск в этой новой папке.\n",
                "Две эпохи, контрольные суммы, сохранение чекпоинтов и исправленный экспорт LoRA включены.\n",
                "Test — отдельное семейство русских шаблонов; он запускается после фиксации решения по validation.\n",
                "Отчёт отбора: DATASET.md. Предпросмотр очищенных презентаций: previews/index.html.\n",
            ],
        },
    )
    index = next(
        i
        for i, c in enumerate(document["cells"])
        if "drive.mount" in "".join(c["source"])
    )
    document["cells"].insert(
        index + 1,
        {
            "cell_type": "code",
            "id": "ru-clean-preflight",
            "metadata": {},
            "execution_count": None,
            "outputs": [],
            "source": [
                "sys.path.insert(0, str(ROOT / 'scripts'))\n",
                "from data_utils import load_records, check_split\n",
                "parts = {name: load_records(ROOT / 'data' / (name + '.jsonl')) for name in ('train', 'validation', 'test')}\n",
                "check_split(parts['train'], parts['validation'], parts['test'])\n",
                "assert manifest['quality_gate']['final_render_failures'] == 0\n",
                "for name, rows in parts.items():\n",
                "    print(name, manifest['split_stats'][name])\n",
                "print('Проверки данных пройдены. Test не участвует в обучении.')\n",
            ],
        },
    )
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_root, output = args.source_root.resolve(), args.output.resolve()
    if (output / "weights/latest.json").exists() or list(
        (output / "runs").glob("*/run.json")
    ):
        raise ValueError("Do not replace a dataset that already has a training run")
    for name in (
        "templates",
        "scripts",
        "data",
        "qa",
        "previews",
        "licenses",
        "runs",
        "weights",
    ):
        (output / name).mkdir(parents=True, exist_ok=True)
    sources = json.loads((source_root / "selection.json").read_text(encoding="utf-8"))
    audits = {
        a["filename"]: a
        for a in json.loads(
            (source_root / "render_audit.json").read_text(encoding="utf-8")
        )
    }
    inventory, rows, rejected, gallery = [], [], [], []
    for source in sources:
        name = source["filename"]
        audit = audits[name]
        # Whole-source exclusions found during visual review, plus automatic gate.
        if "4_bloxy" in name or "select_from_input" in name:
            rejected.append(
                {"template": name, "reason": "too_few_clean_editable_slides"}
            )
            continue
        manual = {1, 52} if "clickhouse_in_the_world" in name else set()
        keep = [
            s["slide"]
            for s in audit["slides"]
            if s["status"] == "pass" and s["slide"] not in manual
        ]
        if len(keep) < 4:
            rejected.append({"template": name, "reason": "too_few_clean_slides"})
            continue
        destination = output / "templates" / name
        filtered_copy(source_root / "selected" / name, destination, keep)
        original_sha = digest(source_root / "selected" / name)
        clean_sha = digest(destination)
        work = source_root / "clean-renders" / clean_sha[:12]
        pdf = work / "render.pdf"
        render_pdf(destination, pdf)
        verified = audit_pdf(pdf)
        if len(verified) != len(keep) or any(s["status"] != "pass" for s in verified):
            write_json(work / "failure.json", verified)
            raise ValueError(f"Filtered presentation failed its render check: {name}")
        all_rows = extract_examples(
            destination, source_root / "analysis", set(range(1, len(keep) + 1))
        )
        source_row = {
            k: v for k, v in source.items() if k not in {"local_path", "text_samples"}
        }
        source_row.update(
            original_sha256=original_sha,
            sha256=clean_sha,
            original_slides=len(audit["slides"]),
            clean_slides=len(keep),
            original_slide_map=keep,
            transformation="Removed rejected slides; original files unchanged",
        )
        inventory.append(source_row)
        write_json(
            output / "qa" / (clean_sha[:12] + ".json"),
            {
                "template": name,
                "original_audit": audit["slides"],
                "manual_rejected_slides": sorted(manual),
                "original_slide_map": keep,
                "clean_sha256": clean_sha,
                "final_render": verified,
            },
        )
        for row in all_rows:
            if is_page_marker(row["slide"]["title"]):
                raise ValueError("Page marker became a title")
            if (
                row["positive"].get("capacity", {}).get("usable_body_zones", 0) == 0
                and row["requirements"]["body_chars"] > 100
            ):
                rejected.append(
                    {
                        "template": name,
                        "slide": row["source_slide"],
                        "reason": "unresolved_body_geometry",
                    }
                )
                continue
            text = clean_text(
                " ".join([row["slide"]["title"], *row["slide"]["bullets"]])
            ).casefold()
            row["group"] = hashlib.sha256(text.encode()).hexdigest()
            row["curated_provenance"] = {
                "design_family": source["design_family"],
                "split": source["split"],
                "language": source["language"],
                "source_url": source["url"],
                "license": source["license"],
                "original_sha256": original_sha,
                "original_slide": keep[row["source_slide"] - 1],
                "quality": "render_pass",
            }
            rows.append(row)
        import pymupdf as fitz
        from PIL import Image, ImageDraw

        contact = Image.new("RGB", (1200, 680), "white")
        label = ImageDraw.Draw(contact)
        with fitz.open(pdf) as doc:
            samples = sorted({0, len(doc) // 3, 2 * len(doc) // 3, len(doc) - 1})
            for k, index in enumerate(samples):
                page = doc[index]
                pix = page.get_pixmap(
                    matrix=fitz.Matrix(570 / page.rect.width, 570 / page.rect.width)
                )
                image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                image.thumbnail((570, 300))
                x, y = (k % 2) * 600 + 10, (k // 2) * 340 + 25
                contact.paste(image, (x, y))
                label.text(
                    (x, y - 18),
                    f"Clean slide {index + 1} / original {keep[index]}",
                    fill="black",
                )
        thumbnail = clean_sha[:12] + ".jpg"
        contact.save(output / "previews" / thumbnail, quality=88)
        gallery.append(
            f'<section><h2>{html.escape(name)}</h2><p>{source["split"]} · {source["language"]} · {len(keep)} slides</p><img width="100%" src="{thumbnail}"></section>'
        )
        print(
            f"Curated {name}: {len(keep)} slides, {len(all_rows)} possible examples",
            flush=True,
        )
    from transformers import AutoTokenizer

    profile = json.loads((ROOT / "training/cpu_profile.json").read_text())
    tokenizer = AutoTokenizer.from_pretrained(
        profile["base_model"], revision=profile["base_revision"]
    )
    parts = {name: [] for name in ("train", "validation", "test")}
    seen, lengths = set(), []
    for split in ("test", "validation", "train"):
        for row in rows:
            if row["curated_provenance"]["split"] != split:
                continue
            if row["group"] in seen:
                rejected.append(
                    {
                        "template": row["template"],
                        "slide": row["source_slide"],
                        "reason": "repeated_content",
                    }
                )
                continue
            seen.add(row["group"])
            for permutation in range(3 if split == "train" else 1):
                record = make_record(row, permutation)
                record["provenance"].update(row["curated_provenance"])
                lengths.append(len(encode_record(tokenizer, record, 3072)["input_ids"]))
                parts[split].append(record)
    check_split(parts["train"], parts["validation"], parts["test"])
    for name, records in parts.items():
        if len(records) < 8:
            raise ValueError(f"Too few curated examples: {name}")
        path = output / "data" / (name + ".jsonl")
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
        load_records(path)
    stats = {
        name: {
            "rows": len(records),
            "unique_groups": len({r["provenance"]["group"] for r in records}),
            "families": sorted({r["provenance"]["design_family"] for r in records}),
            "unique_by_language": dict(
                Counter(
                    r["provenance"]["language"]
                    for r in records
                    if r["permutation"] == 0
                )
            ),
        }
        for name, records in parts.items()
    }
    for name in (
        "train_layout.py",
        "data_utils.py",
        "checkpoints.py",
        "requirements-a100.txt",
        "check_gguf.py",
        "export_cpu.py",
        "cpu_profile.json",
    ):
        shutil.copy2(ROOT / "training" / name, output / "scripts" / name)
    from slide_agent.prompt_config import load_prompt

    write_json(
        output / "data/planner_smoke.json",
        {
            "expected_slides": 3,
            "request": {
                "model": "lct-cpu",
                "temperature": 0,
                "max_tokens": 1024,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": load_prompt("planner-cpu")},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "requested_slide_count": 3,
                                "source": "Проект Сфера. Команда автоматизирует подготовку презентаций. За квартал создано 120 презентаций. Следующий этап — пилот в двух отделах.",
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
            },
        },
    )
    write_json(output / "data/source_inventory.json", inventory)
    write_json(output / "data/curation_rejections.json", rejected)
    write_json(
        output / "data/token_length_audit.json",
        {
            "max_full_tokens": max(lengths),
            "max_length": 3072,
            "tokenizer_revision": profile["base_revision"],
        },
    )
    document = build_notebook(output.name)
    notebook_name = "BrandDeck_CPU_RU_Clean_Training_Export.ipynb"
    write_json(output / notebook_name, document)
    shutil.copy2(ROOT / "training/CPU_README.md", output / "CPU_README.md")
    if (source_root / "licenses").exists():
        shutil.copytree(
            source_root / "licenses", output / "licenses", dirs_exist_ok=True
        )
    (output / "previews/index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Curated corpus</title><style>body{max-width:1200px;margin:auto;font-family:Arial}section{margin:40px 0}h2{font-size:18px}</style><h1>Очищенные презентации</h1>'
        + "\n".join(gallery),
        encoding="utf-8",
    )
    readme = f"""# Новый комплект {output.name}

1. Загрузите эту папку целиком в «Мой диск» Google Drive. Не смешивайте её со старой папкой.
2. Откройте `{notebook_name}` в Colab, выберите A100 и выполните все ячейки по порядку.
3. Обучение: новый LoRA для Qwen2.5-1.5B-Instruct, две эпохи. Старые веса не нужны.
4. После обрыва `RESUME='auto'` продолжит этот запуск. Экспорт можно повторить без обучения.
5. После экспорта скачайте cpu_bundle.zip. CPU и test проверяются до публикации архива.

Состав, ограничения и источники — DATASET.md. Предпросмотр — previews/index.html.
Точное число строк: train {stats["train"]["rows"]}, validation {stats["validation"]["rows"]}, test {stats["test"]["rows"]}.
Уникальных обучающих примеров: {stats["train"]["unique_groups"]}; перестановки кандидатов не увеличивают это число.
LoRA выбирает макеты по тексту и структурным признакам; метки слабые, не экспертные.
Данные подготовлены и проверены локально. Новое обучение и запуск его весов здесь не выполнялись.
"""
    (output / "README.md").write_text(readme, encoding="utf-8")
    source_lines = [
        f"- {s['filename']} — {s['design_family']}, {s['split']}; {s['license']}; {s['url']}"
        for s in inventory
    ]
    dataset = (
        """# Состав и контроль качества

Русскоязычные оригинальные доклады: ClickHouse (несколько авторов/дизайн-систем), ВШЭ, защита JointAdaSpec.
Материалы VK остаются одним семейством только в train. Все материалы ВШЭ — только в test.
Octonica и JointAdaSpec — validation. Внешние авторы и авторство сохранены в PPTX и ссылках ниже.
Не все доклады одной площадки — независимые организации; близкие темы/общая площадка остаются ограничением.

Проблемные wuhua2026 и Glassmorphism полностью исключены. В остальных PPTX удалены слайды с обнаруженными
наложениями строк, низким контрастом крупного текста и выходом текста за границы. Исходники не изменены.
После удаления каждая новая PPTX повторно отрендерена; все её страницы прошли тот же фильтр.
Номера страниц и повторяющиеся колонтитулы удалены из обучающей разметки. Заголовок выбирается по
placeholder, размеру шрифта и положению; номер страницы больше не используется как первый текстовый блок.
Ручной просмотр выборочных страниц дополнен автоматической проверкой всех страниц.

Фильтр не является доказательством безупречного дизайна: текст мельче 12 pt и сложный фон требуют
дополнительной ручной проверки. Оригинальная нумерация, QR, контактные данные авторов могут оставаться
на видимых слайдах: это сохранённые материалы, а не созданные моделью презентации.
Для обучения исключены примеры с большим текстом и неразрешённой геометрией body-зон.
Проверка групп контента, хешей файлов и семейств между train/validation/test обязательна.
Чистые копии сохраняют связи с исходником: original_sha256 и original_slide_map в source_inventory.json.
Англоязычные и китайско-английские дополнительные примеры явно помечены; они только в train.
Качество русского оценивается на validation/test, не участвующих в обучении.

Лицензии источников сохранены в licenses. Материалы организатора не объявляются общедоступными;
это локальный комплект участника. Производные копии отличаются удалёнными слайдами.

## Источники

"""
        + "\n".join(source_lines)
        + "\n"
    )
    (output / "DATASET.md").write_text(dataset, encoding="utf-8")
    manifest = {
        "schema_version": 3,
        "profile": "cpu",
        "task": "layout_candidate_selection",
        "label_kind": "weak_observed_exemplar_not_human_preference",
        "split_unit": "design_family",
        "train_rows": len(parts["train"]),
        "validation_rows": len(parts["validation"]),
        "test_rows": len(parts["test"]),
        "train_unique_groups": stats["train"]["unique_groups"],
        "split_stats": stats,
        "quality_gate": {
            "final_render_failures": 0,
            "known_bad_templates_excluded": ["wuhua2026", "glassmorphism_demo"],
            "filtered_templates": len(inventory),
            "filtered_slides": sum(s["clean_slides"] for s in inventory),
        },
        "layout_prompt_sha256": hashlib.sha256(
            load_prompt("layout").encode()
        ).hexdigest(),
        "files": {
            p.relative_to(output).as_posix(): digest(p)
            for p in sorted(output.rglob("*"))
            if p.is_file()
            and p.name != "manifest.json"
            and "__pycache__" not in p.parts
        },
    }
    write_json(output / "data/manifest.json", manifest)
    print(
        json.dumps(
            {
                "output": str(output),
                "stats": stats,
                "quality": manifest["quality_gate"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
