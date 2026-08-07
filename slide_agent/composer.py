from __future__ import annotations

import copy
import math
from collections import Counter
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.chart.data import CategoryChartData, ChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE, PP_PLACEHOLDER
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Inches, Pt

from .utils import read_json, write_json

TITLE_TYPES = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}
SUBTITLE_TYPES = {PP_PLACEHOLDER.SUBTITLE}
BODY_TYPES = {
    PP_PLACEHOLDER.BODY,
    PP_PLACEHOLDER.OBJECT,
    PP_PLACEHOLDER.VERTICAL_BODY,
    PP_PLACEHOLDER.VERTICAL_OBJECT,
}


def _hex(value: str | None, fallback: str) -> str:
    candidate = str(value or "").lstrip("#").upper()
    if len(candidate) == 8:
        candidate = candidate[-6:]
    return candidate if len(candidate) == 6 else fallback


def _rgb(value: str | None, fallback: str = "000000") -> RGBColor:
    return RGBColor.from_string(_hex(value, fallback))


def _number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = "".join(
        char for char in str(value).replace(",", ".") if char in "-0123456789."
    )
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _theme_colors(design: dict[str, Any]) -> dict[str, str]:
    result = {
        str(item.get("role")): _hex(item.get("hex"), "000000")
        for item in design.get("colors", {}).get("theme", [])
    }
    observed = design.get("colors", {}).get("observed", [])
    fallbacks = [item.get("hex") for item in observed if item.get("hex")]
    result.setdefault("accent1", _hex(fallbacks[0] if fallbacks else None, "4472C4"))
    result.setdefault(
        "accent2", _hex(fallbacks[1] if len(fallbacks) > 1 else None, "70AD47")
    )
    result.setdefault(
        "accent3", _hex(fallbacks[2] if len(fallbacks) > 2 else None, "ED7D31")
    )
    result.setdefault("dk1", "1F2937")
    result.setdefault("lt1", "FFFFFF")
    result.setdefault("lt2", "F3F4F6")
    return result


def _all_layouts(prs: Presentation) -> list[Any]:
    return [layout for master in prs.slide_masters for layout in master.slide_layouts]


def _remove_slide_ids(prs: Presentation, slide_ids_to_remove: list[Any]) -> None:
    slide_ids = prs.slides._sldIdLst
    for slide_id in slide_ids_to_remove:
        rel_id = slide_id.rId
        slide_ids.remove(slide_id)
        try:
            prs.part.drop_rel(rel_id)
        except KeyError:
            pass


def _clone_slide(prs: Presentation, source: Any) -> Any:
    """Clone a source slide while preserving its shapes and media relationships."""
    destination = prs.slides.add_slide(source.slide_layout)
    destination_tree = destination.shapes._spTree
    for shape in list(destination.shapes):
        destination_tree.remove(shape.element)

    relationship_map: dict[str, str] = {}
    for rel in source.part.rels.values():
        if rel.reltype.endswith("/slideLayout") or rel.reltype.endswith("/notesSlide"):
            continue
        try:
            target = rel.target_ref if rel.is_external else rel.target_part
            relationship_map[rel.rId] = destination.part.relate_to(
                target, rel.reltype, is_external=rel.is_external
            )
        except (AttributeError, KeyError, ValueError):
            continue

    for shape in source.shapes:
        element = copy.deepcopy(shape.element)
        for child in element.iter():
            for attribute, value in list(child.attrib.items()):
                if value in relationship_map:
                    child.set(attribute, relationship_map[value])
        destination_tree.insert_element_before(element, "p:extLst")

    try:
        source_bg = source._element.cSld.bg
        if source_bg is not None:
            destination_bg = destination._element.cSld.bg
            if destination_bg is not None:
                destination._element.cSld.remove(destination_bg)
            destination._element.cSld.insert(0, copy.deepcopy(source_bg))
    except AttributeError:
        pass
    return destination


def _placeholder_type(shape: Any) -> Any:
    try:
        return shape.placeholder_format.type
    except (AttributeError, ValueError):
        return None


