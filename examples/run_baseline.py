"""Reproduce offline structural baseline on the supplied templates.

Run from repository root: python examples/run_baseline.py
Generated files stay under slide-workspace; summary never equates structural
QA with visual acceptance or evaluates the quality of an inference model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from slide_agent.service import run_pipeline


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--templates", type=Path, default=root.parent)
    parser.add_argument(
        "--content", type=Path, default=root / "examples/baseline_content.md"
    )
    parser.add_argument(
        "--output", type=Path, default=root / "slide-workspace/baseline"
    )
    args = parser.parse_args()
    templates = sorted(args.templates.glob("*.pptx"))
    if not templates:
        parser.error("No PPTX templates found")
    output = args.output.resolve() / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    output.mkdir(parents=True, exist_ok=False)
    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "git_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=root, text=True
            ).strip()
        ),
        "content": str(args.content.resolve()),
        "content_sha256": digest(args.content),
        "visual_acceptance": "not_evaluated",
        "runs": [],
    }
    (output / "environment.txt").write_text(
        subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True),
        encoding="utf-8",
    )
    for index, template in enumerate(templates, 1):
        before = digest(template)
        run = {"template": template.name, "sha256_before": before}
        started = time.perf_counter()
        try:
            manifest = run_pipeline(
                template_path=template,
                content=args.content,
                workspace=output / f"template-{index}",
                output=output / f"template-{index}.pptx",
                slide_count=10,
                offline=True,
            )
            qa = manifest["qa"]
            template_dir = Path(manifest["template_dir"])
            template_manifest = json.loads(
                (template_dir / "manifest.json").read_text(encoding="utf-8")
            )
            catalog = json.loads(
                (template_dir / "pattern_catalog.json").read_text(encoding="utf-8")
            )
            run.update(
                {
                    "status": qa["status"],
                    "structural_score": qa["score"],
                    "issues": dict(Counter(i["code"] for i in qa["issues"])),
                    "issue_severity": dict(
                        Counter(i["severity"] for i in qa["issues"])
                    ),
                    "render_status": qa["powerpoint_render"]["status"],
                    "planner_mode": manifest["planner_mode"],
                    "slide_count": qa["slide_count"],
                    "layout_count": template_manifest.get("layout_count"),
                    "pattern_count": len(catalog.get("patterns", [])),
                    "attempts": len(manifest["attempts"]),
                    "presentation_dir": manifest["presentation_dir"],
                    "output": str(output / f"template-{index}.pptx"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - record each failed template and continue the batch
            run.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        run["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        run["sha256_after"] = digest(template)
        run["source_unchanged"] = before == run["sha256_after"]
        summary["runs"].append(run)
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(run, ensure_ascii=False), flush=True)
    print(f"Summary: {output / 'summary.json'}", flush=True)
    return int(
        any(
            r["status"] in {"failed", "error"} or not r["source_unchanged"]
            for r in summary["runs"]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
