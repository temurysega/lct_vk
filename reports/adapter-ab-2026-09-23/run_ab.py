"""A/B: heuristic layout selection versus the CPU LoRA adapter.

Both arms share one offline plan per template (same text, visuals and images),
so the only difference is which layout each slide gets. QA retries are off in
both arms because the retry path always remaps heuristically.

Requires a llama.cpp server with the base model and the adapter loaded at
scale 0 (as in compose.cpu.yaml), for example on http://127.0.0.1:8093.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import time
from pathlib import Path

from slide_agent.config import InferenceSettings
from slide_agent.llm import InferenceClient
from slide_agent.planner import assign_patterns, load_content, plan_deck
from slide_agent.service import (
    _collect_images,
    _generate_from_plan,
    _place_images,
    resolve_template,
)
from slide_agent.utils import read_json, write_json

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8093/v1")
    parser.add_argument(
        "--templates", type=Path, default=ROOT.parent / "task+data/Датасет"
    )
    parser.add_argument(
        "--content", type=Path, default=ROOT / "examples/baseline_content.md"
    )
    parser.add_argument("--images", type=Path, default=ROOT / "examples/demo_images")
    parser.add_argument(
        "--workspace", type=Path, default=ROOT / "slide-workspace/adapter-ab-2026-09-23"
    )
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).with_name("summary.json")
    )
    parser.add_argument("--slides", type=int, default=10)
    args = parser.parse_args()

    settings = InferenceSettings(
        base_url=args.server,
        api_key="",
        model="lct-cpu",
        timeout_seconds=240,
        max_retries=1,
        vision_enabled=False,
        backend="llamacpp",
        context_tokens=4096,
        max_output_tokens=2048,
        lora_id=0,
    )
    adapter_client = InferenceClient(settings)
    images = sorted(
        p
        for p in args.images.iterdir()
        if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    summary = {
        "server": args.server,
        "strategy": "balanced",
        "qa_retries": 0,
        "runs": [],
    }
    for template in sorted(args.templates.glob("*.pptx")):
        template_dir = resolve_template(template, args.workspace)
        design = read_json(template_dir / "design_system.json")
        catalog = read_json(template_dir / "pattern_catalog.json")
        source, label = load_content(args.content)
        source, library = _collect_images(args.content, source, images, args.workspace)
        plan = plan_deck(
            source,
            pattern_catalog=catalog,
            design_system=design,
            slide_count=args.slides,
            client=None,
            image_catalog=library.catalog(),
        )
        _place_images(plan, library, design, offline=True)
        run = {"template": template.name, "arms": {}}
        for arm in ("heuristic", "adapter"):
            os.environ["INFERENCE_LAYOUT_MODEL"] = (
                "lct-layout" if arm == "adapter" else ""
            )
            started = time.perf_counter()
            planned = assign_patterns(
                copy.deepcopy(plan),
                catalog,
                layout_strategy="balanced",
                client=adapter_client if arm == "adapter" else None,
            )
            selection_seconds = time.perf_counter() - started
            planned["variant"] = {
                "id": "balanced",
                "label": arm,
                "axis": "layout_selection",
            }
            result = _generate_from_plan(
                plan=planned,
                template_dir=template_dir,
                source_text=source,
                source_label=label,
                workspace_path=args.workspace,
                export_formats=("pdf",),
                qa_retries=0,
            )
            selectors = [s["pattern_selection"]["selector"] for s in planned["slides"]]
            run["arms"][arm] = {
                "presentation_dir": Path(
                    os.path.relpath(result["presentation_dir"], ROOT)
                ).as_posix(),
                "qa": result["qa"]["status"],
                "qa_score": result["qa"]["score"],
                "issues": [
                    {k: i.get(k) for k in ("severity", "code", "slide", "message")}
                    for i in result["qa"]["issues"]
                    if i["severity"] != "info"
                ],
                "exports": result["exports"]["status"],
                "selection_seconds": round(selection_seconds, 2),
                "elapsed_seconds": result["elapsed_seconds"],
                "patterns": [s["pattern_id"] for s in planned["slides"]],
                "selector_modes": [s.get("mode") for s in selectors],
                "adapter_calls": sum(
                    s.get("mode") in {"adapter", "fallback"} for s in selectors
                ),
                "fallback_reasons": [
                    s.get("reason") for s in selectors if s.get("mode") == "fallback"
                ],
            }
            print(
                json.dumps(
                    {
                        "template": template.name,
                        "arm": arm,
                        **{
                            k: run["arms"][arm][k]
                            for k in (
                                "qa",
                                "qa_score",
                                "selection_seconds",
                                "adapter_calls",
                            )
                        },
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        heuristic, adapter = (
            run["arms"]["heuristic"]["patterns"],
            run["arms"]["adapter"]["patterns"],
        )
        run["changed_slides"] = [
            i + 1 for i, (a, b) in enumerate(zip(heuristic, adapter)) if a != b
        ]
        summary["runs"].append(run)
        write_json(args.output, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
