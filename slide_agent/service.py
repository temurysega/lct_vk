from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .analyzer import analyze_template
from .composer import compose_presentation, outline_markdown
from .config import InferenceSettings
from .coverage import coverage_report
from .llm import InferenceClient
from .planner import assign_patterns, load_content, plan_deck
from .powerpoint import inspect_powerpoint_render
from .qa import compact_plan, inspect_presentation
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
) -> dict[str, Any]:
    def report(stage: str, percent: int) -> None:
        if progress:
            progress(stage, percent)

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

    report("content_planning", 35)
    plan = plan_deck(
        source_text,
        pattern_catalog=catalog,
        design_system=design,
        slide_count=slide_count,
        client=client,
    )
    plan = assign_patterns(plan, catalog)
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
        )
        write_json(run_dir / f"deck_plan.retry-{attempt + 1}.json", current_plan)

    template_manifest = read_json(template_dir / "manifest.json")
    write_json(run_dir / "deck_plan.final.json", current_plan)
    planner_mode = plan.get("planner", {}).get("mode", "unknown")
    manifest = {
        **compose_result,
        "presentation_id": run_dir.name,
        "presentation_dir": str(run_dir.resolve()),
        "template_id": template_manifest["template_id"],
        "template_dir": str(template_dir),
        "source": source_label,
        "inference_configured": client is not None,
        "inference_used": planner_mode == "inference"
        or bool(template_manifest.get("ai_enhanced")),
        "planner_mode": planner_mode,
        "qa": qa,
        "content_coverage": coverage,
        "attempts": attempts,
    }
    write_json(run_dir / "manifest.json", manifest)
    report("completed", 100)
    return manifest


def run_pipeline(
    *,
    template_path: str | Path,
    content: str | Path,
    workspace: str | Path | None = None,
    output: str | Path | None = None,
    slide_count: int | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    client = configured_client(offline=offline)
    template_dir = analyze_template(template_path, workspace=workspace, client=client)
    return generate_deck(
        template=template_dir,
        content=content,
        workspace=workspace,
        output=output,
        slide_count=slide_count,
        offline=offline,
    )
