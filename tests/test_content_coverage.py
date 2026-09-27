from copy import deepcopy

import pytest
from pptx import Presentation
from pptx.util import Inches

from slide_agent.coverage import (
    coverage_report,
    grounding_findings,
    source_backed_baseline_fallback,
    unsupported_numbers,
)
from slide_agent.planner import _fallback_plan, _sentences, normalize_plan
from slide_agent.qa import compact_plan


def test_last_sections_and_first_section_body_survive_small_slide_count():
    source = "# Проект\nВводный факт 40%.\n" + "\n".join(
        f"## Раздел {i}\nПодтвержденный факт {i}." for i in range(12)
    )
    plan = normalize_plan(_fallback_plan(source, 5), source_text=source, slide_count=5)
    assert len(plan["slides"]) == 5
    report = coverage_report(source, plan)
    assert report["missing_from_plan"] == []
    assert any("Раздел 11" in slide["title"] for slide in plan["slides"])


def test_normalization_does_not_cut_long_text_or_sixth_bullet():
    text = "Подтвержденный факт " * 100
    plan = {
        "slides": [
            {"title": "Начало"},
            {
                "title": "Данные",
                "body": text,
                "bullets": [f"Факт {i}" for i in range(8)],
            },
            {"title": "Конец"},
        ]
    }
    normalized = normalize_plan(plan, source_text=text, slide_count=3)
    assert normalized["slides"][1]["body"] == text.strip()
    assert len(normalized["slides"][1]["bullets"]) == 8


def test_retry_preserves_chart_arrays_and_every_fact():
    plan = {
        "slides": [
            {
                "body": "Факт " * 200,
                "bullets": list(map(str, range(9))),
                "visual": {
                    "type": "bar_chart",
                    "categories": list(map(str, range(10))),
                    "series": [{"values": list(range(10))}],
                },
            }
        ]
    }
    original = deepcopy(plan)
    retry = compact_plan(plan)
    assert retry == original
    retry["slides"][0]["visual"]["categories"].clear()
    assert plan == original


def test_pptx_coverage_detects_missing_fact_even_if_it_is_in_plan(tmp_path):
    source = "# Проект\nБюджет 40 млн рублей.\nСрок 4 недели."
    plan = _fallback_plan(source, 3)
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_textbox(
        Inches(1), Inches(1), Inches(8), Inches(2)
    ).text = "Проект\nБюджет 40 млн рублей."
    output = tmp_path / "deck.pptx"
    prs.save(output)
    report = coverage_report(source, plan, output)
    assert not report["missing_from_plan"]
    assert report["missing_from_pptx"]
    assert report["status"] == "needs_review"


def test_paraphrase_and_speaker_notes_are_not_claimed_as_verified():
    source = "Срок 4 недели."
    report = coverage_report(
        source, {"slides": [{"body": "Завершим за месяц.", "speaker_notes": source}]}
    )
    assert report["status"] == "needs_review"
    assert report["semantic_verification"] == "not_performed"


def test_image_match_score_is_not_treated_as_slide_claim():
    plan = {
        "slides": [
            {
                "title": "Сотрудники",
                "visual": {
                    "type": "image",
                    "asset_id": "abc123",
                    "match": "lexical",
                    "match_score": 4.54,
                },
            }
        ]
    }
    assert unsupported_numbers("Сотрудники используют помощника.", plan) == []


def test_brief_numeric_check_includes_notes_without_changing_visible_coverage():
    source = "Треть обращений повторяется."
    plan = {"slides": [{
        "title": "Повторные обращения",
        "bullets": ["33% обращений повторяются"],
        "speaker_notes": "Проверка пройдёт в 2027 году.",
    }]}
    assert unsupported_numbers(source, plan) == [{"slide": 1, "numbers": ["33"]}]
    assert unsupported_numbers(source, plan, include_notes=True) == [
        {"slide": 1, "numbers": ["2027", "33"]}
    ]


