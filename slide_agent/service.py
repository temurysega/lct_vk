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
from .contextual_audit import review_content
from .coverage import coverage_report
from .exporter import export_presentation
from .imagegen import generate_images
from .images import ImageLibrary, attach_images, collect_content_images
from .llm import InferenceClient
from .planner import assign_patterns, load_content, plan_deck
from .powerpoint import inspect_powerpoint_render
from .prompt_config import prompt_manifest
from .qa import compact_plan, inspect_presentation
from .render_audit import (
    inspect_rendered_contrast,
    inspect_rendered_fill,
    repair_rendered_contrast,
)
from .resources import serialized_on_cpu
from .utils import find_latest, read_json, resolve_workspace, unique_dir, write_json
from .visual_diversity import inspect_visual_diversity


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


def _hint_cards_for_underfilled(plan: dict[str, Any], qa: dict[str, Any]) -> list[int]:
    """Mark rendered-underfilled slides whose statements can become cards."""
    hinted = []
    for issue in qa.get("issues", []):
        number = issue.get("slide")
        if issue.get("code") != "slide_underfilled" or not isinstance(number, int):
            continue
        slide = plan["slides"][number - 1]
        bullets = [str(item) for item in slide.get("bullets", []) if str(item).strip()]
        if (
            slide.get("role", "content") == "content"
            and not slide.get("visual")
            and not slide.get("body")
            and 2 <= len(bullets) <= 6
            and slide.get("layout_hint") != "cards"
        ):
            slide["layout_hint"] = "cards"
            hinted.append(number)
    return hinted


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
    mode: str = "auto",
    purpose: str | None = None,
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
        mode=mode,
        purpose=purpose,
    )
    _place_images(plan, library, design, offline=offline)
    plan["contextual_audit"] = review_content(source_text, plan, client)
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
    cards_for_underfilled: list[int] = []
    current_plan = plan
    avoided_patterns: dict[int, set[str]] = {}
    for render_pass in range(2):
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
                template_path=template_dir / "original.pptx",
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
            for item in coverage["unsupported_numbers"]:
                qa["issues"].append(
                    {
                        "severity": "warning",
                        "code": "number_not_in_source",
                        "slide": item["slide"],
                        "message": "Числа нет в исходных материалах: "
                        + ", ".join(item["numbers"]),
                    }
                )
                if qa["status"] == "passed":
                    qa["status"] = "warning"
                qa["score"] = min(qa["score"], 96)
            # A brief is expanded by design, so its sentences are not expected
            # verbatim on slides; invented numbers are still flagged above.
            brief_input = current_plan.get("planner", {}).get("input") == "brief"
            if coverage["status"] == "needs_review" and not brief_input:
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
                "render_pass": render_pass + 1,
                "qa_status": qa["status"],
                "qa_score": qa["score"],
            }
            attempts.append(attempt_record)
            needs_retry = qa["status"] == "failed" or qa["score"] < 92 or any(
                issue.get("code") in {"out_of_bounds", "template_overlap", "image_distorted"}
                and issue.get("severity") in {"error", "warning"}
                for issue in qa["issues"]
            )
            if not needs_retry or attempt >= qa_retries:
                break
            new_avoidance, feedback = _qa_pattern_feedback(current_plan, qa)
            for slide_number, pattern_ids in new_avoidance.items():
                avoided_patterns.setdefault(slide_number, set()).update(pattern_ids)
            attempt_record["feedback"] = feedback
            current_plan = compact_plan(current_plan)
            remapped = assign_patterns(
                copy.deepcopy(current_plan),
                catalog,
                avoid_by_slide=avoided_patterns,
                layout_strategy=plan.get("variant", {}).get("id", "balanced"),
            )
            # Only flagged slides change layout: remapping the whole deck would
            # drop the variant's distinct choices and can repeat another variant.
            for slide_number in avoided_patterns:
                current_plan["slides"][slide_number - 1] = remapped["slides"][
                    slide_number - 1
                ]
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
        if exports["status"] == "passed":
            rendered_pdf = run_dir / "exports" / "output.pdf"
            theme = design.get("colors", {}).get("theme", [])
            text_roles = {"dk1", "lt1", "dk2", "lt2"}
            palette = [c.get("hex", "") for c in theme if c.get("role") in text_roles]
            accents = [
                c.get("hex", "") for c in theme if c.get("role") not in text_roles
            ]
            repairs = []
            for _ in range(3):
                visual_audit = inspect_rendered_contrast(rendered_pdf, output_path)
                next_repairs = repair_rendered_contrast(
                    output_path, visual_audit["issues"], palette, accents
                )
                if not next_repairs:
                    break
                repairs.extend(next_repairs)
                exports = export_presentation(
                    output_path,
                    run_dir / "exports",
                    formats=export_formats,
                    expected_slide_count=len(current_plan["slides"]),
                )
                if exports["status"] != "passed":
                    break
            if exports["status"] == "passed":
                visual_audit = inspect_rendered_contrast(rendered_pdf, output_path)
            visual_audit["repairs"] = repairs
            qa["rendered_visual_audit"] = visual_audit
            rendered_issues = list(visual_audit["issues"])
            if exports["status"] == "passed":
                fill_audit = inspect_rendered_fill(
                    rendered_pdf,
                    output_path,
                    [str(s.get("role", "content")) for s in current_plan["slides"]],
                )
                qa["rendered_fill_audit"] = fill_audit
                rendered_issues.extend(fill_audit["issues"])
            qa["issues"].extend(rendered_issues)
            if rendered_issues and qa["status"] == "passed":
                qa["status"] = "warning"
            qa["score"] = max(0, qa["score"] - 4 * len(rendered_issues))

        # The rendered fill decides: slides still below a quarter get their
        # statements as cards once, and the whole deck is audited again.
        if render_pass:
            break
        cards_for_underfilled = _hint_cards_for_underfilled(current_plan, qa)
        if not cards_for_underfilled:
            break
        write_json(run_dir / "deck_plan.cards.json", current_plan)
    qa["structural_score"] = qa["score"]
    contextual = current_plan.get("contextual_audit") or {
        "status": "not_run",
        "reason": "A contextual audit has not been performed",
    }
    qa["contextual_audit"] = contextual
    if contextual.get("status") == "reviewed":
        for item in contextual.get("issues") or []:
            identity = json.dumps(
                [item["slide"], item["code"], item["quote"]], ensure_ascii=False
            )
            qa["issues"].append(
                {
                    "id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16],
                    "severity": "suggestion",
                    "check_type": "contextual",
                    "code": item["code"],
                    "slide": item["slide"],
                    "message": f"{item['reason']} — «{item['quote']}»",
                    "bounds": [],
                    "repairable": False,
                }
            )
        # Model feedback is useful for an editor, but is not deterministic
        # evidence of a defect. Keep the measurable QA score independent.
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
        "cards_for_underfilled_slides": cards_for_underfilled,
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
    mode: str = "auto",
    purpose: str | None = None,
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
        mode=mode,
        purpose=purpose,
    )


