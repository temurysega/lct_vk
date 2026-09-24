"""Reproduce an independent, previously untrained PPTX stress test."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from pathlib import Path

from slide_agent.service import generate_variants

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://raw.githubusercontent.com/shbernal/ts-pptx/master/test/read/fixtures/mixed.pptx"
SOURCE_SHA256 = "db80910224b01b46cb7e8c29e183b59d128a18fd8e0c651558907230874693b9"
TEMPLATE = ROOT / "external-fixtures/heldout-2026-09-24/mixed.pptx"
WORKSPACE = ROOT / "slide-workspace/heldout-2026-09-24"


def main() -> int:
    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    if not TEMPLATE.exists():
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
            TEMPLATE.write_bytes(response.read())
    digest = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"Held-out PPTX hash changed: {digest}")
    images = sorted((ROOT / "examples/acceptance_images").glob("*.png"))
    started = time.perf_counter()
    batch = generate_variants(
        template=TEMPLATE,
        content=ROOT / "examples/acceptance_content.md",
        workspace=WORKSPACE,
        slide_count=10,
        offline=True,
        images=images,
        mode="source",
        export_formats=("pdf", "html"),
    )
    report = {
        "source_url": SOURCE_URL,
        "source_sha256": digest,
        "source_license": "MIT; see ts-pptx/test/read/fixtures/README.md",
        "content_sha256": hashlib.sha256((ROOT / "examples/acceptance_content.md").read_bytes()).hexdigest(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "batch": batch,
    }
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "heldout_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": batch["status"],
                "diversity": batch["diversity_status"],
                "seconds": report["elapsed_seconds"],
                "variants": [
                    {
                        "status": item["status"],
                        "qa": item["qa"]["status"],
                        "images": item["images"]["placed"],
                        "seconds": item["elapsed_seconds"],
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