def test_grounding_rules_find_budget_guarantee_and_reversed_success_metric():
    source = (
        "Документы не уходят наружу. Успех: время ответа и доля обращений, "
        "закрытых без специалиста. Просим одобрить пилот и команду."
    )
    plan = {"slides": [
        {"title": "Снижение доли обращений, закрытых без специалиста"},
        {"title": "Риск утечки исключён"},
        {"title": "Просим утвердить бюджет пилота"},
    ]}
    findings = grounding_findings(source, plan)
    assert [(item["slide"], item["quote"]) for item in findings] == [
        (1, "Снижение доли обращений, закрытых без специалиста"),
        (2, "Риск утечки исключён"),
        (3, "Просим утвердить бюджет пилота"),
    ]
    assert coverage_report(source, plan)["grounding_findings"] == findings


def test_grounding_rules_keep_supported_and_neutral_wording():
    source = (
        "Документы не уходят наружу. Измеряем долю обращений, закрытых "
        "без специалиста. Просим утвердить бюджет пилота."
    )
    plan = {"slides": [
        {"title": "Рост доли обращений, закрытых без специалиста"},
        {"title": "Измеряем долю обращений, закрытых без специалиста"},
        {"title": "Документы не уходят наружу"},
        {"title": "Просим утвердить бюджет пилота"},
    ]}
    assert grounding_findings(source, plan) == []


def test_pilot_duration_does_not_support_a_launch_deadline_or_a_new_note_date():
    source = "Пилот: 2 подразделения, 150 сотрудников, 6 недель."
    plan = {"slides": [
        {"title": "Сроки", "bullets": ["Запустить проект в течение 6 недель."]},
        {"title": "Решение", "speaker_notes": "Запуск проекта в течение двух недель."},
    ]}
    findings = grounding_findings(source, plan)
    assert [(item["slide"], item["quote"]) for item in findings] == [
        (1, "Запустить проект в течение 6 недель."),
        (2, "Запуск проекта в течение двух недель."),
    ]
    assert "другому событию" in findings[0]["reason"]
    assert "Такой срок" in findings[1]["reason"]
    # Literal slide coverage still excludes presenter notes.
    assert unsupported_numbers(source, plan) == []


def test_explicit_launch_deadline_and_pilot_duration_remain_supported():
    source = "Пилот длится 6 недель. Запуск сервиса ожидается в течение 6 недель."
    plan = {"slides": [
        {"title": "Пилот длится 6 недель"},
        {"title": "Запустить продукт в течение 6 недель"},
    ]}
    assert grounding_findings(source, plan) == []


def test_current_search_duration_cannot_become_same_reduction_target():
    source = (
        "Сейчас сотрудник тратит до 30 минут, чтобы найти нужную инструкцию, "
        "а треть обращений повторяется."
    )
    claim = "Сокращение времени поиска инструкций до 30 минут"
    plan = {"slides": [{
        "title": claim,
        "subtitle": "Сейчас сотрудник тратит до 30 минут, чтобы найти инструкцию",
        "speaker_notes": "Снизим время поиска до 30 минут.",
        "bullets": ["Сокращение времени поиска с 30 минут"],
    }]}
    findings = grounding_findings(source, plan)
    assert [item["quote"] for item in findings] == [
        claim, "Сокращение времени поиска с 30 минут", "Снизим время поиска до 30 минут."
    ]
    repaired = source_backed_baseline_fallback(source, plan["slides"][0])
    assert repaired["title"] == "Сейчас сотрудник тратит до 30 минут, чтобы найти нужную инструкцию"
    assert grounding_findings(source, {"slides": [repaired]}) == []


