"""Contextual suggestions are limited to quotes that occur on the slide."""

import pytest

from slide_agent.brief import revise_flagged_slides
from slide_agent.contextual_audit import MAX_SOURCE_CHARS, review_content
from slide_agent.coverage import grounding_findings
from slide_agent.llm import InferenceError
from slide_agent.service import _number_findings, _rewrite_audit


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


def test_numbers_missing_from_the_brief_join_the_rewrite():
    brief = "Треть обращений повторяется. Пилот: 150 сотрудников."
    plan = {
        "slides": [
            {"title": "Пилот", "bullets": ["150 сотрудников в пилоте."]},
            {"title": "Повторы", "bullets": ["33% запросов — повторы.", "Нагрузка растёт."]},
        ]
    }
    assert _number_findings(brief, plan) == [
        {
            "slide": 2,
            "code": "unsupported_claim",
            "quote": "33% запросов — повторы.",
            "reason": "The number 33 is not in the brief: keep the brief's own wording or numbers.",
        }
    ]


def test_the_rewrite_takes_audit_findings_and_numbers_missing_from_the_brief():
    brief = "Треть обращений повторяется."
    plan = {"slides": [{"title": "Повторы", "bullets": ["33% запросов — повторы."]}]}
    review = {"status": "reviewed", "issues": [{"slide": 1, "code": "repeated_message"}]}
    audit = _rewrite_audit(brief, plan, review)
    # revise_flagged_slides rewrites only a reviewed audit.
    assert audit["status"] == "reviewed"
    assert [issue["code"] for issue in audit["issues"]] == ["repeated_message", "unsupported_claim"]
    # Without the model's review the numbers still go to the rewrite.
    audit = _rewrite_audit(brief, plan, {"status": "not_run"})
    assert audit["status"] == "reviewed" and len(audit["issues"]) == 1
    assert _rewrite_audit("33%", plan, {"status": "not_run"}) == {"status": "not_run", "issues": []}


def test_grounding_rules_reach_rewrite_when_model_audit_is_disabled(monkeypatch):
    monkeypatch.setenv("INFERENCE_CONTEXT_AUDIT", "0")
    plan = {"slides": [{"title": "Просим утвердить бюджет пилота"}]}
    report = review_content("Просим одобрить пилот.", plan, FakeClient())
    assert report["status"] == "reviewed"
    assert report["method"] == "deterministic_grounding_rules_v1"
    assert report["issues"][0]["code"] == "unsupported_claim"


def test_context_audit_reads_speaker_notes(monkeypatch):
    import json

    monkeypatch.setenv("INFERENCE_CONTEXT_AUDIT", "1")

    class NotesClient:
        def __init__(self):
            self.payload = None

        def chat_json(self, *, user, **kwargs):
            self.payload = json.loads(user)
            return {"issues": []}

    client = NotesClient()
    plan = {"slides": [{"title": "Пилот", "speaker_notes": "Заметки для докладчика"}]}
    assert review_content("Пилот", plan, client)["status"] == "reviewed"
    assert "Заметки для докладчика" in client.payload["slides"][0]["text"]


def test_rewrite_retries_inverse_kpi_and_invented_note_deadline():
    import json

    source = (
        "Пилот длится 6 недель. Успех: доля обращений, закрытых без специалиста."
    )
    old = {
        "title": "Метрики пилота",
        "role": "content",
        "bullets": ["Снижение доли обращений, закрытых без специалиста", "Время поиска"],
        "speaker_notes": "Запустить проект в течение 6 недель.",
    }
    outline = [
        {"title": "Пилот", "role": "cover", "goal": "", "visual": "none"},
        {"title": "Метрики пилота", "role": "content", "goal": "метрики", "visual": "none"},
        {"title": "Итог", "role": "closing", "goal": "", "visual": "none"},
    ]
    plan = {
        "title": "Пилот",
        "brief": {"purpose": "feature", "outline": outline},
        "slides": [{"title": "Пилот", "role": "cover"}, old,
                   {"title": "Итог", "role": "closing"}],
    }
    audit = {"status": "reviewed", "issues": [
        {**issue, "slide": 2}
        for issue in grounding_findings(source, {"slides": [old]})
    ]}

    class RetryClient:
        settings = type("Settings", (), {"parallel_requests": 1})()

        def __init__(self):
            self.requests = []

        def chat_json(self, *, user, **kwargs):
            self.requests.append(json.loads(user))
            if len(self.requests) == 1:
                return {
                    "subtitle": "",
                    "bullets": old["bullets"],
                    "speaker_notes": old["speaker_notes"],
                }
            return {
                "subtitle": "",
                "bullets": ["Измеряем долю обращений, закрытых без специалиста", "Время поиска"],
                "speaker_notes": "Пилот длится 6 недель.",
            }

    client = RetryClient()
    assert revise_flagged_slides(plan, brief=source, client=client, audit=audit) == [2]
    assert len(client.requests) == 2
    assert client.requests[0]["previous_answer"]["speaker_notes"] == old["speaker_notes"]
    assert any("другому событию" in issue for issue in client.requests[1]["fix_previous_answer"])
    assert plan["slides"][1]["speaker_notes"] == "Пилот длится 6 недель."


