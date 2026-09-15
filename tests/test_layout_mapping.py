from slide_agent.planner import assign_patterns
from slide_agent.service import _qa_pattern_feedback


def _pattern(pattern_id: str, rhetorical: str, *, roles=None):
    return {
        "id": pattern_id,
        "name": pattern_id,
        "source_kind": "slide_exemplar",
        "roles": roles or ["content"],
        "layout_index": 0,
        "master_index": 0,
        "example_slide_indices": [1],
        "capacity": {
            "title_chars": 60,
            "body_chars": 500,
            "body_zones": 1,
            "bullets_per_zone": 5,
            "supports_image": False,
            "supports_data": rhetorical == "data",
            "rhetorical_pattern": rhetorical,
        },
    }


def test_capacity_mapping_explains_selection_and_honors_qa_avoidance():
    catalog = {
        "patterns": [
            _pattern("bullet-a", "bullet_list"),
            _pattern("bullet-b", "bullet_list"),
            _pattern("data", "data", roles=["data", "content"]),
        ]
    }
    plan = {
        "slides": [
            {
                "title": "Архитектура",
                "role": "content",
                "body": "",
                "bullets": ["Парсер", "Планировщик", "Компоновщик"],
                "visual": None,
            }
        ]
    }
    assign_patterns(plan, catalog)
    first = plan["slides"][0]["pattern_id"]
    assert first in {"bullet-a", "bullet-b"}
    selection = plan["slides"][0]["pattern_selection"]
    assert selection["layout_pattern"] == "bullet_list"
    assert selection["why_fit"]
    assert len(selection["alternatives"]) == 2

    qa = {
        "issues": [
            {
                "slide": 1,
                "severity": "error",
                "code": "text_overflow_risk",
                "message": "overflow",
            }
        ]
    }
    avoidance, feedback = _qa_pattern_feedback(plan, qa)
    assign_patterns(plan, catalog, avoid_by_slide=avoidance)
    assert plan["slides"][0]["pattern_id"] != first
    assert feedback[0]["pattern_id"] == first


def test_text_prefers_usable_layout_without_empty_image_slot():
    plain = _pattern("plain", "bullet_list")
    photo = _pattern("photo", "bullet_list")
    photo["capacity"]["image_slot_area_ratio"] = 0.5
    tiny = _pattern("tiny", "bullet_list")
    tiny["capacity"]["usable_body_zones"] = 0
    plan = {
        "slides": [
            {"title": "Вывод", "role": "content", "bullets": ["Факт A", "Факт B"]}
        ]
    }
    assign_patterns(plan, {"patterns": [tiny, photo, plain]})
    assert plan["slides"][0]["pattern_id"] == "plain"