def _placeholder_groups(slide: Any) -> tuple[list[Any], list[Any], list[Any]]:
    titles: list[Any] = []
    subtitles: list[Any] = []
    bodies: list[Any] = []
    for shape in slide.placeholders:
        placeholder_type = _placeholder_type(shape)
        if placeholder_type in TITLE_TYPES:
            titles.append(shape)
        elif placeholder_type in SUBTITLE_TYPES:
            subtitles.append(shape)
        elif placeholder_type in BODY_TYPES and getattr(shape, "has_text_frame", False):
            bodies.append(shape)
    bodies.sort(key=lambda item: (item.left, item.top))
    return titles, subtitles, bodies


def _largest_font(shape: Any) -> float:
    sizes: list[float] = []
    if not getattr(shape, "has_text_frame", False):
        return 0.0
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if run.font.size:
                sizes.append(run.font.size.pt)
    return max(sizes, default=0.0)


def _shape_text_style(shape: Any) -> dict[str, Any]:
    """Capture explicit source styling before replacing exemplar text."""
    if not getattr(shape, "has_text_frame", False):
        return {}
    style: dict[str, Any] = {}
    for paragraph in shape.text_frame.paragraphs:
        if paragraph.alignment is not None and "alignment" not in style:
            style["alignment"] = paragraph.alignment
        for run in paragraph.runs:
            font = run.font
            if font.name and "font_name" not in style:
                style["font_name"] = font.name
            if font.size and "font_size" not in style:
                style["font_size"] = font.size.pt
            if font.bold is not None and "bold" not in style:
                style["bold"] = bool(font.bold)
            try:
                rgb = font.color.rgb
            except (AttributeError, TypeError, ValueError):
                rgb = None
            if rgb is not None and "color" not in style:
                style["color"] = str(rgb)
            if "font_name" in style and "font_size" in style and "color" in style:
                return style
    return style


def _infer_text_groups(
    slide: Any,
    titles: list[Any],
    subtitles: list[Any],
    bodies: list[Any],
    canvas_height: float,
) -> tuple[list[Any], list[Any], list[Any]]:
    if titles and bodies:
        return titles, subtitles, bodies
    occupied = {shape.element for shape in titles + subtitles + bodies}
    candidates = [
        shape
        for shape in slide.shapes
        if shape.element not in occupied
        and getattr(shape, "has_text_frame", False)
        and shape.text.strip()
        and shape.width / 914400 >= 1.0
        and shape.height / 914400 >= 0.18
    ]
    if not titles and candidates:
        upper = [
            shape for shape in candidates if shape.top / 914400 < canvas_height * 0.28
        ]
        pool = upper or [
            shape for shape in candidates if shape.top / 914400 < canvas_height * 0.55
        ]
        pool = pool or candidates
        title = max(
            pool, key=lambda shape: (_largest_font(shape), shape.width, -shape.top)
        )
        titles = [title]
        candidates.remove(title)
    if not subtitles and candidates and titles:
        title_bottom = titles[0].top + titles[0].height
        nearby = [
            shape
            for shape in candidates
            if shape.top >= title_bottom
            and shape.top / 914400 < canvas_height * 0.85
            and _largest_font(shape) >= 11
        ]
        if nearby:
            subtitles = [min(nearby, key=lambda shape: shape.top)]
    subtitle_elements = {shape.element for shape in subtitles}
    if not bodies:
        bodies = [
            shape
            for shape in candidates
            if shape.element not in subtitle_elements
            and not (
                shape.top / 914400 > canvas_height * 0.9 and _largest_font(shape) <= 10
            )
        ]
        bodies.sort(key=lambda item: (item.left, item.top))
    return titles, subtitles, bodies