def test_reduction_target_rule_keeps_actual_targets_and_baseline_wording():
    source = "Сейчас поиск занимает до 30 минут."
    supported = {"slides": [{"title": "Поиск сейчас занимает до 30 минут"},
                            {"title": "Измеряем время поиска"},
                            {"title": "Сократить время поиска после измерения исходного уровня"}]}
    assert grounding_findings(source, supported) == []
    explicit_target = source + " Цель пилота: сократить время поиска до 30 минут."
    assert grounding_findings(explicit_target, {
        "slides": [{"title": "Сократить время поиска до 30 минут"}]
    }) == []


def test_team_headcount_does_not_assign_duties_but_explicit_duties_do():
    source = "Просим выделить команду из 3 человек. Вопрос уходит специалисту поддержки."
    claim = "Специалисты займутся настройкой помощника и анализом данных."
    findings = grounding_findings(source, {"slides": [{"bullets": [claim]}]})
    assert [(item["slide"], item["quote"]) for item in findings] == [(1, claim)]
    supported = "Специалисты будут заниматься настройкой помощника и анализом данных."
    assert grounding_findings(supported, {"slides": [{"bullets": [claim]}]}) == []


def test_visual_label_and_detail_form_one_role_assignment():
    visual = {"type": "hierarchy", "items": [
        {"label": "Ответственный", "detail": "Координирует работу"},
        {"label": "Разработчик", "detail": "Интегрирует модель"},
        {"label": "Аналитик", "detail": "Подбирает документы"},
    ]}
    plan = {"slides": [{"visual": visual}]}
    source = "Просим выделить команду из 3 человек."
    assert [issue["quote"] for issue in grounding_findings(source, plan)] == [
        "Ответственный — Координирует работу",
        "Разработчик — Интегрирует модель",
        "Аналитик — Подбирает документы",
    ]
    supported = (
        "Ответственный координирует работу. Разработчик интегрирует модель. "
        "Аналитик подбирает документы."
    )
    assert grounding_findings(supported, plan) == []
    # A duty of the team does not establish which role will perform it.
    assert grounding_findings("Команда интегрирует модель.", plan)[1]["quote"] == (
        "Разработчик — Интегрирует модель"
    )


def test_support_specialist_answer_is_not_treated_as_an_invented_project_role():
    source = "Если ответа нет, вопрос уходит специалисту поддержки с контекстом."
    plan = {"slides": [{"bullets": [
        "Специалист поддержки отвечает на вопрос с полученным контекстом."
    ]}]}
    assert grounding_findings(source, plan) == []


def test_pilot_does_not_imply_companywide_rollout():
    source = "Пилот для 150 сотрудников длится 6 недель."
    plan = {"slides": [
        {"title": "Результат пилота станет основой для масштабирования на всю компанию"},
        {"bullets": ["Данные пилота подтвердят эффективность для всех сотрудников."]},
        {"speaker_notes": "Опыт пилота позволит перейти к полному внедрению."},
    ]}
    assert [item["slide"] for item in grounding_findings(source, plan)] == [1, 2, 3]
    conditional = {"slides": [{
        "title": "По итогам пилота решим о масштабировании на всю компанию"
    }]}
    assert grounding_findings(source, conditional) == []
    explicit = "Пилот для 150 сотрудников. После пилота масштабируем на всю компанию."
    assert grounding_findings(explicit, {"slides": [plan["slides"][0]]}) == []


def test_too_little_content_does_not_create_empty_slides():
    with pytest.raises(ValueError, match="fewer slides"):
        _fallback_plan("Один факт.", 10)


def test_semicolon_splits_only_before_a_new_statement():
    assert _sentences("Вопрос передаётся специалисту; ссылку можно проверить.") == [
        "Вопрос передаётся специалисту; ссылку можно проверить."
    ]
    assert _sentences("Первый пункт; Второй пункт") == ["Первый пункт", "Второй пункт"]


def test_offline_cover_has_no_service_caption():
    source = "# Пилот\n\n## Проблема\nПоиск занимает время.\n\n## Решение\nПомощник отвечает."
    cover = _fallback_plan(source, 4)["slides"][0]
    assert cover["role"] == "cover" and cover["subtitle"] == ""
