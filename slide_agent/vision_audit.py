"""Optional editorial review of each final, office-rendered slide image.

The model answers the content-validation questions of Appendix 1 of the task
(«Валидация контента»: yes or no, a slide image as input) and names visible
defects. Findings are suggestions: they never certify visual quality and
never change the deterministic QA score. An unavailable model is reported as
such, not as an empty successful review. The instructions live in
``prompts/vision-audit-v*.txt`` and are versioned with the workflow.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any

from .llm import InferenceClient, InferenceError
from .prompt_config import load_prompt

METHOD = "rendered_slide_vision_model_v2"
MAX_SLIDES = 15
MAX_ISSUES_PER_SLIDE = 3
REQUEST_TIMEOUT_SECONDS = 60
REQUEST_MAX_TOKENS = 1100
MAX_SOURCE_CHARS = 3000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Appendix 1 content questions a single slide image can answer. Question 9
# (one language in the whole deck) and 11 (neighbouring slides follow each
# other) concern the deck: the language check and the text audit cover them.
QUESTIONS = {
    "title_is_conclusion": (1, "Заголовок содержит вывод, а не просто называет тему"),
    "content_matches_title": (2, "Содержимое слайда соответствует заголовку"),
    "one_sentence": (3, "Слайд пересказывается одним предложением"),
    "facts_in_source": (4, "Все цифры и факты со слайда есть в исходных материалах"),
    "has_content": (5, "На слайде есть содержание, а не только заголовок"),
    "visuals_relevant": (6, "Картинки и иконки относятся к теме слайда"),
    "no_service_text": (7, "Нет служебного мусора: реплик спикера, кусков промпта"),
    "no_typos": (8, "Текст без опечаток"),
    "rows_support_point": (10, "Все строки таблицы и элементы легенды работают на мысль слайда"),
}
ANSWERS = ("yes", "no", "n/a")
VISUAL_CODES = {
    "clipped_text",
    "small_text",
    "low_contrast",
    "overlap",
    "chart_legibility",
    "visual_irrelevant",
    "reading_order",
    "image_quality",
}
CODES = VISUAL_CODES | set(QUESTIONS)

ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"enum": sorted(CODES)},
        "evidence": {"type": "string", "minLength": 3, "maxLength": 140},
        "reason": {"type": "string", "minLength": 6, "maxLength": 220},
    },
    "required": ["code", "evidence", "reason"],
    "additionalProperties": False,
}
# Copying the title first makes the model read the slide before it answers
# instead of agreeing by default. Fill and word language are measured by the
# deterministic checks, so the model is not asked about them.
OBSERVED_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string", "maxLength": 160}},
    "required": ["title"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"enum": sorted(CODES)},
        "evidence": {"type": "string", "minLength": 3, "maxLength": 140},
        "reason": {"type": "string", "minLength": 6, "maxLength": 220},
    },
    "required": ["code", "evidence", "reason"],
    "additionalProperties": False,
}
EMPTY_AREAS = ("none", "small", "quarter", "half_or_more")
# Observation comes first: copying the title and listing Latin words makes the
# model read the slide before it answers, instead of agreeing by default.
OBSERVED_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 160},
        "non_russian_words": {
            "type": "array",
            "items": {"type": "string", "maxLength": 40},
            "maxItems": 6,
        },
        "empty_area": {"enum": list(EMPTY_AREAS)},
    },
    "required": ["title", "non_russian_words", "empty_area"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "observed": OBSERVED_SCHEMA,
        "answers": {
            "type": "object",
            "properties": {name: {"enum": list(ANSWERS)} for name in QUESTIONS},
            "required": list(QUESTIONS),
            "additionalProperties": False,
        },
        "issues": {
            "type": "array",
            "items": ISSUE_SCHEMA,
            "maxItems": MAX_ISSUES_PER_SLIDE,
        },
    },
    "required": ["observed", "answers", "issues"],
    "additionalProperties": False,
}


def _report(
    status: str,
    expected: list[int],
    reviewed: list[int],
    issues: list[dict[str, Any]],
    reason: str = "",
    answers: dict[int, dict[str, str]] | None = None,
    observed: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    answers = answers or {}
    observed = observed or {}
    summary = {
        name: {
            answer: sum(1 for slide in answers.values() if slide.get(name) == answer)
            for answer in ANSWERS
        }
        for name in QUESTIONS
    }
    result = {
        "status": status,
        "method": METHOD,
        "reviewed_slides": sorted(reviewed),
        "unreviewed_slides": sorted(set(expected) - set(reviewed)),
        "questions": {
            name: {"appendix_question": number, "text": text}
            for name, (number, text) in QUESTIONS.items()
        },
        "answers": {str(number): answers[number] for number in sorted(answers)},
        "observed": {str(number): observed[number] for number in sorted(observed)},
        "summary": summary,
        "issues": sorted(issues, key=lambda item: (item["slide"], item["code"])),
        "limitations": (
            "Model answers and findings are suggestions for a person to confirm; "
            "one image cannot show the whole deck, and a model can miss or "
            "invent a problem"
        ),
    }
    if reason:
        result["reason"] = reason
    return result


def _request_timeout() -> int:
    try:
        value = int(os.getenv("INFERENCE_RENDER_AUDIT_TIMEOUT", REQUEST_TIMEOUT_SECONDS))
    except ValueError:
        return REQUEST_TIMEOUT_SECONDS
    return max(5, value)


def _bounded_client(client: InferenceClient) -> InferenceClient:
    """Use one attempt and a bounded request timeout for this optional stage."""
    if not isinstance(client, InferenceClient):
        return client
    settings = replace(
        client.settings,
        timeout_seconds=max(1, min(client.settings.timeout_seconds, _request_timeout())),
        max_retries=1,
        max_output_tokens=(
            min(client.settings.max_output_tokens, REQUEST_MAX_TOKENS)
            if client.settings.max_output_tokens > 0
            else REQUEST_MAX_TOKENS
        ),
    )
    return InferenceClient(settings)


def _valid_png(path: Path) -> bool:
    try:
        with path.open("rb") as image:
            return image.read(len(PNG_SIGNATURE)) == PNG_SIGNATURE
    except OSError:
        return False


def _review_one(
    number: int,
    path: Path,
    client: InferenceClient,
    *,
    total: int,
    role: str,
    source: str,
) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    request = f"Slide {number} of {total}. Role: {role}."
    if source:
        request += "\n\nSource brief:\n" + source[:MAX_SOURCE_CHARS]
    else:
        request += "\n\nNo source is given: answer facts_in_source with n/a."
    response = client.chat_json(
        system=load_prompt("vision-audit"),
        user=request + "\n\nReturn only JSON.",
        image_paths=[str(path)],
        schema=SCHEMA,
        max_tokens=REQUEST_MAX_TOKENS,
        temperature=0,
    )
    answers = response.get("answers")
    raw = response.get("issues")
    seen_on_slide = response.get("observed")
    if (
        not isinstance(seen_on_slide, dict)
        or not isinstance(seen_on_slide.get("title"), str)
        or not isinstance(answers, dict)
        or set(answers) != set(QUESTIONS)
        or any(answer not in ANSWERS for answer in answers.values())
        or not isinstance(raw, list)
        or len(raw) > MAX_ISSUES_PER_SLIDE
    ):
        raise ValueError("Invalid vision response")
    if not source:
        answers = {**answers, "facts_in_source": "n/a"}
    accepted: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("Invalid vision issue")
        code, evidence, reason = (
            item.get("code"),
            item.get("evidence"),
            item.get("reason"),
        )
        if (
            code not in CODES
            or not isinstance(evidence, str)
            or not isinstance(reason, str)
            or not 3 <= len(evidence.strip()) <= 140
            or not 6 <= len(reason.strip()) <= 220
        ):
            raise ValueError("Invalid vision issue")
        if code in QUESTIONS and answers[code] != "no":
            continue  # the answer is the verdict; a stray note is not a finding
        key = (code, evidence.strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        accepted.append(
            {
                "slide": number,
                "code": code,
                "evidence": " ".join(evidence.split()),
                "reason": " ".join(reason.split()),
            }
        )
    # Every "no" is a finding, even when the model gave no evidence for it.
    named = {item["code"] for item in accepted}
    for name, answer in answers.items():
        if answer == "no" and name not in named:
            appendix, text = QUESTIONS[name]
            accepted.append(
                {
                    "slide": number,
                    "code": name,
                    "evidence": f"Приложение 1, вопрос {appendix}",
                    "reason": f"Модель ответила «нет»: {text}",
                }
            )
    observation = {"title": " ".join(seen_on_slide["title"].split())[:160]}
    return dict(answers), accepted, observation


def review_rendered_slides(
    exports: dict[str, Any],
    client: InferenceClient | None,
    *,
    expected_slide_count: int,
    roles: list[str] | None = None,
    source: str = "",
) -> dict[str, Any]:
    """Review each final PNG only when the actual office export succeeded.

    A first-image probe prevents a text-only model from receiving every slide.
    The remaining requests use the server's configured parallelism, capped at
    three; each has one attempt and a bounded timeout
    (``INFERENCE_RENDER_AUDIT_TIMEOUT``, 60 s by default).
    """
    expected = list(range(1, max(0, expected_slide_count) + 1))
    if os.getenv("INFERENCE_RENDER_AUDIT", "0").strip().lower() not in {
        "1", "true", "on", "yes"
    }:
        return _report("not_run", expected, [], [], "Rendered vision audit is disabled")
    if client is None or not getattr(client.settings, "vision_enabled", False):
        return _report("not_run", expected, [], [], "A vision-capable inference model is not configured")
    if exports.get("status") != "passed" or exports.get("renderer") != "libreoffice":
        return _report("not_run", expected, [], [], "A final office-rendered export is unavailable")

    paths: dict[int, Path] = {}
    for preview in exports.get("previews") or []:
        if not isinstance(preview, dict):
            continue
        number, raw_path = preview.get("slide"), preview.get("path")
        if (
            type(number) is int
            and number in expected
            and isinstance(raw_path, str)
            and Path(raw_path).suffix.lower() == ".png"
            and _valid_png(Path(raw_path))
        ):
            paths[number] = Path(raw_path)
    candidates = sorted(paths)[:MAX_SLIDES]
    if not candidates:
        return _report("not_run", expected, [], [], "No final rendered PNG previews are available")

    roles = roles or []
    bounded = _bounded_client(client)

    def review(
        number: int,
    ) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
        role = roles[number - 1] if number <= len(roles) else "content"
        return _review_one(
            number,
            paths[number],
            bounded,
            total=len(expected),
            role=role,
            source=source,
        )

    reviewed: list[int] = []
    findings: list[dict[str, Any]] = []
    answers: dict[int, dict[str, str]] = {}
    observed: dict[int, dict[str, Any]] = {}
    first = candidates.pop(0)
    try:
        answers[first], issues, observed[first] = review(first)
    except (InferenceError, OSError, TypeError, ValueError, KeyError):
        return _report("unavailable", expected, [], [], "The vision model did not review the rendered image")
    findings.extend(issues)
    reviewed.append(first)

    if candidates:
        workers = min(3, max(1, int(getattr(client.settings, "parallel_requests", 1))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {pool.submit(review, number): number for number in candidates}
            for future in as_completed(pending):
                number = pending[future]
                try:
                    answers[number], issues, observed[number] = future.result()
                except (InferenceError, OSError, TypeError, ValueError, KeyError):
                    continue
                findings.extend(issues)
                reviewed.append(number)
    status = "reviewed" if len(reviewed) == len(expected) else "partial"
    reason = (
        "Some slides could not be reviewed or exceeded the 15-slide audit limit"
        if status == "partial"
        else ""
    )
    return _report(status, expected, reviewed, findings, reason, answers, observed)
