"""Contextual suggestions are limited to quotes that occur on the slide."""

from slide_agent.contextual_audit import MAX_SOURCE_CHARS, review_content


class FakeClient:
    def chat_json(self, *, system, user, schema, **kwargs):
        assert "source" in user and "slides" in user
        assert schema["properties"]["issues"]["maxItems"] == 20
        return {
            "issues": [
                {
                    "slide": 2,
                    "code": "unsupported_claim",
                    "quote": "Пилот запустим за две недели",
                    "reason": "В источнике есть только срок проведения пилота",
                },
                {
                    "slide": 2,
                    "code": "unsupported_claim",
                    "quote": "Пилот запустим за две недели",
                    "reason": "Повтор",
                },
                {
                    "slide": 1,
                    "code": "unsupported_claim",
                    "quote": "Текста на слайде нет",
                    "reason": "Выдуманная цитата модели",
                },
            ]
        }


def test_review_accepts_only_grounded_unique_suggestions(monkeypatch):
    monkeypatch.setenv("INFERENCE_CONTEXT_AUDIT", "1")
    plan = {
        "slides": [
            {"title": "Пилот", "role": "cover"},
            {
                "title": "Пилот запустим за две недели",
                "role": "content",
                "bullets": ["Проверим поиск"],
            },
        ]
    }
    report = review_content("Пилот длится шесть недель.", plan, FakeClient())
    assert report["status"] == "reviewed"
    assert [(i["slide"], i["code"]) for i in report["issues"]] == [
        (2, "unsupported_claim")
    ]


def test_review_reports_when_it_cannot_run(monkeypatch):
    monkeypatch.setenv("INFERENCE_CONTEXT_AUDIT", "1")
    assert review_content("Материал", {"slides": []}, None)["status"] == "not_run"
    assert (
        review_content("x" * (MAX_SOURCE_CHARS + 1), {"slides": []}, FakeClient())[
            "status"
        ]
        == "not_run"
    )


class ReviseClient:
    settings = type("Settings", (), {"parallel_requests": 2})()

    def __init__(self):
        self.requests = []

    def chat_json(self, *, system, user, schema, **kwargs):
        import json

        request = json.loads(user)
        self.requests.append((request, schema))
        answer = {
            "subtitle": "",
            "bullets": ["Пилот длится шесть недель.", "Команда из трёх человек."],
            "speaker_notes": "",
        }
        if "title" in schema["properties"]:
            answer["title"] = "Пилот длится шесть недель"
        return answer


def test_flagged_brief_slides_are_rewritten_once_with_the_findings():
    from slide_agent.brief import revise_flagged_slides

    outline = [
        {"title": "Пилот", "role": "cover", "goal": "", "visual": "none"},
        {"title": "Запустим за две недели", "role": "content", "goal": "срок", "visual": "none"},
        {"title": "Фото", "role": "content", "goal": "фото", "visual": "none", "asset_id": "a1"},
    ]
    plan = {
        "title": "Пилот",
        "brief": {"purpose": "feature", "outline": outline},
        "slides": [
            {"title": "Пилот", "role": "cover", "bullets": []},
            {"title": "Запустим за две недели", "role": "content", "bullets": ["Быстро"]},
            {
                "title": "Фото",
                "role": "content",
                "bullets": ["Снова шесть недель"],
                "visual": {"type": "image", "asset_id": "a1", "match": "lexical"},
            },
        ],
    }
    audit = {
        "status": "reviewed",
        "issues": [
            {"slide": 2, "code": "unsupported_claim", "quote": "за две недели", "reason": "срока нет"},
            {"slide": 3, "code": "repeated_message", "quote": "Снова шесть недель", "reason": "повтор"},
            {"slide": 3, "code": "title_not_claim", "quote": "Фото", "reason": "рубрика"},
        ],
    }
    client = ReviseClient()
    assert revise_flagged_slides(plan, brief="Пилот шесть недель.", client=client, audit=audit) == [2, 3]
    by_slide = {request["slide_number"]: (request, schema) for request, schema in client.requests}
    request, schema = by_slide[2]
    assert request["fix_previous_answer"] == ["«за две недели»: срока нет"]
    assert "title" in schema["properties"]  # the finding quotes the title
    assert plan["slides"][1]["title"] == "Пилот длится шесть недель"
    assert "title" not in by_slide[3][1]["properties"]
    assert plan["slides"][2]["visual"]["asset_id"] == "a1"  # the placed image stays
    assert plan["slides"][2]["bullets"][0] == "Пилот длится шесть недель."


class RepeatClient:
    def chat_json(self, *, system, user, schema, **kwargs):
        return {
            "issues": [
                {"slide": 3, "code": "repeated_message", "quote": "Просим одобрить пилот",
                 "reason": "повторяет слайд 2"},
                {"slide": 4, "code": "repeated_message", "quote": "Риски снижены",
                 "reason": "повторяет источник"},
            ]
        }


def test_repeat_needs_shared_wording_with_a_neighbour(monkeypatch):
    monkeypatch.setenv("INFERENCE_CONTEXT_AUDIT", "1")
    plan = {
        "slides": [
            {"title": "Пилот", "role": "cover"},
            {"title": "Просим одобрить пилот и выделить команду", "role": "content",
             "bullets": ["Команда из трёх человек"]},
            {"title": "Просим одобрить пилот для команды", "role": "content",
             "bullets": ["Выделить команду из трёх человек"]},
            {"title": "Риски снижены архитектурой контура", "role": "content",
             "bullets": ["Документы не покидают периметр"]},
        ]
    }
    report = review_content("Пилот, команда из трёх человек.", plan, RepeatClient())
    assert [(i["slide"], i["code"]) for i in report["issues"]] == [(3, "repeated_message")]
