from __future__ import annotations

import copy
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .analyzer import analyze_template
from .composer import compose_presentation, outline_markdown
from .config import InferenceSettings
from .coverage import coverage_report
from .exporter import export_presentation
from .imagegen import generate_images
from .images import ImageLibrary, attach_images, collect_content_images
from .llm import InferenceClient
from .planner import assign_patterns, load_content, plan_deck
from .powerpoint import inspect_powerpoint_render
from .prompt_config import prompt_manifest
from .qa import compact_plan, inspect_presentation
from .resources import serialized_on_cpu
from .utils import find_latest, read_json, resolve_workspace, unique_dir, write_json


def _qa_pattern_feedback(
    plan: dict[str, Any], qa: dict[str, Any]
) -> tuple[dict[int, set[str]], list[dict[str, Any]]]:
    avoidance: dict[int, set[str]] = {}
    feedback: list[dict[str, Any]] = []
    slides = plan.get("slides", [])
    for issue in qa.get("issues", []):
        if issue.get("severity") not in {"error", "warning"}:
            continue
        slide_number = issue.get("slide")
        if not isinstance(slide_number, int) or not 1 <= slide_number <= len(slides):
            continue
        pattern_id = slides[slide_number - 1].get("pattern_id")
        if pattern_id:
            avoidance.setdefault(slide_number, set()).add(str(pattern_id))
        feedback.append(
            {
                "slide": slide_number,
                "pattern_id": pattern_id,
                "code": issue.get("code"),
                "message": issue.get("message"),
            }
        )
    return avoidance, feedback


def _collect_images(
    content: str | Path,
    source_text: str,
    images: list[str | Path] | tuple[str | Path, ...] | None,
    workspace_path: Path,
) -> tuple[str, ImageLibrary]:
    """Gather document pictures and uploads once per request (all variants)."""
    library = ImageLibrary(workspace_path / "assets" / "images")
    source_text = collect_content_images(content, source_text, library)
    for item in images or ():
        library.add_path(item, source="upload")
    return source_text, library


def _place_images(
    plan: dict[str, Any],
    library: ImageLibrary,
    design: dict[str, Any],
    *,
    offline: bool,
) -> None:
    attach_images(plan, library)
    generate_images(plan, design, library, offline=offline)


def configured_client(*, offline: bool = False) -> InferenceClient | None:
    settings = InferenceSettings.from_env()
    return None if offline or not settings.enabled else InferenceClient(settings)


def resolve_template(
    template: str | Path | None,
    workspace: str | Path | None = None,
    *,
    client: InferenceClient | None = None,
) -> Path:
    workspace_path = resolve_workspace(workspace)
    if template is None:
        latest = find_latest(workspace_path / "templates")
        if latest:
            return latest
        raise FileNotFoundError(
            "No analyzed templates found; provide a .pptx or .pdf template"
        )

    candidate = Path(template).expanduser()
    if candidate.is_dir() and (candidate / "manifest.json").exists():
        return candidate.resolve()
    by_id = workspace_path / "templates" / str(template)
    if by_id.is_dir() and (by_id / "manifest.json").exists():
        return by_id.resolve()
    if candidate.is_file() and candidate.suffix.lower() in {".pptx", ".pdf"}:
        return analyze_template(candidate, workspace=workspace_path, client=client)
    raise FileNotFoundError(
        f"Template id, analysis directory, or .pptx/.pdf file not found: {template}"
    )


def list_templates(workspace: str | Path | None = None) -> list[dict[str, Any]]:
    root = resolve_workspace(workspace) / "templates"
    result = []
    if not root.exists():
        return result
    for directory in sorted(root.iterdir()):
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = read_json(manifest_path)
        manifest["path"] = str(directory.resolve())
        result.append(manifest)
    return result


