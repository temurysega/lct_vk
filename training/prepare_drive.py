"""Build a self-contained Drive folder without requiring a GitHub account."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
from pathlib import Path

from slide_agent.layout_selector import candidate_features, selection_payload
from slide_agent.planner import _score_pattern, _slide_requirements
from slide_agent.prompt_config import load_prompt
from slide_agent.service import resolve_template
from slide_agent.utils import read_json, write_json
from training.build_notebook import notebook

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract_examples(template: Path, cache: Path) -> list[dict]:
    directory = resolve_template(template, cache, client=None)
    context = read_json(directory / "context.json")
    catalog = read_json(directory / "pattern_catalog.json")["patterns"]
    by_id = {p["id"]: p for p in catalog}
    rows = []
    for slide in context["slides"]:
        elements = [
            e
            for e in slide.get("text_elements", [])
            if any(p.get("text", "").strip() for p in e.get("paragraphs", []))
        ]
        if not elements:
            continue
        title = next(
            (e for e in elements if "TITLE" in e.get("placeholder_type", "")),
            elements[0],
        )
        title_text = " ".join(p.get("text", "") for p in title.get("paragraphs", []))
        bullets = [
            p["text"].strip()
            for e in elements
            if e is not title
            for p in e.get("paragraphs", [])
            if p.get("text", "").strip()
        ]
        positive = by_id.get(f"slide-{slide['index']}")
        if positive is None:
            continue
        role = positive.get("roles", ["content"])[0]
        sample = {
            "title": title_text,
            "role": role,
            "body": "",
            "bullets": bullets,
            "visual": None,
        }
        requirements = _slide_requirements(sample)
        # Group by content and role, independent of file or slide number. Repeated
        # placeholders shared across template families must not cross the split.
        group_text = re.sub(
            r"\s+", " ", json.dumps([title_text, bullets, role], ensure_ascii=False)
        ).casefold()
        group = hashlib.sha256(group_text.encode()).hexdigest()
        signature = json.dumps(candidate_features(positive), sort_keys=True)
        alternatives = []
        seen = {signature}
        for p in sorted(
            catalog,
            key=lambda p: (
                -_score_pattern(p, requirements, reuse_count=0, avoided=False)[0]
            ),
        ):
            key = json.dumps(candidate_features(p), sort_keys=True)
            if key not in seen:
                seen.add(key)
                alternatives.append(p)
        if len(alternatives) < 2:
            continue
        rows.append(
            {
                "slide": sample,
                "requirements": requirements,
                "positive": positive,
                "alternatives": alternatives[:12],
                "group": group,
                "source_slide": slide["index"],
                "template": template.name,
                "template_sha256": digest(template),
            }
        )
    return rows


def make_record(row: dict, permutation: int) -> dict:
    rng = random.Random(f"{row['template_sha256']}:{row['source_slide']}:{permutation}")
    patterns = [
        row["positive"],
        *rng.sample(row["alternatives"], min(5, len(row["alternatives"]))),
    ]
    rng.shuffle(patterns)
    payload, mapping = selection_payload(row["slide"], row["requirements"], patterns)
    choice = next(
        label
        for label, pattern_id in mapping.items()
        if pattern_id == row["positive"]["id"]
    )
    heuristic = max(
        patterns,
        key=lambda p: _score_pattern(
            p, row["requirements"], reuse_count=0, avoided=False
        )[0],
    )
    heuristic_choice = next(k for k, v in mapping.items() if v == heuristic["id"])
    return {
        "messages": [
            {"role": "system", "content": load_prompt("layout")},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps({"choice": choice})},
        ],
        "provenance": {
            k: row[k]
            for k in (
                "template",
                "template_sha256",
                "source_slide",
                "group",
                "design_family",
                "source_url",
                "license_url",
            )
            if k in row
        },
        "label_kind": "weak_observed_exemplar_not_human_preference",
        "heuristic_choice": heuristic_choice,
        "permutation": permutation,
    }


def split_records(
    rows: list[dict], validation_template: str
) -> tuple[list[dict], list[dict], int]:
    training = [r for r in rows if r["template"] != validation_template]
    training_groups = {r["group"] for r in training}
    seen_validation = set()
    validation = []
    dropped = 0
    for row in rows:
        if row["template"] != validation_template:
            continue
        if row["group"] in training_groups or row["group"] in seen_validation:
            dropped += 1
            continue
        seen_validation.add(row["group"])
        validation.append(make_record(row, 0))
    train_records = [
        make_record(row, permutation) for row in training for permutation in range(3)
    ]
    return train_records, validation, dropped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    default_templates = ROOT.parent / "task+data" / "Датасет"
    if not default_templates.is_dir():
        default_templates = ROOT.parent / "данные"
    parser.add_argument("--templates", type=Path, default=default_templates)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", choices=("gpu", "cpu"), default="gpu")
    parser.add_argument(
        "--validation-template",
        default="VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    )
    args = parser.parse_args()
    templates = sorted(args.templates.glob("*.pptx"))
    if len(templates) < 3 or args.validation_template not in {
        p.name for p in templates
    }:
        parser.error(
            "Need three template families including the chosen validation template"
        )
    output = (
        args.output
        or ROOT.parent / "gdrive" / ("lct_cpu" if args.profile == "cpu" else "lct")
    ).resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = [
        row
        for p in templates
        for row in extract_examples(p, ROOT / "slide-workspace/training-preparation")
    ]
    train, validation, dropped = split_records(rows, args.validation_template)
    if len(train) < 12 or len(validation) < 3:
        raise ValueError(
            "Too few examples after grouped split; review templates before training"
        )
    for name, records in (("train", train), ("validation", validation)):
        path = output / "data" / f"{name}.jsonl"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
    (output / "templates").mkdir(exist_ok=True)
    for p in templates:
        shutil.copy2(p, output / "templates" / p.name)
    (output / "scripts").mkdir(exist_ok=True)
    for name in (
        "train_layout.py",
        "data_utils.py",
        "checkpoints.py",
        "requirements-a100.txt",
    ):
        shutil.copy2(ROOT / "training" / name, output / "scripts" / name)
    builder = notebook
    notebook_name = "BrandDeck_A100_Training_Drive.ipynb"
    readme = "DRIVE_README.md"
    if args.profile == "cpu":
        from training.build_cpu_notebook import notebook as cpu_notebook

        builder = cpu_notebook
        notebook_name = "BrandDeck_CPU_Training_Export.ipynb"
        readme = "CPU_README.md"
        for name in ("check_gguf.py", "export_cpu.py", "cpu_profile.json"):
            shutil.copy2(ROOT / "training" / name, output / "scripts" / name)
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
    # Package current cells even when the separate notebook build was not run.
    (output / notebook_name).write_text(
        json.dumps(builder(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copy2(ROOT / "training" / readme, output / "README.md")
    (output / "weights").mkdir(exist_ok=True)
    (output / "runs").mkdir(exist_ok=True)
    manifest = {
        "schema_version": 1,
        "profile": args.profile,
        "task": "layout_candidate_selection",
        "label_kind": "weak_observed_exemplar_not_human_preference",
        "note": "Permutations are augmentation, not independent examples. No proof of slide quality.",
        "train_rows": len(train),
        "train_unique_groups": len({r["provenance"]["group"] for r in train}),
        "validation_rows": len(validation),
        "validation_template": args.validation_template,
        "validation_duplicate_groups_removed": dropped,
        "source_slides_with_text": len(rows),
        "sources": [{"name": p.name, "sha256": digest(p)} for p in templates],
        "files": {
            p.relative_to(output).as_posix(): digest(p)
            for p in sorted(output.rglob("*"))
            if p.is_file()
            and p != output / "data/manifest.json"
            and p.parts[len(output.parts)] in {"data", "scripts", "templates"}
        },
        "layout_prompt_sha256": hashlib.sha256(
            load_prompt("layout").encode()
        ).hexdigest(),
    }
    write_json(output / "data/manifest.json", manifest)
    print(
        json.dumps(
            {k: v for k, v in manifest.items() if k not in {"files", "sources"}},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Drive folder: {output}")


if __name__ == "__main__":
    main()