def _set_text_frame(
    shape: Any,
    paragraphs: list[str],
    *,
    font_name: str | None = None,
    font_size: float | None = None,
    color: str | None = None,
    bold_first: bool = False,
    bullets: bool = False,
) -> None:
    existing_style = _shape_text_style(shape)
    font_name = font_name or existing_style.get("font_name")
    font_size = font_size or existing_style.get("font_size")
    color = color or existing_style.get("color")
    alignment = existing_style.get("alignment")
    inherited_bold = existing_style.get("bold")
    if font_size and paragraphs:
        width = max(0.1, shape.width / 914400 - 0.08)
        height = max(0.1, shape.height / 914400 - 0.04)
        fitted_size = float(font_size)
        while fitted_size > 9:
            chars_per_line = max(6, int(width * 72 / (fitted_size * 0.52)))
            line_count = sum(
                max(1, math.ceil(len(str(text)) / chars_per_line))
                for text in paragraphs
            )
            paragraph_gap = max(0, len(paragraphs) - 1) * fitted_size * 0.12 / 72
            required_height = (
                line_count * fitted_size * 1.22 / 72 + paragraph_gap + 0.06
            )
            if required_height <= height:
                break
            fitted_size -= 0.5
        font_size = fitted_size
    text_frame = shape.text_frame
    text_frame.clear()
    text_frame.word_wrap = True
    text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    text_frame.vertical_anchor = MSO_ANCHOR.TOP
    if not paragraphs:
        return
    for index, text in enumerate(paragraphs):
        paragraph = (
            text_frame.paragraphs[0] if index == 0 else text_frame.add_paragraph()
        )
        paragraph.text = str(text)
        paragraph.level = 0
        if not bullets:
            paragraph.alignment = alignment or PP_ALIGN.LEFT
        if paragraph.runs:
            font = paragraph.runs[0].font
            if font_name:
                font.name = font_name
            if font_size:
                font.size = Pt(font_size)
            if color:
                font.color.rgb = _rgb(color)
            if inherited_bold is not None:
                font.bold = inherited_bold
            if bold_first and index == 0:
                font.bold = True


def _add_textbox(
    slide: Any,
    text: str,
    box: tuple[float, float, float, float],
    *,
    font_name: str,
    font_size: float,
    color: str,
    bold: bool = False,
    align: PP_ALIGN = PP_ALIGN.LEFT,
) -> Any:
    x, y, w, h = box
    shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    _set_text_frame(
        shape,
        [text],
        font_name=font_name,
        font_size=font_size,
        color=color,
        bold_first=bold,
    )
    shape.text_frame.paragraphs[0].alignment = align
    shape.text_frame.margin_left = Inches(0.04)
    shape.text_frame.margin_right = Inches(0.04)
    shape.text_frame.margin_top = Inches(0.02)
    shape.text_frame.margin_bottom = Inches(0.02)
    return shape


def _zone_from_shape(shape: Any) -> tuple[float, float, float, float]:
    return (
        shape.left / 914400,
        shape.top / 914400,
        shape.width / 914400,
        shape.height / 914400,
    )


def _content_zone(
    slide: Any,
    design: dict[str, Any],
    title_shapes: list[Any],
) -> tuple[float, float, float, float]:
    canvas = design["canvas"]
    spacing = design["spacing"]
    left = float(spacing.get("typical_left_margin_inches", 0.6))
    right = float(spacing.get("typical_right_margin_inches", 0.6))
    title_bottom = 1.2
    if title_shapes:
        title_bottom = (
            max((item.top + item.height) / 914400 for item in title_shapes) + 0.15
        )
    return (
        left,
        title_bottom,
        max(2.0, float(canvas["width_inches"]) - left - right),
        max(1.0, float(canvas["height_inches"]) - title_bottom - 0.45),
    )


def _split_zone(
    zone: tuple[float, float, float, float], ratio: float = 0.44
) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
    x, y, w, h = zone
    gap = 0.28
    left_w = max(1.0, w * ratio - gap / 2)
    return (x, y, left_w, h), (x + left_w + gap, y, max(1.0, w - left_w - gap), h)