def test_stubborn_kpi_rewrite_uses_exact_metric_from_neutral_brief():
    source = "Успех пилота: доля обращений, закрытых без специалиста."
    bad = "Снижение доли обращений, закрытых без специалиста"
    plan = {
        "title": "Пилот",
        "brief": {"purpose": "feature", "outline": [
            {"title": "Пилот", "role": "cover", "goal": "", "visual": "none"},
            {"title": bad, "role": "content", "goal": "метрика", "visual": "none"},
            {"title": "Итог", "role": "closing", "goal": "", "visual": "none"},
        ]},
        "slides": [
            {"title": "Пилот", "role": "cover"},
            {"title": bad, "role": "content", "bullets": [bad, "Время ответа"]},
            {"title": "Итог", "role": "closing"},
        ],
    }
    audit = {"status": "reviewed", "issues": [
        {**item, "slide": 2}
        for item in grounding_findings(source, {"slides": [plan["slides"][1]]})
    ]}

    class StubbornClient:
        settings = type("Settings", (), {"parallel_requests": 1})()

        def __init__(self):
            self.calls = 0

        def chat_json(self, **kwargs):
            self.calls += 1
            return {
                "title": bad,
                "subtitle": "",
                "bullets": [bad, "Время ответа"],
                "speaker_notes": "",
            }

    client = StubbornClient()
    assert revise_flagged_slides(plan, brief=source, client=client, audit=audit) == [2]
    assert client.calls == 2
    assert plan["slides"][1]["title"] == (
        "Показатель: доля обращений, закрытых без специалиста"
    )
    assert plan["brief"]["source_backed_kpi_fallbacks"] == [2]
    assert grounding_findings(source, plan) == []


def test_stubborn_cover_rewrite_restores_current_duration_from_brief():
    source = "Сейчас поиск инструкции занимает до 30 минут."
    bad = "Сокращение времени поиска инструкции до 30 минут"
    plan = {
        "title": bad,
        "brief": {"purpose": "feature", "outline": [
            {"title": bad, "role": "cover", "goal": "", "visual": "none"},
            {"title": "Пилот", "role": "content", "goal": "Проверка", "visual": "none"},
            {"title": "Решение", "role": "closing", "goal": "", "visual": "none"},
        ]},
        "slides": [
            {"title": bad, "role": "cover", "subtitle": bad},
            {"title": "Пилот", "role": "content"},
            {"title": "Решение", "role": "closing"},
        ],
    }
    audit = {"status": "reviewed", "issues": grounding_findings(source, plan)}

    class StubbornClient:
        settings = type("Settings", (), {"parallel_requests": 1})()

        def __init__(self):
            self.calls = 0

        def chat_json(self, **kwargs):
            self.calls += 1
            return {"title": bad, "subtitle": bad, "bullets": [], "speaker_notes": ""}

    client = StubbornClient()
    assert revise_flagged_slides(plan, brief=source, client=client, audit=audit) == [1]
    assert client.calls == 2
    assert plan["slides"][0]["title"] == "Сейчас поиск инструкции занимает до 30 минут"
    assert plan["title"] == plan["slides"][0]["title"]
    assert plan["brief"]["outline"][0]["title"] == plan["title"]
    assert plan["brief"]["source_backed_baseline_fallbacks"] == [1]
    assert grounding_findings(source, plan) == []


