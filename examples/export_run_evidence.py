"""Export a shareable, path-free summary of an ignored dataset run.

Generate:
    python examples/export_run_evidence.py --report slide-workspace/<run>/dataset_report.json

Verify a committed summary and its repository files:
    python examples/export_run_evidence.py --verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "reports" / "final-dataset" / "evidence.json"
TEMPLATE_SLUGS = {
    "VK Tech шаблон.pptx": "vk-tech",
    "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx": "vk-workspace",
    "Шаблон презентации VK Education.pptx": "vk-education",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_digest(root: Path) -> str:
    """Use exactly the fingerprint algorithm in examples/run_dataset.py."""
    digest = hashlib.sha256()
    paths = sorted((root / "slide_agent").rglob("*.py"))
    paths += sorted((root / "slide_agent" / "prompts").glob("*.txt"))
    paths += sorted((root / "slide_agent" / "assets").rglob("*.json"))
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def file_record(path: Path, *, expected: str | None = None) -> dict:
    record = {"path": path.as_posix(), "sha256": sha256(ROOT / path) if (ROOT / path).is_file() else None}
    if expected is not None:
        record["reported_sha256"] = expected
        record["matches_report"] = record["sha256"] == expected
    return record


def artifact_record(path: Path) -> dict:
    if not path.is_file():
        return {"path": path.relative_to(ROOT).as_posix(), "sha256": None, "bytes": None}
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
    }


def make_evidence(report_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    config = report["config"]
    relative_config = Path(report.get("config_path") or "examples/brief_demo.json")
    if relative_config.is_absolute() or ".." in relative_config.parts:
        raise ValueError("Run report config must be inside the repository")
    config_path = ROOT / relative_config
    config_matches = (
        config_path.is_file()
        and json.loads(config_path.read_text(encoding="utf-8")) == config
        and (not report.get("config_sha256") or sha256(config_path) == report["config_sha256"])
    )
    content = file_record(Path(config["content"]), expected=report["content_sha256"])
    image_dir = ROOT / config["images"] if config.get("images") else None
    image_paths = sorted(
        (path for path in image_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES),
        key=lambda path: path.name,
    ) if image_dir and image_dir.is_dir() else []
    reported_images = report.get("images_sha256_before")
    images = [
        file_record(
            path.relative_to(ROOT),
            expected=reported_images.get(path.name) if reported_images is not None else None,
        )
        for path in image_paths
    ]
    current_source = source_digest(ROOT)
    template_dir = ROOT / config["templates"]
    runs = []
    all_artifacts_present = True
    pdf_copies_match = True
    for run in report["runs"]:
        template = template_dir / run["template"]
        template_hash = sha256(template) if template.is_file() else None
        result = {
            "template": run["template"],
            "template_sha256": run.get("source_sha256"),
            "template_sha256_on_disk": template_hash,
            "template_matches_report": template_hash == run.get("source_sha256"),
            "template_unchanged_during_run": run.get("source_unchanged"),
            "elapsed_seconds": run["elapsed_seconds"],
            "planner": run.get("planner"),
            "brief_timings_seconds": run.get("brief_timings_seconds"),
            "status": run.get("batch", {}).get("status", "failed"),
            "diversity_status": run.get("batch", {}).get("diversity_status"),
            "workflow": {
                "version": run.get("batch", {}).get("workflow", {}).get("version"),
                "prompt_sha256": run.get("batch", {}).get("workflow", {}).get("sha256"),
                "pictograms": run.get("batch", {}).get("workflow", {}).get("pictograms"),
            },
            "variants": [],
        }
        for variant in run.get("batch", {}).get("variants", []):
            slug = TEMPLATE_SLUGS.get(run["template"])
            if slug is None:
                raise ValueError(f"No deliverable name for template {run['template']!r}")
            variant_id = variant["variant"]["id"]
            if variant_id not in {"balanced", "columns", "focus"}:
                raise ValueError(f"Unexpected variant id: {variant_id!r}")
            prefix = ROOT / "deliverables" / f"{slug}-{variant_id}"
            artifacts = {
                "pptx": artifact_record(prefix.with_suffix(".pptx")),
                "pdf": artifact_record(prefix.with_suffix(".pdf")),
            }
            all_artifacts_present &= all(item["sha256"] is not None for item in artifacts.values())
            original_pdf = variant.get("exports", {}).get("artifacts", {}).get("pdf")
            original_pdf_hash = sha256(Path(original_pdf)) if original_pdf and Path(original_pdf).is_file() else None
            pdf_match = original_pdf_hash == artifacts["pdf"]["sha256"] if original_pdf_hash else None
            pdf_copies_match &= pdf_match is True
            contextual = variant["qa"].get("contextual_audit") or {}
            vision = variant["qa"].get("vision_audit") or {}
            result["variants"].append({
                "id": variant_id,
                "status": variant.get("status"),
                "slides": variant["slide_count"],
                "elapsed_seconds": variant["elapsed_seconds"],
                "qa_status": variant["qa"]["status"],
                "qa_score": variant["qa"]["score"],
                "editorial_suggestions": len(contextual.get("issues") or []),
                "vision_audit": {
                    "status": vision.get("status", "not_run"),
                    "method": vision.get("method"),
                    "reviewed_slides": len(vision.get("reviewed_slides") or []),
                    "suggestions": len(vision.get("issues") or []),
                    "answers": vision.get("summary"),
                },
                "images_placed": variant.get("images", {}).get("placed", 0),
                "images_available": variant.get("images", {}).get("available", 0),
                "artifacts": artifacts,
                "pdf_matches_run_export": pdf_match,
            })
        runs.append(result)
    rules = {
        "source_unchanged_during_run": report.get("source_unchanged_during_run") is True,
        "source_matched_git_head_at_run_start": report.get("source_matches_git_head_at_start"),
        "inputs_unchanged_during_run": report.get("inputs_unchanged_during_run"),
        "source_matches_current_tree": report.get("source_digest_after") == current_source,
        "config_matches_current_file": bool(config_matches),
        "content_matches_report": content["matches_report"],
        "image_names_match_report": [path.name for path in image_paths] == report.get("images", []),
        "image_hashes_match_report": (
            all(image.get("matches_report") is True for image in images)
            if reported_images is not None else None
        ),
        "templates_match_report": all(run["template_matches_report"] for run in runs),
        "templates_unchanged_during_run": all(run["template_unchanged_during_run"] is True for run in runs),
        "all_artifacts_present": all_artifacts_present,
        "pdf_copies_match_run_exports": pdf_copies_match,
        "all_runs_passed_config_gates": all(
            run["status"] == "completed"
            and run["diversity_status"] == "passed"
            and run["elapsed_seconds"] <= config.get("max_seconds_per_template", float("inf"))
            and len(run["variants"]) == 3
            and all(
                deck["qa_status"] == "passed"
                and deck["images_placed"] >= config.get("min_images_placed", 0)
                for deck in run["variants"]
            )
            for run in runs
        ),
    }
    return {
        "schema_version": 1,
        "source_report_sha256": sha256(report_path),
        "run_started_at_utc": report.get("started_at_utc"),
        "run_finished_at_utc": report.get("finished_at_utc"),
        "git_revision_at_run_start": report.get("git_revision_at_start"),
        "source_digest_at_run": report["source_digest_before"],
        "source_digest_after_run": report.get("source_digest_after"),
        "source_digest_at_export": current_source,
        "config": {
            "path": relative_config.as_posix(),
            "sha256": sha256(config_path) if config_path.is_file() else None,
            "content_kind": config.get("content_kind"),
            "mode": config.get("mode"),
            "offline": config.get("offline"),
            "slide_count": config.get("slide_count"),
            "max_seconds_per_template": config.get("max_seconds_per_template"),
            "min_images_placed": config.get("min_images_placed"),
            "inference_model": config.get("inference", {}).get("INFERENCE_MODEL"),
        },
        "inputs": {"content": content, "images": images, "templates_directory": config["templates"]},
        "runs": runs,
        "checks": rules,
        "limitations": [
            "Source PPTX files are supplied separately from this Git repository.",
            "Structural QA and text-model suggestions do not certify factual accuracy or visual quality.",
            "Model sampling and rendering environment can change generated text, layout, and elapsed time.",
            "Published PPTX files are compacted copies; published PDF files are byte-for-byte copies of run exports.",
        ],
    }


def verify_evidence(evidence: dict) -> list[str]:
    errors = []
    if source_digest(ROOT) != evidence["source_digest_at_run"]:
        errors.append("Current slide_agent source and prompts differ from the measured run")
    config = evidence["config"]
    config_path = ROOT / config["path"]
    if not config_path.is_file() or sha256(config_path) != config["sha256"]:
        errors.append("Config file is missing or changed")
    content = evidence["inputs"]["content"]
    content_path = ROOT / content["path"]
    if not content_path.is_file() or sha256(content_path) != content["reported_sha256"]:
        errors.append("Content file is missing or changed")
    for image in evidence["inputs"]["images"]:
        path = ROOT / image["path"]
        if not path.is_file() or sha256(path) != image["sha256"]:
            errors.append(f"Image missing or changed: {image['path']}")
    template_dir = ROOT / evidence["inputs"]["templates_directory"]
    for run in evidence["runs"]:
        template = template_dir / run["template"]
        if not template.is_file() or sha256(template) != run["template_sha256"]:
            errors.append(f"Source template missing or changed: {run['template']}")
        for deck in run["variants"]:
            for artifact in deck["artifacts"].values():
                path = ROOT / artifact["path"]
                if not path.is_file() or sha256(path) != artifact["sha256"]:
                    errors.append(f"Deliverable missing or changed: {artifact['path']}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Original ignored dataset_report.json")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true", help="Check published evidence against this checkout")
    args = parser.parse_args()
    if args.verify:
        evidence = json.loads(args.out.read_text(encoding="utf-8"))
        errors = verify_evidence(evidence)
        print("Evidence verified" if not errors else "\n".join(errors))
        return int(bool(errors))
    if args.report is None:
        parser.error("--report is required unless --verify is used")
    evidence = make_evidence(args.report.resolve())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Evidence: {args.out}")
    for check, passed in evidence["checks"].items():
        print(f"{'UNVERIFIED' if passed is None else 'PASS' if passed else 'FAIL'} {check}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