def _add_body_text(
    slide: Any,
    slide_spec: dict[str, Any],
    body_shapes: list[Any],
    zone: tuple[float, float, float, float],
    design: dict[str, Any],
) -> tuple[float, float, float, float]:
    font = design["typography"]["primary_font"]
    size = float(design["typography"].get("body_size_pt", 18))
    colors = _theme_colors(design)
    body = slide_spec.get("body", "")
    bullets = slide_spec.get("bullets", [])
    visual = slide_spec.get("visual")

    # Slide exemplars without native placeholders often contain many tiny
    # source-specific labels. Clear those labels and write new content into a
    # safe zone while retaining the exemplar's background and decoration.
    if body_shapes and not any(
        getattr(shape, "is_placeholder", False) for shape in body_shapes
    ):
        bullet_like = [
            shape
            for shape in body_shapes
            if shape.text.lstrip().startswith(("•", "-", "–", "—"))
        ]
        preferred = bullet_like if len(bullet_like) >= 2 else body_shapes
        if not bullet_like and len(preferred) > 2:
            preferred = sorted(
                preferred,
                key=lambda shape: shape.width * shape.height,
                reverse=True,
            )[: max(2, math.ceil(len(preferred) * 0.5))]
        style_source = max(
            preferred,
            key=lambda shape: shape.width * shape.height,
            default=body_shapes[0],
        )
        source_style = _shape_text_style(style_source)
        left = min(shape.left for shape in preferred) / 914400
        top = min(shape.top for shape in preferred) / 914400
        right = max(shape.left + shape.width for shape in preferred) / 914400
        bottom = max(shape.top + shape.height for shape in preferred) / 914400
        source_zone = (left, top, right - left, bottom - top)
        if source_zone[2] >= 2.8 and source_zone[3] >= 0.75:
            zone = source_zone
        for shape in body_shapes:
            _set_text_frame(shape, [])
        if visual:
            text_zone, visual_zone = _split_zone(zone)
        else:
            text_zone, visual_zone = zone, zone
        paragraphs = ([body] if body else []) + list(bullets)
        if paragraphs:
            text_shape = _add_textbox(
                slide,
                "",
                text_zone,
                font_name=source_style.get("font_name", font),
                font_size=float(source_style.get("font_size", size)),
                color=source_style.get("color", colors.get("dk1", "1F2937")),
            )
            text_shape.name = "BrandDeck Body"
            _set_text_frame(
                text_shape,
                [f"• {item}" if bullets else item for item in paragraphs],
                font_name=source_style.get("font_name", font),
                font_size=float(source_style.get("font_size", size)),
                color=source_style.get("color", colors.get("dk1", "1F2937")),
            )
        return visual_zone

    if len(body_shapes) >= 2:
        text_parts = list(bullets)
        midpoint = max(1, math.ceil(len(text_parts) / 2))
        groups = [text_parts[:midpoint], text_parts[midpoint:]]
        if body:
            groups[0].insert(0, body)
        for shape, paragraphs in zip(body_shapes[:2], groups):
            _set_text_frame(shape, paragraphs, bullets=bool(bullets))
        for shape in body_shapes[2:]:
            _set_text_frame(shape, [])
        return _zone_from_shape(body_shapes[1]) if visual else zone

    if body_shapes:
        body_zone = _zone_from_shape(body_shapes[0])
        if visual:
            text_zone, visual_zone = _split_zone(body_zone)
            body_shapes[0].left = Inches(text_zone[0])
            body_shapes[0].top = Inches(text_zone[1])
            body_shapes[0].width = Inches(text_zone[2])
            body_shapes[0].height = Inches(text_zone[3])
        else:
            visual_zone = body_zone
        paragraphs = ([body] if body else []) + list(bullets)
        _set_text_frame(body_shapes[0], paragraphs, bullets=bool(bullets))
        return visual_zone

    if visual:
        text_zone, visual_zone = _split_zone(zone)
    else:
        text_zone, visual_zone = zone, zone
    paragraphs = ([body] if body else []) + list(bullets)
    if paragraphs:
        text_shape = _add_textbox(
            slide,
            "",
            text_zone,
            font_name=font,
            font_size=size,
            color=colors.get("dk1", "1F2937"),
        )
        text_shape.name = "BrandDeck Body"
        _set_text_frame(
            text_shape,
            [f"• {item}" if bullets else item for item in paragraphs],
            font_name=font,
            font_size=size,
            color=colors.get("dk1", "1F2937"),
        )
    return visual_zone


