import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from examples.create_demo_assets import create_template
from slide_agent.audit import _language_issues
from slide_agent.brief import (
    detect_purpose,
    outline_schema,
    plan_from_brief,
    slide_schema,
    use_brief_mode,
)
from slide_agent.config import InferenceSettings
from slide_agent.coverage import unsupported_numbers
from slide_agent.llm import InferenceClient, InferenceError
from slide_agent.planner import plan_deck
from slide_agent.prompt_config import load_prompt
from slide_agent.service import generate_deck

BRIEF = (
    "Назначение: фича. ИИ-помощник отвечает сотрудникам по регламентам. "
    "Сейчас поиск инструкции занимает до 30 минут. Пилот: 2 подразделения, "
    "150 сотрудников, 6 недель. Просим одобрить пилот."
)
VISUALS = ["none", "process", "icon_grid", "none", "table", "none"]


def _outline(count: int, *, duplicate: bool = False) -> dict:
    slides = [
        {
            "title": "Повтор" if duplicate and 0 < i < 3 else f"Вывод {i}",
            "role": "content",
            "goal": f"Цель слайда {i}",
            "visual": VISUALS[i % len(VISUALS)],
        }
        for i in range(count)
    ]
    slides[0]["role"], slides[-1]["role"] = "cover", "closing"
    return {
        "language": "ru",
        "title": "ИИ-помощник",
        "subtitle": "Пилот",
        "slides": slides,
    }


def _slide_content(schema: dict) -> dict:
    properties = schema["properties"]
    content = {
        "subtitle": "",
        "bullets": ["Ответ за минуту", "Ссылка на регламент"][
            : properties["bullets"]["maxItems"]
        ],
        "speaker_notes": "Коротко.",
    }
    visual = properties.get("visual")
    if visual:
        kind = visual["properties"]["type"]["enum"][0]
        if kind == "table":
            content["visual"] = {
                "type": kind,
                "headers": ["Этап", "Срок"],
                "rows": [["Сбор", "2 недели"], ["Пилот", "6 недель"]],
            }
        else:
            content["visual"] = {
                "type": kind,
                "items": [
                    {"label": "Вопрос", "detail": "Сотрудник пишет вопрос"},
                    {"label": "Поиск", "detail": "Помощник находит регламент"},
                    {"label": "Ответ", "detail": "Ответ со ссылкой"},
                ],
            }
    return content


class FakeClient:
    def __init__(self, *, duplicate_first: bool = False, fail_slide: int | None = None):
        self.settings = SimpleNamespace(parallel_requests=3)
        self.duplicate_first = duplicate_first
        self.fail_slide = fail_slide
        self.outline_requests: list[dict] = []
        self.schemas: list[dict] = []

    def chat_json(self, *, system, user, schema=None, **_kwargs):
        request = json.loads(user)
        self.schemas.append(schema)
        if system == load_prompt("brief-outline"):
            self.outline_requests.append(request)
            count = request["requested_slide_count"]
            first = len(self.outline_requests) == 1
            return _outline(count, duplicate=self.duplicate_first and first)
        if request["slide_number"] == self.fail_slide:
            raise InferenceError("timeout")
        return _slide_content(schema)


def test_brief_builds_exact_outline_and_content():
    client = FakeClient()
    plan = plan_from_brief(BRIEF, client=client, slide_count=12)
    slides = plan["slides"]
    assert len(slides) == 12
    assert slides[0]["role"] == "cover" and slides[-1]["role"] == "closing"
    assert [s["title"] for s in slides] == [f"Вывод {i}" for i in range(12)]
    outline_schema_sent = client.schemas[0]["properties"]["slides"]
    assert outline_schema_sent["minItems"] == outline_schema_sent["maxItems"] == 12
    diagram = next(s for s in slides if (s["visual"] or {}).get("type") == "process")
    assert diagram["bullets"] == []
    assert plan["brief"]["purpose"] == "feature"
    assert plan["brief"]["outline_problems"] == []
    assert "metric_cards" not in plan["brief"]["visual_types"]
    assert "timeline" not in plan["brief"]["visual_types"]


def test_brief_reserves_relevant_uploaded_picture_before_writing_slide():
    class PictureClient(FakeClient):
        def chat_json(self, *, system, user, schema=None, **kwargs):
            if system == load_prompt("brief-outline"):
                outline = _outline(json.loads(user)["requested_slide_count"])
                outline["slides"][2].update(
                    title="Сервер работает внутри контура",
                    goal="Данные хранятся внутри контура компании",
                )
                return outline
            return super().chat_json(system=system, user=user, schema=schema, **kwargs)

    catalog = [
        {"id": "server-room", "label": "внутренний контур серверная", "context": ""}
    ]
    plan = plan_from_brief(
        BRIEF, client=PictureClient(), slide_count=6, image_catalog=catalog
    )
    slide = plan["slides"][2]
    assert slide["visual"] == {"type": "image", "asset_id": "server-room"}
    assert len(slide["bullets"]) >= 2
    assert "image" in plan["brief"]["visual_types"]


