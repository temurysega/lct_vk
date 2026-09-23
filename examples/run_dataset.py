"""Generate three variants on every supplied template using a reproducible JSON config."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import time
from pathlib import Path

from slide_agent.service import generate_variants
from slide_agent.utils import read_json, write_json


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=root / "examples/demo.json")
    args = parser.parse_args()
    config = read_json(args.config)
    # Model settings live in the config so one file reproduces the run;
    # secrets such as INFERENCE_API_KEY stay in the environment.
    for name, value in config.get("inference", {}).items():
        if not name.startswith(("INFERENCE_", "IMAGE_")) or name.endswith("API_KEY"):
            parser.error(f"Unsupported inference setting in config: {name}")
        os.environ[name] = str(value)
    workspace = (root / config["workspace"]).resolve()
    templates = sorted((root / config["templates"]).glob("*.pptx"))
    if not templates:
        parser.error("No PPTX templates found in the configured directory")
    report = {
        "config": config,
        "content_sha256": hashlib.sha256(
            (root / config["content"]).read_bytes()
        ).hexdigest(),
        "runs": [],
    }
    images = []
    if config.get("images"):
        # Optional folder of pictures placed on matching slides of every deck.
        images = sorted(
            path
            for path in (root / config["images"]).iterdir()
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        )
    report["images"] = [path.name for path in images]
    for template in templates:
        digest = hashlib.sha256(template.read_bytes()).hexdigest()
        started = time.perf_counter()
        try:
            batch = generate_variants(
                template=template,
                content=root / config["content"],
                workspace=workspace,
                slide_count=config.get("slide_count", 10),
                offline=config.get("offline", True),
                export_formats=tuple(config.get("export_formats", ["pdf", "html"])),
                images=images,
                mode=config.get("mode", "auto"),
                purpose=config.get("purpose"),
            )
            run = {"template": template.name, "source_sha256": digest, "batch": batch}
            plan = read_json(
                Path(batch["variants"][0]["presentation_dir"]) / "deck_plan.json"
            )
            run["planner"] = plan.get("planner")
            run["brief_timings_seconds"] = plan.get("brief", {}).get("timings_seconds")
        except Exception as exc:  # noqa: BLE001 - keep results of the other templates
            run = {"template": template.name, "error": str(exc)}
        run["elapsed_seconds"] = round(time.perf_counter() - started, 1)
        run["source_unchanged"] = (
            digest == hashlib.sha256(template.read_bytes()).hexdigest()
        )
        report["runs"].append(run)
        write_json(workspace / "dataset_report.json", report)
        print(
            json.dumps(
                {
                    "template": template.name,
                    "status": run.get("batch", {}).get("status", "failed"),
                    "planner": run.get("planner"),
                    "brief_timings_seconds": run.get("brief_timings_seconds"),
                    "elapsed_seconds": run["elapsed_seconds"],
                    "error": run.get("error"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    cards = []
    for run in report["runs"]:
        planner = run.get("planner") or {}
        cards.append(
            f"<h2>{html.escape(run['template'])}</h2><p>Планирование: "
            f"{html.escape(str(planner.get('mode', '—')))}, вход "
            f"{html.escape(str(planner.get('input', '—')))}; три варианта за "
            f"{run['elapsed_seconds']} с.</p>"
        )
        for deck in run.get("batch", {}).get("variants", []):
            directory = Path(deck["presentation_dir"])
            links = {"pptx": deck["output"], **deck["exports"]["artifacts"]}
            label = html.escape(deck["variant"]["label"])
            anchor = " · ".join(
                f'<a href="{html.escape(Path(p).relative_to(workspace).as_posix())}">{f.upper()}</a>'
                for f, p in links.items()
            )
            preview = directory / "exports/slide-3.png"
            img = (
                f'<img src="{preview.relative_to(workspace).as_posix()}">'
                if preview.exists()
                else ""
            )
            visuals = deck.get("visuals") or {}
            cards.append(
                f"<article><h3>{label}</h3>{img}<p>{anchor}</p><p>QA: {deck['qa']['status']}; "
                f"{deck['slide_count']} слайдов; {deck['elapsed_seconds']} с.; "
                f"схем {visuals.get('diagrams', 0)}, пиктограмм {visuals.get('pictograms', 0)}, "
                f"изображений {visuals.get('images', 0)}.</p></article>"
            )
    (workspace / "index.html").write_text(
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        "<title>BrandDeck — 3 × 3</title><style>body{font:16px Segoe UI,sans-serif;background:#f3f6fc;"
        "padding:32px;color:#213252}article{display:inline-block;width:30%;vertical-align:top;"
        "background:white;padding:1%;margin:0 1% 24px 0;border-radius:14px}img{width:100%}"
        "a{color:#1768dc}p{font-size:13px}</style><h1>Три шаблона × три варианта</h1>"
        f"<p>Материал: {html.escape(config['content'])}; режим "
        f"{html.escape(str(config.get('mode', 'auto')))}; модель "
        f"{html.escape(os.getenv('INFERENCE_MODEL') or 'нет')}. "
        "Семантический аудит не проводился.</p>" + "\n".join(cards) + "</html>",
        encoding="utf-8",
    )
    print(f"Report: {workspace / 'index.html'}")
    return int(
        any(
            r.get("error")
            or not r["source_unchanged"]
            or r["batch"]["status"] == "failed"
            or r["batch"]["diversity_status"] != "passed"
            for r in report["runs"]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
