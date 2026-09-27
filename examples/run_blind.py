"""Run a pinned, previously unseen template through the full brief workflow.

The source PPTX is MIT licensed and is downloaded into ignored external-fixtures/.
The first run should happen before inspecting the source deck visually or its output.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from slide_agent.service import generate_variants
from slide_agent.utils import write_json

from .run_dataset import source_digest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "2352e3fe338cd13b9a34cac9e824b2c4040c1c7a"
SOURCE_URL = (
    "https://raw.githubusercontent.com/onocom/powerpoint-template/"
    f"{SOURCE_COMMIT}/powerpoint-template-sample.pptx"
)
SOURCE_SHA256 = "42962be01a13cc1f99b52f6880c9b2c42242229ee55bb7890947ecc205642d90"
SOURCE_LICENSE = "https://github.com/onocom/powerpoint-template/blob/2352e3fe338cd13b9a34cac9e824b2c4040c1c7a/LICENSE"
TEMPLATE = ROOT / "external-fixtures/blind-2026-09-27/powerpoint-template-sample.pptx"
WORKSPACE = ROOT / "slide-workspace/blind-2026-09-27"


def main() -> int:
    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    if not TEMPLATE.exists():
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "branddeck-blind-test"})
        with urllib.request.urlopen(request, timeout=60) as response:
            TEMPLATE.write_bytes(response.read())
    digest = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"Pinned template hash changed: {digest}")

    config = json.loads((ROOT / "examples/brief_demo.json").read_text(encoding="utf-8"))
    for name, value in config["inference"].items():
        if name.endswith("API_KEY"):
            continue
        os.environ.setdefault(name, str(value))
    content = ROOT / config["content"]
    images = sorted((ROOT / config["images"]).glob("*.png"))
    before = source_digest(ROOT)
    started = time.perf_counter()
    report = {
        "source_url": SOURCE_URL,
        "source_license": SOURCE_LICENSE,
        "source_commit": SOURCE_COMMIT,
        "source_sha256": digest,
        "content_sha256": hashlib.sha256(content.read_bytes()).hexdigest(),
        "source_digest_before": before,
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "model": os.getenv("INFERENCE_MODEL"),
    }
    try:
        report["batch"] = generate_variants(
            template=TEMPLATE,
            content=content,
            workspace=WORKSPACE,
            slide_count=int(config["slide_count"]),
            offline=False,
            images=images,
            mode="brief",
            purpose=str(config["purpose"]),
            export_formats=("pdf", "html"),
        )
    except Exception as exc:  # noqa: BLE001 - keep the failed blind-test evidence
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    report["source_digest_after"] = source_digest(ROOT)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    write_json(WORKSPACE / "blind_report.json", report)

    batch = report.get("batch") or {}
    summary = {
        "status": batch.get("status", "failed"),
        "diversity": batch.get("diversity_status", "not_run"),
        "elapsed_seconds": report["elapsed_seconds"],
        "error": report.get("error"),
        "variants": [
            {
                "id": item["variant"]["id"],
                "status": item["status"],
                "qa": item["qa"]["status"],
                "images": item["images"]["placed"],
            }
            for item in batch.get("variants", [])
        ],
    }
    print(json.dumps(summary, ensure_ascii=False))
    return int(
        bool(report.get("error"))
        or report["source_digest_before"] != report["source_digest_after"]
        or batch.get("status") != "completed"
        or batch.get("diversity_status") != "passed"
        or report["elapsed_seconds"] > 300
        or any(
            item["qa"]["status"] != "passed" or item["images"]["placed"] < 2
            for item in batch.get("variants", [])
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