def _add_metric_cards(
    slide: Any,
    visual: dict[str, Any],
    zone: tuple[float, float, float, float],
    design: dict[str, Any],
) -> None:
    items = [item for item in visual.get("items", []) if isinstance(item, dict)][:6]
    if not items:
        return
    colors = _theme_colors(design)
    accents = [colors.get("accent1"), colors.get("accent2"), colors.get("accent3")]
    x, y, w, h = zone
    columns = 2 if len(items) <= 4 else 3
    rows = math.ceil(len(items) / columns)
    gap = 0.16
    card_w = (w - gap * (columns - 1)) / columns
    card_h = min(1.55, (h - gap * (rows - 1)) / rows)
    font = design["typography"]["primary_font"]
    for index, item in enumerate(items):
        col, row = index % columns, index // columns
        cx, cy = x + col * (card_w + gap), y + row * (card_h + gap)
        card = slide.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE,
            Inches(cx),
            Inches(cy),
            Inches(card_w),
            Inches(card_h),
        )
        card.name = "BrandDeck Metric Card"
        card.fill.solid()
        card.fill.fore_color.rgb = _rgb(colors.get("lt2"), "F3F4F6")
        card.line.color.rgb = _rgb(accents[index % len(accents)], "4472C4")
        card.line.width = Pt(1.5)
        _add_textbox(
            slide,
            str(item.get("value", "")),
            (cx + 0.12, cy + 0.18, card_w - 0.24, card_h * 0.44),
            font_name=font,
            font_size=max(20, design["typography"].get("title_size_pt", 30) * 0.72),
            color=accents[index % len(accents)],
            bold=True,
        ).name = "BrandDeck Metric Value"
        _add_textbox(
            slide,
            str(item.get("label", "")),
            (cx + 0.12, cy + card_h * 0.58, card_w - 0.24, card_h * 0.28),
            font_name=font,
            font_size=max(10, design["typography"].get("body_size_pt", 16) * 0.8),
            color=colors.get("dk1", "1F2937"),
        ).name = "BrandDeck Metric Label"


def _add_bar_chart(
    slide: Any,
    visual: dict[str, Any],
    zone: tuple[float, float, float, float],
    design: dict[str, Any],
) -> None:
    categories = [str(item) for item in visual.get("categories", [])][:12]
    series = [item for item in visual.get("series", []) if isinstance(item, dict)][:5]
    if not categories or not series:
        return
    chart_data = CategoryChartData()
    chart_data.categories = categories
    for item in series:
        values = [_number(value) for value in item.get("values", [])[: len(categories)]]
        values.extend([0.0] * (len(categories) - len(values)))
        chart_data.add_series(str(item.get("name", "Series")), values)
    x, y, w, h = zone
    chart = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(x),
        Inches(y),
        Inches(w),
        Inches(h),
        chart_data,
    ).chart
    chart.has_legend = len(series) > 1
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
    chart.value_axis.has_major_gridlines = True
    colors = _theme_colors(design)
    palette = [colors.get("accent1"), colors.get("accent2"), colors.get("accent3")]
    for index, chart_series in enumerate(chart.series):
        chart_series.format.fill.solid()
        chart_series.format.fill.fore_color.rgb = _rgb(
            palette[index % len(palette)], "4472C4"
        )


def _add_pie_chart(
    slide: Any,
    visual: dict[str, Any],
    zone: tuple[float, float, float, float],
    design: dict[str, Any],
) -> None:
    categories = [str(item) for item in visual.get("categories", [])][:10]
    values = [_number(value) for value in visual.get("values", [])[: len(categories)]]
    if not categories or len(values) != len(categories):
        return
    chart_data = ChartData()
    chart_data.categories = categories
    chart_data.add_series("Values", values)
    x, y, w, h = zone
    chart = slide.shapes.add_chart(
        XL_CHART_TYPE.PIE,
        Inches(x),
        Inches(y),
        Inches(w),
        Inches(h),
        chart_data,
    ).chart
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.RIGHT
    chart.plots[0].has_data_labels = True
    chart.plots[0].data_labels.show_percentage = True


