"""Build a CPU Drive bundle from a curated source inventory, splitting whole families."""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

from training.data_utils import check_split, load_records
from training.prepare_drive import ROOT, digest, extract_examples, make_record
from training.template_families import cluster_sources, fingerprint


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def split_families(rows, assignment, permutations=3):
    """Assign whole families first; remove shared content before augmentation."""
    parts = {name: [] for name in ("train", "validation", "test")}
    content_splits = {}
    for row in rows:
        split = assignment[row["design_family"]]
        content_splits.setdefault(row["group"], set()).add(split)
    collisions = {g for g, splits in content_splits.items() if len(splits) > 1}
    seen = set()
    dropped = Counter()
    for row in rows:
        if row["group"] in collisions:
            dropped["cross_split_shared_content"] += 1
            continue
        key = row["design_family"], row["group"]
        if key in seen:
            dropped["within_family_repeated_content"] += 1
            continue
        seen.add(key)
        split = assignment[row["design_family"]]
        parts[split].extend(
            make_record(row, i) for i in range(permutations if split == "train" else 1)
        )
    check_split(parts["train"], parts["validation"], parts["test"])
    return parts, dict(dropped)


def expanded_notebook(original, folder):
    result = json.loads(original.read_text(encoding="utf-8"))
    for cell in result["cells"]:
        source = "".join(cell["source"]).replace("MyDrive/lct_cpu", "MyDrive/" + folder)
        source = source.replace("**lct_cpu**", "**" + folder + "**")
        source = source.replace("MAX_LENGTH = 3072", "MAX_LENGTH = 4096")
        if cell["cell_type"] == "code":
            cell["execution_count"] = None
            cell["outputs"] = []
        cell["source"] = source.splitlines(keepends=True)
    intro = (
        "# BrandDeck: расширенный набор и проверка новых дизайн-систем\n\n"
        f"Загрузите папку **{folder}** целиком в MyDrive. Исходный ноутбук сохранён. "
        "Используйте отдельную папку: старые веса и чекпоинты относятся к другому набору.\n\n"
        "Train/validation/test разделены по семействам до перестановок кандидатов. "
        "Validation используется для выбора адаптера; test оценивается только после "
        "экспорта GGUF, решение о включении адаптера по нему не меняется. "
        "Не подбирайте настройки по test. Метрики отражают слабую разметку выбора "
        "макета, а не экспертную оценку качества дизайна. Большинство новых материалов "
        "созданы программно/с помощью ИИ; это ограничение разнообразия источников.\n\n"
        "Обучение требует Colab GPU (A100), экспорт и итоговая проверка выполняются на CPU.\n"
    )
    result["cells"][0]["source"] = intro.splitlines(keepends=True)
    preflight = """import sys
sys.path.insert(0, str(ROOT / 'scripts'))
from data_utils import load_records, check_split
parts = {name: load_records(ROOT / 'data' / (name + '.jsonl'))
         for name in ('train', 'validation', 'test')}
check_split(parts['train'], parts['validation'], parts['test'])
assert manifest['split_unit'] == 'design_family'
for name, rows in parts.items():
    print(name, 'строк:', len(rows),
          'групп:', len({r['provenance']['group'] for r in rows}),
          'семейств:', len({r['provenance']['design_family'] for r in rows}))
print('Пересечений нет. Test не участвует в обучении и выборе адаптера.')
"""
    result["cells"].insert(
        3,
        {
            "cell_type": "code",
            "metadata": {},
            "source": preflight.splitlines(keepends=True),
            "outputs": [],
            "execution_count": None,
        },
    )
    for cell in result["cells"]:
        text = "".join(cell["source"])
        if "str(LOCAL_SCRIPTS / 'export_cpu.py')" in text:
            text += """
exported = json.loads((ROOT / 'exports/latest.json').read_text())
test_path = ROOT / Path(exported['archive']).parent / 'cpu/test_report.json'
final_test = json.loads(test_path.read_text())
print('Новые семейства — финальный test (не использовать для подбора параметров):')
for kind in ('baseline', 'adapted'):
    print(kind, {k: v for k, v in final_test[kind].items() if k != 'predictions'})
print('Решение по validation:', final_test['frozen_enable_layout_adapter'])
"""
            cell["source"] = text.splitlines(keepends=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--original-notebook", type=Path, required=True)
    parser.add_argument("--vk-templates", type=Path, default=ROOT.parent)
    parser.add_argument("--seed", type=int, default=20260922)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    source_root = args.inventory.parent
    output = args.output.resolve()
    if (output / "runs").exists() and any((output / "runs").iterdir()):
        raise ValueError("Refusing to overwrite a bundle with training runs")
    sources = inventory["accepted"]
    for source in sources:
        path = source_root / "templates" / source["name"]
        source["fingerprint"] = fingerprint(path)
        if source.get("sha256") and source["sha256"] != source["fingerprint"]["sha256"]:
            raise ValueError("Curated source changed: " + source["name"])
        source["sha256"] = source["fingerprint"]["sha256"]
    edges = cluster_sources(sources)
    families = sorted({s["design_family"] for s in sources})
    if len(families) < 6:
        raise ValueError("Need at least six independent families")
    # Keep the already-seen VK brand in train; never call it an unseen test family.
    eligible = [f for f in families if f != "vk-brand"]
    random.Random(args.seed).shuffle(eligible)
    n = max(2, round(len(families) * 0.15))
    assignment = {f: "train" for f in families}
    assignment.update({f: "test" for f in eligible[:n]})
    assignment.update({f: "validation" for f in eligible[n : 2 * n]})
    # Persist this assignment BEFORE extracting or augmenting any examples.
    write_json(
        output / "data/family_split.json", {"seed": args.seed, "assignment": assignment}
    )
    rows = []
    exclusions = list(inventory.get("excluded", []))
    for source in sources:
        path = source_root / "templates" / source["name"]
        extracted = extract_examples(path, ROOT / "slide-workspace/expanded-training")
        source["eligible_source_slides"] = len(extracted)
        print(
            source["name"],
            source["design_family"],
            assignment[source["design_family"]],
            len(extracted),
            flush=True,
        )
        for row in extracted:
            row.update(
                {k: source[k] for k in ("design_family", "source_url", "license_url")}
            )
        rows.extend(extracted)
    parts, dropped = split_families(rows, assignment)
    if any(len(part) < 3 for part in parts.values()):
        raise ValueError("Insufficient held-out examples after deduplication")
    # Reuse the existing CPU packaging contract (profile, planner smoke, scripts).
    subprocess.run(
        [
            sys.executable,
            "-m",
            "training.prepare_drive",
            "--profile",
            "cpu",
            "--templates",
            str(args.vk_templates),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    old_manifest = json.loads((output / "data/manifest.json").read_text())
    baseline = {
        k: old_manifest[k]
        for k in ("train_rows", "train_unique_groups", "validation_rows")
    }
    for name, records in parts.items():
        (output / "data" / (name + ".jsonl")).write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
        load_records(output / "data" / (name + ".jsonl"))
    for source in sources:
        shutil.copy2(
            source_root / "templates" / source["name"],
            output / "templates" / source["name"],
        )
    shutil.copytree(source_root / "evidence", output / "licenses", dirs_exist_ok=True)
    notebook_name = "BrandDeck_CPU_Expanded_Training_Export.ipynb"
    write_json(
        output / notebook_name, expanded_notebook(args.original_notebook, output.name)
    )
    # Remove the freshly generated legacy notebook to avoid an ambiguous entry point.
    (output / "BrandDeck_CPU_Training_Export.ipynb").unlink()
    stats = {
        name: {
            "rows": len(records),
            "unique_content_groups": len({r["provenance"]["group"] for r in records}),
            "design_families": len({r["provenance"]["design_family"] for r in records}),
            "templates_with_examples": len(
                {r["provenance"]["template_sha256"] for r in records}
            ),
        }
        for name, records in parts.items()
    }
    write_json(
        output / "data/source_inventory.json",
        {"accepted": sources, "excluded": exclusions},
    )
    write_json(
        output / "data/dedup_audit.json",
        {
            "edges": edges,
            "dropped": dropped,
            "pairwise_split_leakage": False,
            "method": "Declared design lineage + colour-independent geometry; conservative structural matches. Not a proof of absence of all stylistic similarity.",
        },
    )
    manifest = {
        k: v
        for k, v in old_manifest.items()
        if k
        not in (
            "files",
            "sources",
            "validation_template",
            "validation_duplicate_groups_removed",
        )
    }
    manifest.update(
        schema_version=2,
        split_unit="design_family",
        seed=args.seed,
        train_rows=len(parts["train"]),
        validation_rows=len(parts["validation"]),
        test_rows=len(parts["test"]),
        train_unique_groups=stats["train"]["unique_content_groups"],
        source_slides_with_text=len(rows),
        split_stats=stats,
        original_bundle_stats=baseline,
        design_families=len(families),
        additional_design_families=len(families) - 1,
        original_notebook_sha256=digest(args.original_notebook),
        sources=[
            {
                k: s[k]
                for k in (
                    "name",
                    "sha256",
                    "design_family",
                    "source_url",
                    "license_url",
                )
            }
            for s in sources
        ],
    )
    manifest["files"] = {
        p.relative_to(output).as_posix(): digest(p)
        for p in sorted(output.rglob("*"))
        if p.is_file()
        and p != output / "data/manifest.json"
        and p.relative_to(output).parts[0]
        in {"data", "scripts", "templates", "licenses"}
    }
    write_json(output / "data/manifest.json", manifest)
    readme = f"""# BrandDeck: расширенный CPU-комплект

Загрузите папку `{output.name}` в Google Drive / MyDrive и откройте `{notebook_name}` в Colab A100.
Запускайте ячейки сверху вниз. Пакет автономный, GitHub не требуется.
Новые запуски находятся в отдельной папке; старые веса не подходят к этому разбиению.

{json.dumps(stats, ensure_ascii=False, indent=2)}

Разбиение: целые семейства, seed={args.seed}; VK всегда в train. Test — только финальная оценка после экспорта.
Отчёты: `data/source_inventory.json`, `data/family_split.json`, `data/dedup_audit.json`.
Атрибуция и исходные лицензии: `licenses/`. Материалы остаются под лицензиями их авторов.
Шаблоны VK предоставлены пользователем; комплект для его обучения, права на публичное распространение VK не заявляются.
PPTX скопированы без изменений. JSONL — извлечённые тексты/признаки и слабые метки выбора макета.
Большинство новых файлов созданы программно или ИИ; семейства дизайна не равны независимым авторам.
Перестановки: 3 на обучающий пример, 1 на validation/test. Они не увеличивают число независимых групп.
Обучение и реальный GGUF-экспорт здесь не запускались: требуется GPU Colab и загрузка базовой модели.
Исходная инструкция CPU сохранена в `CPU_README.md`.
"""
    shutil.copy2(ROOT / "training/CPU_README.md", output / "CPU_README.md")
    (output / "README.md").write_text(readme, encoding="utf-8")
    print(json.dumps(stats, indent=2))
    print(output)


if __name__ == "__main__":
    main()
