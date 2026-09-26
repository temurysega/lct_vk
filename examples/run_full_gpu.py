"""Benchmark brief, images, three variants and exports on one VK template."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from slide_agent.service import generate_variants

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "examples/brief_demo_2026_09_25.json"
WORKSPACE = Path(
    os.getenv(
        "BRANDDECK_BENCH_WORKSPACE",
        str(ROOT / "slide-workspace/brief-full-gpu-2026-09-25"),
    )
).resolve()


def source_digest() -> str:
    digest = hashlib.sha256()
    files = sorted((ROOT / "slide_agent").rglob("*.py"))
    files += sorted((ROOT / "slide_agent/prompts").glob("*.txt"))
    for path in files:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    for name, value in config["inference"].items():
        os.environ[name] = str(value)
    template = next((ROOT / config["templates"]).glob("VK Tech*.pptx"))
    content = ROOT / config["content"]
    images = sorted((ROOT / "examples/acceptance_images").glob("*.png"))
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    before = source_digest()
    started = time.perf_counter()
    batch = generate_variants(
        template=template,
        content=content,
        workspace=WORKSPACE,
        slide_count=config["slide_count"],
        offline=False,
        images=images,
        mode="brief",
        purpose=config["purpose"],
        export_formats=tuple(config["export_formats"]),
    )
    report = {
        "code_commit": commit,
        "source_digest_before": before,
        "source_digest_after": source_digest(),
        "template_sha256": hashlib.sha256(template.read_bytes()).hexdigest(),
        "brief_sha256": hashlib.sha256(content.read_bytes()).hexdigest(),
        "images": [path.name for path in images],
        "inference": config["inference"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "batch": batch,
    }
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "full_gpu_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": batch["status"],
                "elapsed_seconds": report["elapsed_seconds"],
                "source_unchanged": before == report["source_digest_after"],
                "variants": [
                    {
                        "variant": item["variant"]["id"],
                        "seconds": item["elapsed_seconds"],
                        "qa": item["qa"]["score"],
                        "images": item["images"]["placed"],
                    }
                    for item in batch["variants"]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0 if batch["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