def _add_table(
    slide: Any,
    visual: dict[str, Any],
    zone: tuple[float, float, float, float],
    design: dict[str, Any],
) -> None:
    headers = [str(value) for value in visual.get("headers", [])][:8]
    rows = [row for row in visual.get("rows", []) if isinstance(row, list)][:10]
    if not headers:
        return
    rows = [[str(value) for value in row[: len(headers)]] for row in rows]
    x, y, w, h = zone
    table_shape = slide.shapes.add_table(
        len(rows) + 1,
        len(headers),
        Inches(x),
        Inches(y),
        Inches(w),
        Inches(h),
    )
    table_shape.name = "BrandDeck Table"
    table = table_shape.table
    colors = _theme_colors(design)
    font = design["typography"]["primary_font"]
    values = [headers] + rows
    for row_index, row in enumerate(values):
        for col_index, value in enumerate(row):
            cell = table.cell(row_index, col_index)
            cell.text = value
            cell.margin_left = Inches(0.06)
            cell.margin_right = Inches(0.06)
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(
                colors.get("accent1") if row_index == 0 else colors.get("lt2"),
                "4472C4" if row_index == 0 else "F3F4F6",
            )
            for paragraph in cell.text_frame.paragraphs:
                paragraph.alignment = PP_ALIGN.LEFT
                for run in paragraph.runs:
                    run.font.name = font
                    run.font.size = Pt(
                        max(9, design["typography"].get("body_size_pt", 16) * 0.68)
                    )
                    run.font.bold = row_index == 0
                    run.font.color.rgb = _rgb(
                        colors.get("lt1") if row_index == 0 else colors.get("dk1"),
                        "FFFFFF" if row_index == 0 else "1F2937",
                    )


def _add_timeline(
    slide: Any,
    visual: dict[str, Any],
    zone: tuple[float, float, float, float],
    design: dict[str, Any],
) -> None:
    items = [item for item in visual.get("items", []) if isinstance(item, dict)][:6]
    if not items:
        return
    colors = _theme_colors(design)
    font = design["typography"]["primary_font"]
    x, y, w, h = zone
    center_y = y + h * 0.43
    line = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(x), Inches(center_y), Inches(w), Inches(0.035)
    )
    line.name = "BrandDeck Timeline Line"
    line.fill.solid()
    line.fill.fore_color.rgb = _rgb(colors.get("accent1"), "4472C4")
    line.line.fill.background()
    step = w / max(1, len(items))
    for index, item in enumerate(items):
        cx = x + step * index + step / 2
        dot = slide.shapes.add_shape(
            MSO_SHAPE.OVAL,
            Inches(cx - 0.11),
            Inches(center_y - 0.09),
            Inches(0.22),
            Inches(0.22),
        )
        dot.name = "BrandDeck Timeline Point"
        dot.fill.solid()
        dot.fill.fore_color.rgb = _rgb(colors.get("accent2"), "70AD47")
        dot.line.fill.background()
        text_y = center_y - 0.72 if index % 2 == 0 else center_y + 0.24
        _add_textbox(
            slide,
            str(item.get("label", "")),
            (cx - step * 0.43, text_y, step * 0.86, 0.28),
            font_name=font,
            font_size=max(10, design["typography"].get("body_size_pt", 16) * 0.75),
            color=colors.get("accent1", "4472C4"),
            bold=True,
            align=PP_ALIGN.CENTER,
        ).name = "BrandDeck Timeline Label"
        _add_textbox(
            slide,
            str(item.get("detail", "")),
            (cx - step * 0.43, text_y + 0.3, step * 0.86, 0.55),
            font_name=font,
            font_size=max(8, design["typography"].get("body_size_pt", 16) * 0.58),
            color=colors.get("dk1", "1F2937"),
            align=PP_ALIGN.CENTER,
        ).name = "BrandDeck Timeline Detail"