@serialized_on_cpu
def generate_deck(
    *,
    template: str | Path | None,
    content: str | Path,
    workspace: str | Path | None = None,
    output: str | Path | None = None,
    slide_count: int | None = None,
    offline: bool = False,
    qa_retries: int = 1,
    progress: Callable[[str, int], None] | None = None,
    export_formats: tuple[str, ...] = (),
    images: list[str | Path] | tuple[str | Path, ...] | None = None,
) -> dict[str, Any]:
    def report(stage: str, percent: int) -> None:
        if progress:
            progress(stage, percent)

    started = time.perf_counter()
    workspace_path = resolve_workspace(workspace)
    if slide_count is not None and not 3 <= slide_count <= 100:
        raise ValueError("Slide count must be between 3 and 100")
    requested_output = Path(output).expanduser().resolve() if output else None
    if requested_output and requested_output.suffix.lower() != ".pptx":
        raise ValueError("Output path must use the .pptx extension")
    client = configured_client(offline=offline)
    report("template_analysis", 10)
    template_dir = resolve_template(template, workspace_path, client=client)
    design = read_json(template_dir / "design_system.json")
    catalog = read_json(template_dir / "pattern_catalog.json")
    source_text, source_label = load_content(content)
    if not source_text.strip():
        raise ValueError("Content is empty")
    if len(source_text) > 1_000_000:
        raise ValueError("Content exceeds the 1,000,000 character safety limit")
    source_text, library = _collect_images(content, source_text, images, workspace_path)

    report("content_planning", 35)
    plan = plan_deck(
        source_text,
        pattern_catalog=catalog,
        design_system=design,
        slide_count=slide_count,
        client=client,
        image_catalog=library.catalog(),
    )
    _place_images(plan, library, design, offline=offline)
    plan = assign_patterns(plan, catalog, client=client)
    return _generate_from_plan(
        plan=plan,
        template_dir=template_dir,
        source_text=source_text,
        source_label=source_label,
        workspace_path=workspace_path,
        requested_output=requested_output,
        client=client,
        qa_retries=qa_retries,
        progress=progress,
        export_formats=export_formats,
        started=started,
    )


