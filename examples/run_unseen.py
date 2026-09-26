"""Run the frozen September 25 unseen-template acceptance check once."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from slide_agent.service import generate_variants

from .run_dataset import source_digest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "85f779a655995a21c4cb01002b5b012c0bac85b6"
SOURCE_URL = (
    "https://raw.githubusercontent.com/addsumtech/slides_maker-site/"
    f"{SOURCE_COMMIT}/templates/decks/en/quarterly-review/template.pptx"
)
SOURCE_SHA256 = "5786268aab6bb8fa30716accf42474589d02801087b37a612fdb15e011dcc872"
TEMPLATE = ROOT / "external-fixtures/unseen-2026-09-25/quarterly-review.pptx"
WORKSPACE = ROOT / "slide-workspace/unseen-2026-09-25"


def main() -> int:
    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    if not TEMPLATE.exists():
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
            TEMPLATE.write_bytes(response.read())
    digest = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"Unseen template hash changed: {digest}")
    content = ROOT / "examples/acceptance_content.md"
    images = sorted((ROOT / "examples/acceptance_images").glob("*.png"))
    implementation_digest_before = source_digest(ROOT)
    started = time.perf_counter()
    batch = generate_variants(
        template=TEMPLATE,
        content=content,
        workspace=WORKSPACE,
        slide_count=10,
        offline=True,
        images=images,
        mode="source",
        export_formats=("pdf", "html"),
    )
    report = {
        "source_url": SOURCE_URL,
        "source_commit": SOURCE_COMMIT,
        "source_sha256": digest,
        "content_sha256": hashlib.sha256(content.read_bytes()).hexdigest(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "source_digest_before": implementation_digest_before,
        "source_digest_after": source_digest(ROOT),
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "batch": batch,
    }
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "unseen_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": batch["status"],
                "diversity": batch["diversity_status"],
                "elapsed_seconds": report["elapsed_seconds"],
                "variants": [
                    {
                        "variant": item["variant"]["id"],
                        "status": item["status"],
                        "qa": item["qa"]["status"],
                        "score": item["qa"]["score"],
                        "images": item["images"]["placed"],
                    }
                    for item in batch["variants"]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0 if (
        batch["status"] == "completed"
        and batch["diversity_status"] == "passed"
        and report["source_digest_before"] == report["source_digest_after"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