def _add_visual(
    slide: Any,
    visual: dict[str, Any] | None,
    zone: tuple[float, float, float, float],
    design: dict[str, Any],
) -> None:
    if not visual:
        return
    visual_type = visual.get("type")
    if visual_type == "metric_cards":
        _add_metric_cards(slide, visual, zone, design)
    elif visual_type == "bar_chart":
        _add_bar_chart(slide, visual, zone, design)
    elif visual_type == "pie_chart":
        _add_pie_chart(slide, visual, zone, design)
    elif visual_type == "table":
        _add_table(slide, visual, zone, design)
    elif visual_type == "timeline":
        _add_timeline(slide, visual, zone, design)


def _remove_unmatched_content_images(
    slide: Any,
    slide_spec: dict[str, Any],
    design: dict[str, Any],
    repeated_image_hashes: set[str],
) -> None:
    if slide_spec.get("role") == "image":
        return
    visual = slide_spec.get("visual") or {}
    if visual.get("type") == "image":
        return
    canvas_area = max(
        0.1,
        float(design["canvas"]["width_inches"])
        * float(design["canvas"]["height_inches"]),
    )
    for shape in list(slide.shapes):
        if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
            continue
        area_ratio = shape.width / 914400 * (shape.height / 914400) / canvas_area
        try:
            image_hash = str(shape.image.sha1)
        except (AttributeError, ValueError):
            image_hash = ""
        # Full-bleed imagery is treated as background. Every other non-recurring
        # picture is source content (logos, screenshots, icons, diagrams) and
        # must not leak into a text-only generated slide. Brand assets are
        # identified by recurring across a substantial share of the template.
        if 0.0005 <= area_ratio <= 0.8 and image_hash not in repeated_image_hashes:
            slide.shapes._spTree.remove(shape.element)


def _fill_slide(
    slide: Any,
    slide_spec: dict[str, Any],
    design: dict[str, Any],
    repeated_image_hashes: set[str],
) -> None:
    # Example-slide charts and tables carry source-specific data. Keep the
    # surrounding style, but replace those data objects with the new plan.
    for shape in list(slide.shapes):
        if getattr(shape, "has_chart", False) or getattr(shape, "has_table", False):
            slide.shapes._spTree.remove(shape.element)
    _remove_unmatched_content_images(slide, slide_spec, design, repeated_image_hashes)
    titles, subtitles, bodies = _placeholder_groups(slide)
    titles, subtitles, bodies = _infer_text_groups(
        slide, titles, subtitles, bodies, float(design["canvas"]["height_inches"])
    )
    if not slide_spec.get("subtitle") and slide_spec.get("role") == "content":
        bodies = subtitles + bodies
        subtitles = []
    assigned_text = {shape.element for shape in titles + subtitles + bodies}
    for shape in slide.shapes:
        if (
            shape.element not in assigned_text
            and getattr(shape, "has_text_frame", False)
            and shape.text.strip()
        ):
            _set_text_frame(shape, [])
    font = design["typography"]["primary_font"]
    colors = _theme_colors(design)
    title = slide_spec.get("title", "")
    subtitle = slide_spec.get("subtitle", "")

    if titles:
        _set_text_frame(titles[0], [title])
    else:
        margin = float(design["spacing"].get("typical_left_margin_inches", 0.6))
        _add_textbox(
            slide,
            title,
            (margin, 0.45, design["canvas"]["width_inches"] - margin * 2, 0.8),
            font_name=font,
            font_size=float(design["typography"].get("title_size_pt", 30)),
            color=colors.get("dk1", "1F2937"),
            bold=True,
        ).name = "BrandDeck Title"

    if subtitles:
        _set_text_frame(subtitles[0], [subtitle])
    elif subtitle and slide_spec.get("role") in {"cover", "closing", "section"}:
        canvas = design["canvas"]
        _add_textbox(
            slide,
            subtitle,
            (0.8, canvas["height_inches"] * 0.64, canvas["width_inches"] - 1.6, 0.65),
            font_name=font,
            font_size=float(design["typography"].get("body_size_pt", 18)),
            color=colors.get("dk1", "1F2937"),
            align=PP_ALIGN.CENTER,
        ).name = "BrandDeck Subtitle"

    zone = _content_zone(slide, design, titles)
    visual_zone = _add_body_text(slide, slide_spec, bodies, zone, design)
    _add_visual(slide, slide_spec.get("visual"), visual_zone, design)

    notes = slide_spec.get("speaker_notes")
    if notes:
        try:
            slide.notes_slide.notes_text_frame.text = notes
        except (AttributeError, NotImplementedError):
            pass