VARIANTS = {
    "balanced": "Сбалансированный",
    "columns": "Несколько блоков",
    "focus": "Единый акцент",
}


def _diversity_hybrid_plan(
    results: list[dict[str, Any]], visual: dict[str, Any]
) -> tuple[int, dict[str, Any], dict[str, Any]] | None:
    """Use a third template layout when two rendered variants look alike."""
    if visual.get("status") == "passed" or len(results) != 3:
        return None
    ids = [item["variant"]["id"] for item in results]
    pairs = visual.get("pairs") or []
    required = int(visual.get("required_changed_slides_per_pair", 2))

    def changed(first: str, second: str) -> set[int]:
        for pair in pairs:
            if set(pair.get("variants") or []) == {first, second}:
                return set(pair.get("changed_slides") or [])
        return set()

    for pair in pairs:
        if pair.get("status") == "passed":
            continue
        primary, target = pair["variants"]
        donor = next((item for item in ids if item not in {primary, target}), None)
        if donor is None:
            continue
        target_donor = changed(target, donor)
        candidates = sorted(changed(primary, donor) & target_donor)
        if len(candidates) < required or len(target_donor) - required < required:
            continue
        target_index, donor_index = ids.index(target), ids.index(donor)
        target_plan = read_json(
            Path(results[target_index]["presentation_dir"]) / "deck_plan.final.json"
        )
        donor_plan = read_json(
            Path(results[donor_index]["presentation_dir"]) / "deck_plan.final.json"
        )
        selected = candidates[:required]
        for number in selected:
            destination = target_plan["slides"][number - 1]
            reference = donor_plan["slides"][number - 1]
            for field in ("pattern_id", "layout_index", "master_index", "pattern_selection"):
                if field in reference:
                    destination[field] = copy.deepcopy(reference[field])
        return target_index, target_plan, {
            "source_variant": donor,
            "target_variant": target,
            "slides": selected,
            "reason": "rendered variants had fewer visibly changed slides than required",
        }
    return None


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
    mode: str = "auto",
    purpose: str | None = None,
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
        mode=mode,
        purpose=purpose,
    )
    # Images are matched and generated once, so all variants share content.
    _place_images(base, library, design, offline=offline)
    base["contextual_audit"] = review_content(source, base, client)
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
    visual_diversity = inspect_visual_diversity(
        results, [str(slide.get("role", "content")) for slide in base["slides"]]
    )
    diversity_repair = _diversity_hybrid_plan(results, visual_diversity)
    if diversity_repair:
        target_index, hybrid_plan, repair_record = diversity_repair
        revised = _generate_from_plan(
            plan=hybrid_plan,
            template_dir=template_dir,
            source_text=source,
            source_label=label,
            workspace_path=workspace_path,
            client=client,
            export_formats=export_formats,
            qa_retries=1,
        )
        revised_visual = inspect_visual_diversity(
            [revised if index == target_index else item for index, item in enumerate(results)],
            [str(slide.get("role", "content")) for slide in base["slides"]],
        )
        if revised["qa"]["status"] == "passed" and revised_visual["status"] == "passed":
            revised["diversity_repair"] = repair_record
            results[target_index] = revised
            signatures[target_index] = tuple(
                item["pattern_id"]
                for item in read_json(
                    Path(revised["presentation_dir"]) / "deck_plan.final.json"
                )["slides"]
            )
            visual_diversity = revised_visual
    batch = {
        "batch_id": batch_dir.name,
        "status": "failed"
        if any(item["status"] == "failed" for item in results)
        else "completed",
        "content_sha256": fingerprint,
        "variants": results,
        "distinct_layout_sequences": len(set(signatures)),
        "visual_diversity": visual_diversity,
        "diversity_status": "passed"
        if len(set(signatures)) == 3 and visual_diversity["status"] == "passed"
        else "needs_review",
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
