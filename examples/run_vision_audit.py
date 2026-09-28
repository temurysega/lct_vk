"""Run the visual slide audit with the real model over the decks of a dataset run.

The audit is optional in the pipeline (``INFERENCE_RENDER_AUDIT=1``) and takes
45–100 s per deck on an 8 GB GPU, so the timed 3 × 3 run leaves it off; this
script applies it to the finished decks and saves a shareable report:

    python examples/run_vision_audit.py \\
        --report slide-workspace/<run>/dataset_report.json \\
        --out reports/vision-audit-<date>

Model settings come from the ``inference`` block of the run's config; the
server must load the image projector (``deploy/llama/start-llama.ps1`` does
when ``models/gpu/mmproj-F16.gguf`` is present).
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path, PureWindowsPath

from slide_agent import __version__
from slide_agent.prompt_config import prompt_manifest
from slide_agent.service import configured_client
from slide_agent.utils import read_json, write_json
from slide_agent.vision_audit import QUESTIONS, review_rendered_slides

ROOT = Path(__file__).resolve().parents[1]
NAMES = {
    "VK Tech шаблон": "VK Tech",
    "VK_WorkSpace_Клиентская_конференция_Шаблон_03": "VK WorkSpace",
    "Шаблон презентации VK Education": "VK Education",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = read_json(args.report)
    for name, value in report["config"].get("inference", {}).items():
        os.environ[name] = str(value)
    os.environ["INFERENCE_VISION"] = "1"
    os.environ["INFERENCE_RENDER_AUDIT"] = "1"
    client = configured_client(offline=False)
    decks = []
    for run in report["runs"]:
        template = NAMES.get(PureWindowsPath(run["template"]).stem, run["template"])
        for variant in run["batch"]["variants"]:
            directory = Path(variant["presentation_dir"])
            manifest = read_json(directory / "manifest.json")
            plan = read_json(directory / "deck_plan.final.json")
            started = time.perf_counter()
            result = review_rendered_slides(
                manifest["exports"],
                client,
                expected_slide_count=len(plan["slides"]),
                roles=[str(slide.get("role", "content")) for slide in plan["slides"]],
                source=(directory / "source-content.md").read_text(encoding="utf-8"),
            )
            seconds = round(time.perf_counter() - started, 1)
            decks.append(
                {
                    "template": template,
                    "variant": variant["variant"]["id"],
                    "seconds": seconds,
                    **{key: result[key] for key in (
                        "status", "method", "reviewed_slides", "unreviewed_slides",
                        "answers", "observed", "summary", "issues",
                    )},
                }
            )
            print(
                f"{template} {variant['variant']['id']}: {result['status']}, "
                f"{len(result['reviewed_slides'])} slides, {len(result['issues'])} "
                f"suggestions, {seconds} s",
                flush=True,
            )
    totals = {
        name: {
            answer: sum(deck["summary"][name][answer] for deck in decks)
            for answer in ("yes", "no", "n/a")
        }
        for name in QUESTIONS
    }
    summary = {
        "service_version": __version__,
        "workflow": {key: prompt_manifest()[key] for key in ("version", "files", "sha256")},
        "model": report["config"].get("inference", {}).get("INFERENCE_MODEL"),
        "source_run": {
            "git_revision": report.get("git_revision_at_start"),
            "started_at_utc": report.get("started_at_utc"),
        },
        "questions": {
            name: {"appendix_question": number, "text": text}
            for name, (number, text) in QUESTIONS.items()
        },
        "totals": totals,
        "decks": decks,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    write_json(args.out / "summary.json", summary)
    print(f"Report: {args.out / 'summary.json'}")
    return int(any(deck["status"] not in {"reviewed", "partial"} for deck in decks))


if __name__ == "__main__":
    raise SystemExit(main())