def test_outline_with_repeated_titles_is_requested_again():
    client = FakeClient(duplicate_first=True)
    plan = plan_from_brief(BRIEF, client=client, slide_count=8)
    assert len(client.outline_requests) == 2
    assert client.outline_requests[1]["fix_previous_outline"]
    assert len({s["title"] for s in plan["slides"]}) == 8


def test_failed_slide_keeps_outline_goal():
    plan = plan_from_brief(BRIEF, client=FakeClient(fail_slide=3), slide_count=6)
    assert plan["slides"][2]["bullets"] == ["Цель слайда 2"]
    assert plan["brief"]["slide_failures"][0]["slide"] == 3


def test_too_long_slide_text_is_requested_again():
    class Wordy(FakeClient):
        def chat_json(self, *, system, user, schema=None, **kwargs):
            request = json.loads(user)
            if (
                system == load_prompt("brief-slide")
                and "fix_previous_answer" not in request
            ):
                return {
                    "subtitle": "",
                    "bullets": ["слово " * 20, "коротко"],
                    "speaker_notes": "",
                }
            return super().chat_json(system=system, user=user, schema=schema, **kwargs)

    plan = plan_from_brief(BRIEF, client=Wordy(), slide_count=6)
    plain = [s for s in plan["slides"][1:-1] if not s["visual"]]
    assert plain and all(len(b.split()) <= 15 for s in plain for b in s["bullets"])


def test_brief_mode_selection():
    client = FakeClient()
    assert use_brief_mode(BRIEF, "auto", client)
    assert not use_brief_mode(BRIEF, "auto", None)
    assert not use_brief_mode(BRIEF, "source", client)
    long_source = "# A\ntext\n# B\ntext\n# C\ntext"
    assert not use_brief_mode(long_source, "auto", client)
    assert not use_brief_mode("x" * 3000, "auto", client)
    with pytest.raises(ValueError):
        use_brief_mode(BRIEF, "brief", None)
    with pytest.raises(ValueError):
        use_brief_mode(BRIEF, "other", client)


def test_purpose_detection():
    assert detect_purpose("Назначение: продукт. Что-то про фичу") == "product"
    assert detect_purpose("Предлагаем инициативу по обучению") == "initiative"
    assert detect_purpose("Статус проекта миграции") == "project"
    assert detect_purpose("Без подсказок") == "project"


def test_schemas_bound_bullets_and_items():
    assert slide_schema("content", "process")["properties"]["bullets"]["maxItems"] == 0
    assert "visual" not in slide_schema("cover", "process")["properties"]
    plain = slide_schema("content", "none")["properties"]
    assert (plain["bullets"]["minItems"], plain["bullets"]["maxItems"]) == (2, 4)
    matrix = slide_schema("content", "matrix")["properties"]["visual"]
    assert matrix["properties"]["items"]["minItems"] == 4
    assert outline_schema(10, ["none"])["properties"]["slides"]["maxItems"] == 10


def test_plan_deck_uses_brief_and_reports_failure():
    plan = plan_deck(
        BRIEF,
        pattern_catalog={"patterns": []},
        design_system={},
        slide_count=10,
        client=FakeClient(),
        purpose="feature",
    )
    assert plan["planner"] == {"mode": "inference", "input": "brief"}
    assert len(plan["slides"]) == 10
    assert plan["brief"]["purpose"] == "feature"

    class Broken(FakeClient):
        def chat_json(self, **kwargs):
            raise InferenceError("server down")

    with pytest.raises(ValueError, match="brief"):
        plan_deck(
            BRIEF,
            pattern_catalog={"patterns": []},
            design_system={},
            slide_count=10,
            client=Broken(),
            mode="brief",
        )


def test_numbers_missing_from_source_are_reported():
    plan = {
        "slides": [
            {"title": "Поиск занимает 30 минут", "bullets": ["3 шага", "рост на 45%"]},
            {
                "title": "Метрики",
                "bullets": [],
                "visual": {
                    "type": "metric_cards",
                    "items": [{"label": "Пилот", "value": "1 200"}],
                },
                "speaker_notes": "в заметках 999 не проверяется",
            },
        ]
    }
    source = "Поиск занимает до 30 минут. Пилот на 1200 сотрудников."
    assert unsupported_numbers(source, plan) == [{"slide": 1, "numbers": ["45"]}]


