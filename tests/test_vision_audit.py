"""The optional image review must be honest about what it actually inspected."""

from pathlib import Path

from PIL import Image

from slide_agent.config import InferenceSettings
from slide_agent.llm import InferenceClient, InferenceError
from slide_agent.prompt_config import load_prompt
from slide_agent.service import _attach_vision_audit
from slide_agent.vision_audit import QUESTIONS, _bounded_client, review_rendered_slides


def _exports(folder: Path, count: int = 2) -> dict:
    previews = []
    for number in range(1, count + 1):
        path = folder / f"slide-{number}.png"
        Image.new("RGB", (32, 20), "white").save(path)
        previews.append({"slide": number, "path": str(path)})
    return {"status": "passed", "renderer": "libreoffice", "previews": previews}


class FakeVisionClient:
    settings = InferenceSettings(
        base_url="https://example.invalid/v1",
        api_key="private-secret",
        model="vision",
        vision_enabled=True,
        parallel_requests=2,
    )

    def __init__(self, *, failing: set[int] = frozenset()):
        self.failing = failing
        self.calls: list[int] = []
        self.requests: list[str] = []

    def chat_json(self, *, system, user, image_paths, schema, **kwargs):
        number = int(user.split("Slide ")[1].split(" ")[0])
        self.calls.append(number)
        self.requests.append(user)
        assert system == load_prompt("vision-audit")
        assert len(image_paths) == 1 and Path(image_paths[0]).is_file()
        assert schema["properties"]["issues"]["maxItems"] == 3
        assert set(schema["properties"]["answers"]["required"]) == set(QUESTIONS)
        if number in self.failing:
            raise InferenceError("private-secret appeared in a server error")
        answers = dict.fromkeys(QUESTIONS, "yes")
        if number == 1:
            return {"observed": {"title": "Заголовок"}, "answers": answers, "issues": [{
                "code": "small_text",
                "evidence": "chart legend at lower right",
                "reason": "The labels are too small to read at presentation size",
            }]}
        return {"observed": {"title": "Заголовок"}, "answers": answers, "issues": []}


def test_audit_only_runs_with_opt_in_and_final_office_pngs(tmp_path, monkeypatch):
    client = FakeVisionClient()
    exports = _exports(tmp_path)
    disabled = review_rendered_slides(exports, client, expected_slide_count=2)
    assert disabled["status"] == "not_run"
    assert disabled["reviewed_slides"] == []
    assert client.calls == []

    monkeypatch.setenv("INFERENCE_RENDER_AUDIT", "1")
    exports["status"] = "failed"
    assert review_rendered_slides(exports, client, expected_slide_count=2)["status"] == "not_run"
    exports["status"] = "passed"
    exports["renderer"] = "schematic"
    assert review_rendered_slides(exports, client, expected_slide_count=2)["status"] == "not_run"
    assert client.calls == []


