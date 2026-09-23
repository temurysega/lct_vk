from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .utils import write_json


def _color(value: Any, fallback: str = "FFFFFF") -> str:
    candidate = str(value or "").strip().lstrip("#").upper()
    if len(candidate) == 8:
        candidate = candidate[-6:]
    return f"#{candidate}" if len(candidate) == 6 else f"#{fallback}"


def _rect(item: dict[str, Any], scale: float) -> tuple[int, int, int, int]:
    x = float(item.get("left", 0)) * scale
    y = float(item.get("top", 0)) * scale
    w = float(item.get("width", 0)) * scale
    h = float(item.get("height", 0)) * scale
    return round(x), round(y), round(x + w), round(y + h)


def _background(slide: dict[str, Any]) -> str:
    effective = slide.get("effective_background", {})
    return _color(effective.get("color"), "FFFFFF")


def _draw_text(
    draw: ImageDraw.ImageDraw, element: dict[str, Any], scale: float
) -> None:
    bounds = _rect(element, scale)
    paragraphs = element.get("paragraphs", [])
    text = "\n".join(str(item.get("text", "")) for item in paragraphs).strip()
    if not text:
        return
    font_data = next(
        (item.get("font", {}) for item in paragraphs if item.get("font")), {}
    )
    requested_size = max(
        9, min(60, int(float(font_data.get("size_pt", 15)) * scale / 72))
    )

    def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        try:
            return ImageFont.truetype("arial.ttf", size)
        except OSError:
            return ImageFont.load_default()

    def wrap(value: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> str:
        max_width = max(24, bounds[2] - bounds[0] - 8)
        lines: list[str] = []
        for raw_line in value.splitlines() or [value]:
            words = raw_line.split()
            if not words:
                lines.append("")
                continue
            current = words[0]
            for word in words[1:]:
                candidate = f"{current} {word}"
                if draw.textlength(candidate, font=font) <= max_width:
                    current = candidate
                else:
                    lines.append(current)
                    current = word
            lines.append(current)
        return "\n".join(lines)

    font = load_font(requested_size)
    wrapped = wrap(text[:1200], font)
    available_height = max(18, bounds[3] - bounds[1] - 6)
    for size in range(requested_size, 8, -1):
        font = load_font(size)
        wrapped = wrap(text[:1200], font)
        text_bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=3)
        if text_bounds[3] - text_bounds[1] <= available_height:
            break
    draw.multiline_text(
        (bounds[0] + 4, bounds[1] + 3),
        wrapped,
        fill=_color(font_data.get("color"), "202124"),
        font=font,
        spacing=3,
    )


def _draw_image(
    canvas: Image.Image,
    image_data: dict[str, Any],
    scale: float,
    assets_dir: Path,
) -> None:
    bounds = _rect(image_data, scale)
    width = max(1, bounds[2] - bounds[0])
    height = max(1, bounds[3] - bounds[1])
    candidates = [
        image_data.get("exported_path"),
        image_data.get("path"),
        assets_dir / str(image_data.get("name", "")),
    ]
    source = next(
        (
            Path(candidate)
            for candidate in candidates
            if candidate and Path(candidate).is_file()
        ),
        None,
    )
    if source is not None:
        try:
            with Image.open(source) as original:
                rendered = original.convert("RGBA")
                rendered.thumbnail((width, height), Image.Resampling.LANCZOS)
                left = bounds[0] + (width - rendered.width) // 2
                top = bounds[1] + (height - rendered.height) // 2
                canvas.paste(rendered, (left, top), rendered)
            return
        except OSError:
            # Native EMF/WMF can be valid Office assets without a Pillow decoder.
            # This is a schematic preview; the original asset stays in the PPTX.
            pass
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(bounds, fill="#DDE7F2", outline="#6B7280", width=2)
    draw.line((bounds[0], bounds[1], bounds[2], bounds[3]), fill="#6B7280")
    draw.line((bounds[0], bounds[3], bounds[2], bounds[1]), fill="#6B7280")


def render_context_previews(
    context: dict[str, Any],
    output_dir: str | Path,
    *,
    max_slides: int = 8,
) -> dict[str, Any]:
    """Rasterize an OOXML-derived schematic for multimodal pattern analysis.

    The schematic is deterministic and works without PowerPoint or LibreOffice.
    It preserves exact element geometry and colors; embedded pictures are marked
    as image regions. This is not used for composition, only as an additional
    computer-vision signal for the Inference API.
    """
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    presentation = context.get("presentation", {})
    width_inches = float(presentation.get("slide_width_inches", 13.333))
    height_inches = float(presentation.get("slide_height_inches", 7.5))
    scale = 96.0
    width, height = round(width_inches * scale), round(height_inches * scale)

    all_slides = context.get("slides", [])
    slides: list[dict[str, Any]] = []
    seen_layouts: set[int] = set()
    for slide in all_slides:
        layout_index = slide.get("layout_index")
        if isinstance(layout_index, int) and layout_index not in seen_layouts:
            slides.append(slide)
            seen_layouts.add(layout_index)
        if len(slides) >= max_slides:
            break
    if len(slides) < max_slides:
        selected_indices = {item.get("index") for item in slides}
        slides.extend(
            item for item in all_slides if item.get("index") not in selected_indices
        )
        slides = slides[:max_slides]
    layouts = context.get("slide_layouts", [])
    masters = context.get("slide_masters", [])
    rendered: list[dict[str, Any]] = []
    for slide in slides:
        canvas = Image.new("RGB", (width, height), _background(slide))
        draw = ImageDraw.Draw(canvas)
        layout_index = slide.get("layout_index")
        layout = (
            layouts[layout_index]
            if isinstance(layout_index, int) and layout_index < len(layouts)
            else {}
        )
        master_index = layout.get("master_index")
        master = (
            masters[master_index]
            if isinstance(master_index, int) and master_index < len(masters)
            else {}
        )
        for layer in (master, layout, slide):
            for shape in layer.get("shapes", []):
                bounds = _rect(shape, scale)
                fill_data = shape.get("fill", {})
                fill = (
                    _color(fill_data.get("color"), "E5E7EB")
                    if fill_data.get("type") not in {None, "inherit", "none"}
                    else None
                )
                line_color = shape.get("line", {}).get("color")
                outline = _color(line_color) if line_color else None
                if fill or outline:
                    draw.rectangle(bounds, fill=fill, outline=outline, width=1)
                if shape.get("type") in {"TABLE", "CHART"}:
                    draw.text(
                        (bounds[0] + 4, bounds[1] + 4),
                        str(shape.get("type")),
                        fill="#111827",
                    )
            for image in layer.get("images", []):
                _draw_image(canvas, image, scale, target.parent / "images")
        for text in slide.get("text_elements", []):
            _draw_text(draw, text, scale)
        path = target / f"slide-{int(slide.get('index', len(rendered) + 1)):02d}.png"
        canvas.save(path, optimize=True)
        rendered.append(
            {
                "slide_index": slide.get("index"),
                "layout_index": slide.get("layout_index"),
                "path": str(path.resolve()),
                "kind": "ooxml_schematic",
            }
        )

    manifest = {
        "renderer": "ooxml_schematic_v1",
        "description": "Geometry-accurate raster previews for optional multimodal analysis",
        "previews": rendered,
    }
    write_json(target / "manifest.json", manifest)
    return manifest
