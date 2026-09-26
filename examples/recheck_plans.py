"""Recompose saved 3x3 plans after renderer fixes without another LLM call."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from slide_agent.composer import compose_presentation
from slide_agent.exporter import export_presentation
from slide_agent.qa import inspect_presentation
from slide_agent.render_audit import (
    inspect_rendered_contrast,
    inspect_rendered_fill,
    repair_rendered_contrast,
)
from slide_agent.utils import read_json, write_json
from slide_agent.visual_diversity import inspect_visual_diversity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--template-substring", default="")
    parser.add_argument("--variant", default="")
    parser.add_argument("--override-slide", type=int)
    parser.add_argument("--override-pattern")
    args = parser.parse_args()
    source = read_json(args.report)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    result = {"source_report": str(args.report.resolve()), "templates": []}
    for source_run in source["runs"]:
        if args.template_substring not in source_run["template"]:
            continue
        template_result = {"template": source_run["template"], "variants": []}
        previews = []
        roles = []
        for source_variant in source_run["batch"]["variants"]:
            name = source_variant["variant"]["id"]
            if args.variant and name != args.variant:
                continue
            directory = output / Path(source_run["template"]).stem / name
            directory.mkdir(parents=True, exist_ok=True)
            template_dir = Path(source_variant["template_dir"])
            design = read_json(template_dir / "design_system.json")
            plan = read_json(Path(source_variant["output"]).parent / "deck_plan.final.json")
            if args.override_slide is not None and args.override_pattern:
                catalog = read_json(template_dir / "pattern_catalog.json")
                pattern = next(
                    item for item in catalog["patterns"]
                    if item["id"] == args.override_pattern
                )
                target = plan["slides"][args.override_slide - 1]
                for field in ("layout_index", "master_index"):
                    target[field] = pattern[field]
                target["pattern_id"] = pattern["id"]
            if not roles:
                roles = [str(s.get("role", "content")) for s in plan["slides"]]
            pptx = directory / "output.pptx"
            compose_presentation(
                template_dir=template_dir,
                plan=plan,
                output_path=pptx,
                design_system=design,
            )
            qa = inspect_presentation(
                pptx,
                design_system=design,
                expected_slide_count=len(plan["slides"]),
                template_path=template_dir / "original.pptx",
            )
            exported = export_presentation(
                pptx, directory / "exports", formats=("pdf", "html"),
                expected_slide_count=len(plan["slides"]),
            )
            issues = list(qa["issues"])
            if exported["status"] == "passed":
                pdf = directory / "exports" / "output.pdf"
                theme = design.get("colors", {}).get("theme", [])
                text_roles = {"dk1", "lt1", "dk2", "lt2"}
                palette = [c.get("hex", "") for c in theme if c.get("role") in text_roles]
                accents = [c.get("hex", "") for c in theme if c.get("role") not in text_roles]
                for _ in range(3):
                    contrast_report = inspect_rendered_contrast(pdf, pptx)
                    if not repair_rendered_contrast(pptx, contrast_report["issues"], palette, accents):
                        break
                    exported = export_presentation(
                        pptx, directory / "exports", formats=("pdf", "html"),
                        expected_slide_count=len(plan["slides"]),
                    )
                issues.extend(inspect_rendered_contrast(pdf, pptx)["issues"])
                issues.extend(
                    inspect_rendered_fill(
                        pdf, pptx, [str(s.get("role", "content")) for s in plan["slides"]]
                    )["issues"]
                )
            codes = dict(Counter(i["code"] for i in issues if i.get("severity") != "info"))
            template_result["variants"].append(
                {"variant": name, "pptx": str(pptx), "exports": exported["status"], "issues": codes}
            )
            previews.append({"variant": {"id": name}, "exports": exported})
            print(source_run["template"], name, exported["status"], codes, flush=True)
        template_result["diversity"] = (
            inspect_visual_diversity(previews, roles)
            if len(previews) == 3
            else {"status": "not_run"}
        )
        result["templates"].append(template_result)
        write_json(output / "recheck_report.json", result)
    return int(any(
        any(v["issues"] or v["exports"] != "passed" for v in t["variants"])
        or t["diversity"]["status"] not in {"passed", "not_run"}
        for t in result["templates"]
    ))


if __name__ == "__main__":
    raise SystemExit(main())