def test_every_rendered_slide_can_be_reviewed_and_score_stays_structural(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INFERENCE_RENDER_AUDIT", "1")
    client = FakeVisionClient()
    report = review_rendered_slides(_exports(tmp_path), client, expected_slide_count=2)
    assert report["status"] == "reviewed"
    assert report["reviewed_slides"] == [1, 2]
    assert report["unreviewed_slides"] == []
    assert set(client.calls) == {1, 2}
    assert [(item["slide"], item["code"]) for item in report["issues"]] == [
        (1, "small_text")
    ]

    qa = {"status": "passed", "score": 100, "structural_score": 100, "issues": []}
    _attach_vision_audit(qa, report)
    assert qa["score"] == qa["structural_score"] == 100
    assert qa["status"] == "passed"
    assert qa["issues"][0]["severity"] == "suggestion"
    assert qa["issues"][0]["check_type"] == "vision_model"
    assert qa["issues"][0]["repairable"] is False


def test_model_failure_is_unavailable_and_does_not_leak_server_details(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INFERENCE_RENDER_AUDIT", "1")
    client = FakeVisionClient(failing={1})
    report = review_rendered_slides(_exports(tmp_path), client, expected_slide_count=2)
    assert report["status"] == "unavailable"
    assert report["reviewed_slides"] == []
    assert report["unreviewed_slides"] == [1, 2]
    assert client.calls == [1]
    assert "private-secret" not in str(report)


def test_later_failure_is_partial_and_vision_flag_is_required(tmp_path, monkeypatch):
    monkeypatch.setenv("INFERENCE_RENDER_AUDIT", "1")
    client = FakeVisionClient(failing={2})
    report = review_rendered_slides(_exports(tmp_path), client, expected_slide_count=2)
    assert report["status"] == "partial"
    assert report["reviewed_slides"] == [1]
    assert report["unreviewed_slides"] == [2]
    assert len(report["issues"]) == 1

    client.settings = InferenceSettings("http://example.invalid", "", "text", vision_enabled=False)
    report = review_rendered_slides(_exports(tmp_path), client, expected_slide_count=2)
    assert report["status"] == "not_run"


def test_real_client_is_limited_to_one_short_attempt():
    client = InferenceClient(InferenceSettings(
        base_url="https://example.invalid/v1",
        api_key="",
        model="vision",
        timeout_seconds=120,
        max_retries=3,
    ))
    bounded = _bounded_client(client)
    assert bounded.settings.timeout_seconds == 60
    assert bounded.settings.max_retries == 1
    assert bounded.settings.max_output_tokens == 1100
    assert client.settings.timeout_seconds == 120


def test_answers_follow_appendix_questions_and_every_no_is_a_finding(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("INFERENCE_RENDER_AUDIT", "1")

    class Critic(FakeVisionClient):
        def chat_json(self, *, user, **kwargs):
            number = int(user.split("Slide ")[1].split(" ")[0])
            self.requests.append(user)
            answers = dict.fromkeys(QUESTIONS, "yes")
            if number == 2:
                answers["title_is_conclusion"] = "no"
                answers["no_typos"] = "no"
                answers["has_content"] = "n/a"
                return {"observed": {"title": "Заголовок"}, "answers": answers, "issues": [
                    {"code": "no_typos", "evidence": "«excludes» in the title",
                     "reason": "An English word in a Russian deck"},
                    {"code": "has_content", "evidence": "whole slide",
                     "reason": "A note on an answer that is not «no»"},
                ]}
            return {"observed": {"title": "Заголовок"}, "answers": answers, "issues": []}

    client = Critic()
    report = review_rendered_slides(
        _exports(tmp_path), client, expected_slide_count=2,
        roles=["cover", "content"], source="Бриф: пилот на 6 недель.",
    )
    assert report["status"] == "reviewed"
    assert "Role: cover" in client.requests[0] and "Бриф: пилот" in client.requests[0]
    assert report["answers"]["2"]["title_is_conclusion"] == "no"
    assert report["summary"]["no_typos"] == {"yes": 1, "no": 1, "n/a": 0}
    assert report["questions"]["rows_support_point"]["appendix_question"] == 10
    found = {(item["slide"], item["code"]): item for item in report["issues"]}
    assert set(found) == {(2, "no_typos"), (2, "title_is_conclusion")}
    assert found[(2, "no_typos")]["evidence"] == "«excludes» in the title"
    assert "вопрос 1" in found[(2, "title_is_conclusion")]["evidence"]
    assert report["observed"]["2"] == {"title": "Заголовок"}

    # Without a source the fact question cannot be answered.
    client = Critic()
    report = review_rendered_slides(_exports(tmp_path), client, expected_slide_count=2)
    assert all(slide["facts_in_source"] == "n/a" for slide in report["answers"].values())
    assert "No source is given" in client.requests[0]


def test_a_response_without_every_answer_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("INFERENCE_RENDER_AUDIT", "1")

    class Partial(FakeVisionClient):
        def chat_json(self, **kwargs):
            return {"observed": {"title": "Т"}, "answers": {"no_typos": "yes"}, "issues": []}

    report = review_rendered_slides(_exports(tmp_path), Partial(), expected_slide_count=2)
    assert report["status"] == "unavailable"
    assert report["reviewed_slides"] == []


def test_timeout_of_the_render_audit_is_configurable(monkeypatch):
    monkeypatch.setenv("INFERENCE_RENDER_AUDIT_TIMEOUT", "90")
    client = InferenceClient(InferenceSettings(
        base_url="https://example.invalid/v1", api_key="", model="vision",
        timeout_seconds=120, max_retries=3,
    ))
    assert _bounded_client(client).settings.timeout_seconds == 90
