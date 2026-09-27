from slide_agent.analyzer import _backdrop_area_ratio, _slide_exemplar_patterns
from slide_agent.planner import assign_patterns
from slide_agent.service import _hint_cards_for_underfilled, _qa_pattern_feedback


def test_exemplar_catalog_ignores_large_navigation_number():
    def element(text, x, y, w, h, size):
        return {
            "left": x,
            "top": y,
            "width": w,
            "height": h,
            "paragraphs": [{"text": text, "font": {"size_pt": size}}],
        }

    context = {
        "presentation": {"slide_width_inches": 13.33, "slide_height_inches": 7.5},
        "slide_layouts": [{}],
        "slides": [
            {
                "index": 1,
                "layout_index": 0,
                "text_elements": [
                    element("02", 11.3, 0.4, 1.6, 1.9, 72),
                    element("PART ONE", 0.5, 0.4, 2.2, 0.3, 12),
                    element("Настоящий заголовок", 0.5, 0.9, 6.8, 0.6, 30),
                    element("Основной текст", 0.5, 2.1, 6.0, 2.3, 18),
                ],
            }
        ],
    }

    pattern = _slide_exemplar_patterns(context)[0]
    assert len(pattern["placeholders"]) == 3
    title = next(zone for zone in pattern["placeholders"] if zone["type"] == "title")
    assert title["w"] == 6.8
    assert title["y"] == 0.9


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


def _image_slide():
    return {
        "title": "Сотрудник и специалист",
        "role": "content",
        "bullets": ["Ответ со ссылкой", "Контекст вопроса", "Передача без повтора"],
        "visual": {"type": "image", "asset_id": "a1"},
    }


def test_image_slide_prefers_template_slot_over_extra_columns():
    columns = _pattern("columns", "bullet_list")
    columns["capacity"]["usable_body_zones"] = 3
    photo = _pattern("photo", "bullet_list")
    photo["capacity"]["image_slot_area_ratio"] = 0.3
    plan = {"slides": [_image_slide()]}
    assign_patterns(plan, {"patterns": [columns, photo]}, layout_strategy="columns")
    assert plan["slides"][0]["pattern_id"] == "photo"


def test_image_slide_without_slot_reports_the_risk():
    plan = {"slides": [_image_slide()]}
    assign_patterns(plan, {"patterns": [_pattern("plain", "bullet_list")]})
    assert "нет слота изображения" in plan["slides"][0]["pattern_selection"]["risk"]


def test_text_slide_avoids_layout_with_empty_object_block():
    plain = _pattern("plain", "bullet_list")
    backdrop = _pattern("backdrop", "bullet_list")
    backdrop["capacity"]["backdrop_area_ratio"] = 0.5
    plan = {"slides": [{"title": "Итог", "role": "content", "bullets": ["A", "B"]}]}
    assign_patterns(plan, {"patterns": [backdrop, plain]}, layout_strategy="focus")
    assert plan["slides"][0]["pattern_id"] == "plain"


def test_object_block_counts_only_when_nothing_covers_it():
    layout = {
        "shapes": [
            {"type": "RECTANGLE", "fill": {"type": "solid"}, "left": 6.67, "top": 0,
             "width": 6.67, "height": 7.5},
            {"type": "RECTANGLE", "fill": {"type": "inherit"}, "left": 0, "top": 0,
             "width": 6.0, "height": 7.5},
        ]
    }
    body = {"type": "body", "x": 0.7, "y": 2.2, "w": 5.9, "h": 4.5}
    picture = {"type": "picture", "x": 6.67, "y": 0, "w": 6.67, "h": 7.5}
    photo = {"left": 6.7, "top": 0.2, "width": 6.5, "height": 7.0}
    assert _backdrop_area_ratio(layout, [body], 13.333, 7.5) == 0.5
    assert _backdrop_area_ratio(layout, [body, picture], 13.333, 7.5) == 0
    assert _backdrop_area_ratio(layout, [body], 13.333, 7.5, [photo]) == 0


def test_rendered_underfilled_statements_are_marked_for_cards():
    plan = {
        "slides": [
            {"role": "cover", "title": "Пилот"},
            {"role": "content", "title": "Итог", "bullets": ["A.", "B.", "C."]},
            {"role": "content", "title": "Схема", "bullets": [], "visual": {"type": "process"}},
            {"role": "content", "title": "Один", "bullets": ["Только один тезис."]},
        ]
    }
    qa = {
        "issues": [
            {"code": "slide_underfilled", "slide": number} for number in (2, 3, 4)
        ]
        + [{"code": "text_overlap", "slide": 1}]
    }
    # Slide 2 becomes cards; the process diagram (3) and the single
    # statement (4) are sent to another layout instead.
    assert _hint_cards_for_underfilled(plan, qa) == [2, 3, 4]
    assert plan["slides"][1]["layout_hint"] == "cards"
    assert plan["slides"][2]["remap_underfilled"] is True
    assert _hint_cards_for_underfilled(plan, qa) == []  # only once per slide


def test_underfilled_table_slide_is_marked_to_fill_its_zone():
    plan = {
        "slides": [
            {"role": "content", "title": "Метрики",
             "visual": {"type": "table", "headers": ["A", "B"], "rows": [["1", "2"]]}},
        ]
    }
    qa = {"issues": [{"code": "slide_underfilled", "slide": 1}]}
    assert _hint_cards_for_underfilled(plan, qa) == [1]
    # A sparse table is already stretched over its zone, so the zone is small.
    assert plan["slides"][0]["visual"]["fill_zone"] is True
    assert plan["slides"][0]["remap_underfilled"] is True
    assert _hint_cards_for_underfilled(plan, qa) == []


def test_underfilled_cards_drawn_by_the_composer_get_another_layout():
    plan = {"slides": [{"role": "content", "title": "Итог", "bullets": ["A.", "B.", "C."]}]}
    qa = {"issues": [{"code": "slide_underfilled", "slide": 1}]}
    assert _hint_cards_for_underfilled(plan, qa, {1}) == [1]
    assert plan["slides"][0]["layout_hint"] == "cards"
    assert plan["slides"][0]["remap_underfilled"] is True


def test_underfilled_data_diagram_gets_another_layout_but_a_photo_comparison_does_not():
    plan = {
        "slides": [
            {"role": "data", "title": "Срок", "visual": {"type": "process", "items": []}},
            {"role": "comparison", "title": "Контур", "bullets": ["A."],
             "visual": {"type": "image", "asset_id": "a1"}},
        ]
    }
    qa = {"issues": [{"code": "slide_underfilled", "slide": number} for number in (1, 2)]}
    assert _hint_cards_for_underfilled(plan, qa) == [1]
    assert plan["slides"][0]["remap_underfilled"] is True
