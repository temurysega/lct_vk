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