def _generate_from_plan(
    *,
    plan: dict[str, Any],
    template_dir: Path,
    source_text: str,
    source_label: str,
    workspace_path: Path,
    requested_output: Path | None = None,
    client: InferenceClient | None = None,
    qa_retries: int = 0,
    progress: Callable[[str, int], None] | None = None,
    export_formats: tuple[str, ...] = (),
    started: float | None = None,
    revision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = started if started is not None else time.perf_counter()
    design = read_json(template_dir / "design_system.json")
    catalog = read_json(template_dir / "pattern_catalog.json")

    def report(stage: str, percent: int) -> None:
        if progress:
            progress(stage, percent)

    report("layout_mapping", 55)
    run_dir = unique_dir(
        workspace_path / "presentations", plan.get("title", "presentation")
    )
    (run_dir / "source-content.md").write_text(source_text, encoding="utf-8")
    write_json(run_dir / "deck_plan.json", plan)
    (run_dir / "outline.md").write_text(outline_markdown(plan), encoding="utf-8")

    output_path = requested_output or run_dir / "output.pptx"
    attempts: list[dict[str, Any]] = []
    current_plan = plan
    avoided_patterns: dict[int, set[str]] = {}
    for attempt in range(max(0, qa_retries) + 1):
        report("composition", 65 + min(attempt, 2) * 5)
        compose_result = compose_presentation(
            template_dir=template_dir,
            plan=current_plan,
            output_path=output_path,
            design_system=design,
        )
        report("quality_assurance", 82 + min(attempt, 2) * 5)
        qa = inspect_presentation(
            output_path,
            design_system=design,
            expected_slide_count=len(current_plan["slides"]),
            report_path=run_dir / "qa_report.json",
        )
        render_qa = inspect_powerpoint_render(
            output_path,
            run_dir / f"powerpoint-render-{attempt + 1}",
            expected_slide_count=len(current_plan["slides"]),
        )
        qa["powerpoint_render"] = render_qa
        coverage = coverage_report(source_text, current_plan, output_path)
        write_json(run_dir / "coverage_report.json", coverage)
        qa["content_coverage"] = coverage["status"]
        if coverage["status"] == "needs_review":
            qa["issues"].append(
                {
                    "severity": "warning",
                    "code": "content_coverage_unverified",
                    "message": "Some source units were not matched in the plan/PPTX; see coverage_report.json",
                }
            )
            if qa["status"] == "passed":
                qa["status"] = "warning"
            qa["score"] = min(qa["score"], 96)
        if render_qa["status"] != "skipped":
            qa["issues"].extend(render_qa["issues"])
            qa["score"] = min(qa["score"], int(render_qa["score"] or 0))
            if render_qa["status"] == "failed":
                qa["status"] = "failed"
            elif render_qa["status"] == "warning" and qa["status"] == "passed":
                qa["status"] = "warning"
        write_json(run_dir / "qa_report.json", qa)
        attempt_record: dict[str, Any] = {
            "attempt": attempt + 1,
            "qa_status": qa["status"],
            "qa_score": qa["score"],
        }
        attempts.append(attempt_record)
        needs_retry = qa["status"] == "failed" or qa["score"] < 92
        if not needs_retry or attempt >= qa_retries:
            break
        new_avoidance, feedback = _qa_pattern_feedback(current_plan, qa)
        for slide_number, pattern_ids in new_avoidance.items():
            avoided_patterns.setdefault(slide_number, set()).update(pattern_ids)
        attempt_record["feedback"] = feedback
        current_plan = compact_plan(current_plan)
        current_plan = assign_patterns(
            current_plan,
            catalog,
            avoid_by_slide=avoided_patterns,
            layout_strategy=plan.get("variant", {}).get("id", "balanced"),
        )
        write_json(run_dir / f"deck_plan.retry-{attempt + 1}.json", current_plan)

    template_manifest = read_json(template_dir / "manifest.json")
    report("export", 94)
    exports = export_presentation(
        output_path,
        run_dir / "exports",
        formats=export_formats,
        expected_slide_count=len(current_plan["slides"]),
    )
    from .audit import enrich_audit

    qa = enrich_audit(output_path, qa, current_plan)
    qa["contextual_audit"] = {
        "status": "not_run",
        "reason": "A semantic/VLM audit has not been performed",
    }
    write_json(run_dir / "qa_report.json", qa)
    write_json(run_dir / "deck_plan.final.json", current_plan)
    planner_mode = plan.get("planner", {}).get("mode", "unknown")
    selector_modes = [
        s.get("pattern_selection", {}).get("selector", {}).get("mode", "heuristic")
        for s in current_plan["slides"]
    ]
    manifest = {
        **compose_result,
        "presentation_id": run_dir.name,
        "presentation_dir": str(run_dir.resolve()),
        "template_id": template_manifest["template_id"],
        "template_dir": str(template_dir),
        "source": source_label,
        "inference_configured": client is not None,
        "inference_used": planner_mode == "inference"
        or bool(template_manifest.get("ai_enhanced"))
        or "adapter" in selector_modes,
        "planner_mode": planner_mode,
        "layout_selection": {
            mode: selector_modes.count(mode)
            for mode in ("adapter", "heuristic", "fallback")
        },
        "qa": qa,
        "content_coverage": coverage,
        "attempts": attempts,
        "exports": exports,
        "status": "failed"
        if qa["status"] == "failed" or exports["status"] == "failed"
        else "completed",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "workflow": prompt_manifest(),
        "variant": plan.get("variant"),
        "revision": revision,
        "images": plan.get("image_matching"),
        "image_generation": plan.get("image_generation"),
    }
    write_json(run_dir / "manifest.json", manifest)
    report(manifest["status"], 100)
    return manifest


def run_pipeline(
    *,
    template_path: str | Path,
    content: str | Path,
    workspace: str | Path | None = None,
    output: str | Path | None = None,
    slide_count: int | None = None,
    offline: bool = False,
    export_formats: tuple[str, ...] = (),
    images: list[str | Path] | tuple[str | Path, ...] | None = None,
) -> dict[str, Any]:
    return generate_deck(
        template=template_path,
        content=content,
        workspace=workspace,
        output=output,
        slide_count=slide_count,
        offline=offline,
        export_formats=export_formats,
        images=images,
    )


VARIANTS = {
    "balanced": "Сбалансированный",
    "columns": "Несколько блоков",
    "focus": "Единый акцент",
}


@serialized_on_cpu
def generate_variants(
    *,
    template: str | Path,
    content: str | Path,
    workspace: str | Path | None = None,
    slide_count: int | None = 10,
    offline: bool = False,
    export_formats: tuple[str, ...] = ("pdf", "html"),
    progress: Callable[[str, int], None] | None = None,
    images: list[str | Path] | tuple[str | Path, ...] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    if slide_count is not None and not 3 <= slide_count <= 100:
        raise ValueError("Slide count must be between 3 and 100")
    workspace_path = resolve_workspace(workspace)
    client = configured_client(offline=offline)
    if progress:
        progress("template_analysis", 5)
    template_dir = resolve_template(template, workspace_path, client=client)
    source, label = load_content(content)
    if not source.strip() or len(source) > 1_000_000:
        raise ValueError("Content must contain 1 to 1,000,000 characters")
    design = read_json(template_dir / "design_system.json")
    catalog = read_json(template_dir / "pattern_catalog.json")
    source, library = _collect_images(content, source, images, workspace_path)
    if progress:
        progress("content_planning", 15)
    base = plan_deck(
        source,
        pattern_catalog=catalog,
        design_system=design,
        slide_count=slide_count,
        client=client,
        image_catalog=library.catalog(),
    )
    # Images are matched and generated once, so all variants share content.
    _place_images(base, library, design, offline=offline)
    fingerprint = hashlib.sha256(
        json.dumps(base["slides"], ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    previous: dict[int, set[str]] = {}
    results = []
    signatures = []
    for index, (strategy, label_variant) in enumerate(VARIANTS.items()):
        plan = assign_patterns(
            copy.deepcopy(base),
            catalog,
            layout_strategy=strategy,
            previous_by_slide=previous,
            client=client,
        )
        plan["variant"] = {
            "id": strategy,
            "label": label_variant,
            "axis": "layout_selection",
            "content_sha256": fingerprint,
        }

        def on_progress(
            stage: str, percent: int, offset: int = index, variant: str = strategy
        ) -> None:
            if progress:
                progress(f"{variant}:{stage}", 20 + offset * 26 + int(percent * 0.25))

        result = _generate_from_plan(
            plan=plan,
            template_dir=template_dir,
            source_text=source,
            source_label=label,
            workspace_path=workspace_path,
            client=client,
            export_formats=export_formats,
            progress=on_progress,
            qa_retries=1,
        )
        results.append(result)
        final = read_json(Path(result["presentation_dir"]) / "deck_plan.final.json")
        signatures.append(tuple(s["pattern_id"] for s in final["slides"]))
        for n, slide in enumerate(final["slides"], 1):
            previous.setdefault(n, set()).add(slide["pattern_id"])
    batch_dir = unique_dir(workspace_path / "batches", base.get("title", "variants"))
    batch = {
        "batch_id": batch_dir.name,
        "status": "failed"
        if any(item["status"] == "failed" for item in results)
        else "completed",
        "content_sha256": fingerprint,
        "variants": results,
        "distinct_layout_sequences": len(set(signatures)),
        "diversity_status": "passed" if len(set(signatures)) == 3 else "needs_review",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "workflow": prompt_manifest(),
    }
    write_json(batch_dir / "manifest.json", batch)
    return batch


@serialized_on_cpu
def repair_presentation(
    presentation_id: str,
    issue_ids: list[str],
    *,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Remap only user-selected slides and create an immutable new revision."""
    workspace_path = resolve_workspace(workspace)
    root = (workspace_path / "presentations").resolve()
    directory = (root / presentation_id).resolve()
    if directory.parent != root:
        raise ValueError("Invalid presentation id")
    manifest = read_json(directory / "manifest.json")
    qa = read_json(directory / "qa_report.json")
    indexed = {i["id"]: i for i in qa["issues"]}
    if not issue_ids or any(i not in indexed for i in issue_ids):
        raise ValueError("Select existing audit issues")
    selected = [indexed[i] for i in dict.fromkeys(issue_ids)]
    if any(not i.get("repairable") for i in selected):
        raise ValueError(
            "Selected issues require content review and cannot be remapped automatically"
        )
    plan = read_json(directory / "deck_plan.final.json")
    source = (directory / "source-content.md").read_text(encoding="utf-8")
    template_dir = Path(manifest["template_dir"])
    catalog = read_json(template_dir / "pattern_catalog.json")
    selected_slides = sorted({i["slide"] for i in selected})
    avoidance = {n: {plan["slides"][n - 1]["pattern_id"]} for n in selected_slides}
    remapped = assign_patterns(
        copy.deepcopy(plan),
        catalog,
        avoid_by_slide=avoidance,
        layout_strategy=(plan.get("variant") or {}).get("id", "balanced"),
    )
    for number in selected_slides:
        plan["slides"][number - 1] = remapped["slides"][number - 1]
    return _generate_from_plan(
        plan=plan,
        template_dir=template_dir,
        source_text=source,
        source_label=manifest["source"],
        workspace_path=workspace_path,
        export_formats=tuple(manifest.get("exports", {}).get("artifacts", {})),
        revision={
            "parent_id": presentation_id,
            "selected_issue_ids": issue_ids,
            "selected_slides": selected_slides,
            "action": "remap_layout",
        },
    )