def test_brief_rewrite_blocks_remaining_unsupported_budget():
    class StubbornClient:
        settings = type("Settings", (), {"parallel_requests": 1})()

        def chat_json(self, **kwargs):
            return {
                "title": "Просим утвердить бюджет пилота",
                "subtitle": "",
                "bullets": ["Утвердить бюджет", "Одобрить пилот"],
                "speaker_notes": "",
            }

    plan = {
        "title": "Пилот",
        "brief": {"purpose": "feature", "outline": [
            {"title": "Пилот", "role": "cover", "goal": "", "visual": "none"},
            {"title": "Просим утвердить бюджет пилота", "role": "content", "goal": "решение", "visual": "none"},
            {"title": "Решение", "role": "closing", "goal": "", "visual": "none"},
        ]},
        "slides": [
            {"title": "Пилот", "role": "cover"},
            {"title": "Просим утвердить бюджет пилота", "role": "content"},
            {"title": "Решение", "role": "closing"},
        ],
    }
    audit = {"status": "reviewed", "issues": [{
        "slide": 2, "code": "unsupported_claim",
        "quote": "Просим утвердить бюджет пилота",
        "reason": "В брифе нет бюджета",
    }]}
    with pytest.raises(InferenceError, match="unresolved grounding issue"):
        revise_flagged_slides(
            plan, brief="Просим одобрить пилот.", client=StubbornClient(), audit=audit
        )


def test_stubborn_rewrite_does_not_report_a_pending_pilot_as_launched():
    source = "Пилот: 2 подразделения, 6 недель. Просим одобрить пилот."
    bad = "Пилот запущен в двух подразделениях"
    plan = {
        "title": "Помощник",
        "brief": {"purpose": "feature", "outline": [
            {"title": "Помощник", "role": "cover", "goal": "", "visual": "none"},
            {"title": bad, "role": "content", "goal": "охват", "visual": "none"},
            {"title": "Итог", "role": "closing", "goal": "", "visual": "none"},
        ]},
        "slides": [
            {"title": "Помощник", "role": "cover"},
            {"title": bad, "role": "content", "bullets": ["Срок — 6 недель"]},
            {"title": "Итог", "role": "closing"},
        ],
    }
    audit = {"status": "reviewed", "issues": grounding_findings(source, plan)}
    assert [issue["slide"] for issue in audit["issues"]] == [2]

    class StubbornClient:
        settings = type("Settings", (), {"parallel_requests": 1})()

        def chat_json(self, **kwargs):
            return {"title": bad, "subtitle": "", "bullets": ["Срок — 6 недель"],
                    "speaker_notes": ""}

    assert revise_flagged_slides(
        plan, brief=source, client=StubbornClient(), audit=audit
    ) == [2]
    assert plan["slides"][1]["title"] == "Пилот планируется в двух подразделениях"
    assert plan["brief"]["source_backed_pilot_status_fallbacks"] == [2]
    assert grounding_findings(source, plan) == []


def test_stubborn_invented_number_is_dropped_instead_of_blocking_the_deck():
    from slide_agent.service import _rewrite_audit

    source = "Помощник отвечает на вопросы. Пилот: 150 сотрудников."
    bullets = ["Помощник отвечает на вопросы", "Точность ответов 100 %"]
    plan = {
        "title": "Помощник",
        "brief": {"purpose": "feature", "outline": [
            {"title": "Помощник", "role": "cover", "goal": "", "visual": "none"},
            {"title": "Ответы", "role": "content", "goal": "ответы", "visual": "none"},
            {"title": "Итог", "role": "closing", "goal": "", "visual": "none"},
        ]},
        "slides": [
            {"title": "Помощник", "role": "cover"},
            {"title": "Ответы", "role": "content", "bullets": list(bullets)},
            {"title": "Итог", "role": "closing"},
        ],
    }
    audit = _rewrite_audit(source, plan, {"status": "not_run"})

    class StubbornClient:
        settings = type("Settings", (), {"parallel_requests": 1})()

        def chat_json(self, **kwargs):
            return {"subtitle": "", "bullets": list(bullets), "speaker_notes": ""}

    assert revise_flagged_slides(
        plan, brief=source, client=StubbornClient(), audit=audit
    ) == [2]
    assert plan["slides"][1]["bullets"] == ["Помощник отвечает на вопросы"]
    assert plan["brief"]["dropped_invented_number_statements"] == [2]