def outline_markdown(plan: dict[str, Any]) -> str:
    lines = [f"# {plan.get('title', 'Presentation')}", ""]
    for index, slide in enumerate(plan.get("slides", []), 1):
        lines.extend(
            [
                f"## {index}. {slide.get('title', '')}",
                "",
                f"Pattern: `{slide.get('pattern_id', 'unassigned')}` · Role: `{slide.get('role', 'content')}`",
                "",
            ]
        )
        if slide.get("subtitle"):
            lines.append(slide["subtitle"])
            lines.append("")
        if slide.get("body"):
            lines.append(slide["body"])
            lines.append("")
        for bullet in slide.get("bullets", []):
            lines.append(f"- {bullet}")
        if slide.get("visual"):
            lines.extend(["", f"Visual: `{slide['visual'].get('type')}`"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def compose_presentation(
    *,
    template_dir: Path,
    plan: dict[str, Any],
    output_path: Path,
    design_system: dict[str, Any],
) -> dict[str, Any]:
    original = template_dir / "original.pptx"
    prs = Presentation(original)
    layouts = _all_layouts(prs)
    source_slides = list(prs.slides)
    image_hash_counts: Counter[str] = Counter()
    for source_slide in source_slides:
        for shape in source_slide.shapes:
            if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
                continue
            try:
                image_hash_counts[str(shape.image.sha1)] += 1
            except (AttributeError, ValueError):
                continue
    recurring_threshold = max(3, math.ceil(len(source_slides) * 0.4))
    repeated_image_hashes = {
        image_hash
        for image_hash, count in image_hash_counts.items()
        if count >= recurring_threshold
    }
    original_slide_ids = list(prs.slides._sldIdLst)
    catalog = read_json(template_dir / "pattern_catalog.json")
    patterns = {item["id"]: item for item in catalog.get("patterns", [])}

    selected_patterns: list[str] = []
    for generated_index, slide_spec in enumerate(plan.get("slides", [])):
        layout_index = int(slide_spec.get("layout_index", 0))
        if layout_index < 0 or layout_index >= len(layouts):
            layout_index = 0
        pattern = patterns.get(str(slide_spec.get("pattern_id")), {})
        example_indices = [
            int(value)
            for value in pattern.get("example_slide_indices", [])
            if isinstance(value, int) and 1 <= value <= len(source_slides)
        ]
        if example_indices:
            source_index = example_indices[generated_index % len(example_indices)] - 1
            slide = _clone_slide(prs, source_slides[source_index])
        else:
            slide = prs.slides.add_slide(layouts[layout_index])
        _fill_slide(slide, slide_spec, design_system, repeated_image_hashes)
        selected_patterns.append(
            str(slide_spec.get("pattern_id", f"layout-{layout_index}"))
        )

    _remove_slide_ids(prs, original_slide_ids)

    prs.core_properties.title = str(plan.get("title", "Generated presentation"))
    prs.core_properties.subject = (
        "Generated by BrandDeck AI from an arbitrary PPTX template"
    )
    prs.core_properties.keywords = "BrandDeck AI, template-adaptive, generated"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(output_path)
    result = {
        "output": str(output_path.resolve()),
        "slide_count": len(prs.slides),
        "patterns_used": selected_patterns,
    }
    write_json(output_path.with_suffix(".manifest.json"), result)
    return result