def test_language_checks():
    def codes(text: str, lang: str = "ru") -> list[str]:
        plan = {"language": lang}
        return [i["code"] for i in _language_issues(text, 1, "BrandDeck Text", plan)]

    assert codes("Сolution для команды") == ["mixed_script_word"]
    assert codes("Риски будут handled позже") == ["language_mix"]
    assert codes("Модель Qwen в VK WorkSpace, API, https://example.com") == []
    assert codes("Risks are handled", "en") == []
    assert codes("Risks and Mitigation") == ["language_mix"]


def test_empty_diagram_becomes_bullets_and_is_retried():
    class Empty(FakeClient):
        def chat_json(self, *, system, user, schema=None, **kwargs):
            if system == load_prompt("brief-slide") and schema.get(
                "properties", {}
            ).get("visual"):
                kind = schema["properties"]["visual"]["properties"]["type"]["enum"][0]
                empty = {"label": "", "detail": ""}
                return {
                    "subtitle": "",
                    "bullets": [],
                    "speaker_notes": "",
                    "visual": {
                        "type": kind,
                        "items": [empty, empty],
                        "headers": [],
                        "rows": [],
                    },
                }
            return super().chat_json(system=system, user=user, schema=schema, **kwargs)

    plan = plan_from_brief(BRIEF, client=Empty(), slide_count=6)
    for slide in plan["slides"][1:-1]:
        assert slide["bullets"] or slide["visual"], slide
    diagram_slide = plan["slides"][1]
    assert diagram_slide["visual"] is None
    assert diagram_slide["bullets"] == ["Цель слайда 1"]


class _SchemaHandler(BaseHTTPRequestHandler):
    requests: ClassVar[list[dict]] = []
    reject_schema = False

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        type(self).requests.append(request)
        kind = request.get("response_format", {}).get("type")
        if kind == "json_schema" and type(self).reject_schema:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":"json_schema unsupported"}')
            return
        user = json.loads(request["messages"][1]["content"])
        if "requested_slide_count" in user:
            body = _outline(user["requested_slide_count"])
        else:
            schema = request["response_format"].get("json_schema", {}).get("schema")
            body = _slide_content(schema or slide_schema("content", "none"))
        payload = json.dumps(
            {
                "choices": [
                    {"message": {"content": json.dumps(body, ensure_ascii=False)}}
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


@pytest.fixture
def schema_server():
    _SchemaHandler.requests = []
    _SchemaHandler.reject_schema = False
    server = HTTPServer(("127.0.0.1", 0), _SchemaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=2)


def test_client_sends_schema_and_extra_body(schema_server):
    client = InferenceClient(
        InferenceSettings(
            base_url=f"http://127.0.0.1:{schema_server.server_port}/v1",
            api_key="",
            model="m",
            extra_body='{"chat_template_kwargs": {"enable_thinking": false}}',
        )
    )
    schema = slide_schema("content", "none")
    assert client.chat_json(system="s", user='{"slide_number": 2}', schema=schema)
    request = _SchemaHandler.requests[-1]
    assert request["response_format"]["json_schema"]["schema"] == schema
    assert request["chat_template_kwargs"] == {"enable_thinking": False}

    _SchemaHandler.reject_schema = True
    assert client.chat_json(system="s", user='{"slide_number": 2}', schema=schema)
    assert _SchemaHandler.requests[-1]["response_format"] == {"type": "json_object"}


def test_generate_deck_from_brief_end_to_end(
    tmp_path: Path, schema_server, monkeypatch
):
    monkeypatch.setenv(
        "INFERENCE_BASE_URL", f"http://127.0.0.1:{schema_server.server_port}/v1"
    )
    monkeypatch.setenv("INFERENCE_MODEL", "m")
    monkeypatch.setenv("INFERENCE_VISION", "0")
    monkeypatch.setenv("INFERENCE_PARALLEL", "2")
    template = tmp_path / "clean-blue.pptx"
    create_template(template, "clean-blue")
    result = generate_deck(
        template=template,
        content=BRIEF,
        workspace=tmp_path / "workspace",
        slide_count=10,
        mode="brief",
    )
    assert result["planner_mode"] == "inference"
    assert result["slide_count"] == 10
    assert result["status"] == "completed"
    codes = {issue["code"] for issue in result["qa"]["issues"]}
    assert "content_coverage_unverified" not in codes
    assert "slide_count_mismatch" not in codes
