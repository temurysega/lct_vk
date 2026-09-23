"""Convert SVG geometry to editable DrawingML custom geometry.

Pictograms and diagram polygons are written as native ``a:custGeom`` shapes:
they stay vector, recolourable and editable in PowerPoint, and the audit does
not see them as raster pictures. Arcs and quadratic curves are converted to
cubic Bezier segments, the only curve primitive every Office renderer shares.
"""

from __future__ import annotations

import math
import re
from typing import Any

from lxml import etree
from pptx.oxml.ns import qn

Command = tuple  # ("M", x, y) | ("L", x, y) | ("C", x1, y1, x2, y2, x, y) | ("Z",)

_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_KAPPA = 0.5522847498307936
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


class _Scanner:
    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0

    def _skip(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in " \t\r\n,":
            self.index += 1

    def command(self) -> str | None:
        self._skip()
        if (
            self.index < len(self.text)
            and self.text[self.index] in "MmLlHhVvCcSsQqTtAaZz"
        ):
            self.index += 1
            return self.text[self.index - 1]
        return None

    def has_number(self) -> bool:
        self._skip()
        return self.index < len(self.text) and (
            self.text[self.index].isdigit() or self.text[self.index] in "+-."
        )

    def number(self) -> float:
        self._skip()
        match = _NUMBER.match(self.text, self.index)
        if not match:
            raise ValueError(f"Expected a number at {self.index} in path data")
        self.index = match.end()
        return float(match.group())

    def flag(self) -> bool:
        # Arc flags may be written without separators: "a2 2 0 012 2".
        self._skip()
        if self.index >= len(self.text) or self.text[self.index] not in "01":
            raise ValueError(f"Expected an arc flag at {self.index} in path data")
        self.index += 1
        return self.text[self.index - 1] == "1"

    def done(self) -> bool:
        self._skip()
        return self.index >= len(self.text)


def arc_to_cubics(
    x1: float,
    y1: float,
    rx: float,
    ry: float,
    rotation: float,
    large_arc: bool,
    sweep: bool,
    x2: float,
    y2: float,
) -> list[Command]:
    """Endpoint arc to cubic Beziers (SVG 1.1 implementation notes F.6)."""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [("L", x2, y2)]
    phi = math.radians(rotation % 360)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy
    radii_scale = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if radii_scale > 1:
        rx, ry = rx * math.sqrt(radii_scale), ry * math.sqrt(radii_scale)
    numerator = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    factor = math.sqrt(max(0.0, numerator / denominator)) if denominator else 0.0
    if large_arc == sweep:
        factor = -factor
    cxp, cyp = factor * rx * y1p / ry, -factor * ry * x1p / rx
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        value = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return value

    start = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle(
        (x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry
    )
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    segments = max(1, math.ceil(abs(delta) / (math.pi / 2) - 1e-9))
    step = delta / segments
    alpha = 4 / 3 * math.tan(step / 4)
    commands: list[Command] = []

    def point(theta: float) -> tuple[float, float]:
        ex, ey = rx * math.cos(theta), ry * math.sin(theta)
        return cx + cos_phi * ex - sin_phi * ey, cy + sin_phi * ex + cos_phi * ey

    def derivative(theta: float) -> tuple[float, float]:
        ex, ey = -rx * math.sin(theta), ry * math.cos(theta)
        return cos_phi * ex - sin_phi * ey, sin_phi * ex + cos_phi * ey

    theta = start
    for index in range(segments):
        following = theta + step
        px, py = point(theta)
        qx, qy = point(following) if index + 1 < segments else (x2, y2)
        dpx, dpy = derivative(theta)
        dqx, dqy = derivative(following)
        commands.append(
            (
                "C",
                px + alpha * dpx,
                py + alpha * dpy,
                qx - alpha * dqx,
                qy - alpha * dqy,
                qx,
                qy,
            )
        )
        theta = following
    return commands


def parse_path(data: str) -> list[Command]:
    """Parse SVG path data into absolute M/L/C/Z commands."""
    scanner = _Scanner(data)
    commands: list[Command] = []
    x = y = start_x = start_y = 0.0
    last_control: tuple[float, float] | None = None
    last_quadratic: tuple[float, float] | None = None
    command: str | None = None
    while not scanner.done():
        next_command = scanner.command()
        if next_command is not None:
            command = next_command
        elif command is None or command in "Zz":
            raise ValueError("Path data must start with a command")
        relative = command.islower()
        kind = command.upper()
        if kind == "Z":
            commands.append(("Z",))
            x, y = start_x, start_y
            last_control = last_quadratic = None
            command = None
            continue
        ox, oy = (x, y) if relative else (0.0, 0.0)
        if kind == "M":
            x, y = scanner.number() + ox, scanner.number() + oy
            start_x, start_y = x, y
            commands.append(("M", x, y))
            command = "l" if relative else "L"
            last_control = last_quadratic = None
        elif kind == "L":
            x, y = scanner.number() + ox, scanner.number() + oy
            commands.append(("L", x, y))
            last_control = last_quadratic = None
        elif kind == "H":
            x = scanner.number() + ox
            commands.append(("L", x, y))
            last_control = last_quadratic = None
        elif kind == "V":
            y = scanner.number() + oy
            commands.append(("L", x, y))
            last_control = last_quadratic = None
        elif kind in {"C", "S"}:
            if kind == "C":
                c1x, c1y = scanner.number() + ox, scanner.number() + oy
            elif last_control is not None:
                c1x, c1y = 2 * x - last_control[0], 2 * y - last_control[1]
            else:
                c1x, c1y = x, y
            c2x, c2y = scanner.number() + ox, scanner.number() + oy
            x, y = scanner.number() + ox, scanner.number() + oy
            commands.append(("C", c1x, c1y, c2x, c2y, x, y))
            last_control, last_quadratic = (c2x, c2y), None
        elif kind in {"Q", "T"}:
            if kind == "Q":
                qx, qy = scanner.number() + ox, scanner.number() + oy
            elif last_quadratic is not None:
                qx, qy = 2 * x - last_quadratic[0], 2 * y - last_quadratic[1]
            else:
                qx, qy = x, y
            ex, ey = scanner.number() + ox, scanner.number() + oy
            commands.append(
                (
                    "C",
                    x + 2 / 3 * (qx - x),
                    y + 2 / 3 * (qy - y),
                    ex + 2 / 3 * (qx - ex),
                    ey + 2 / 3 * (qy - ey),
                    ex,
                    ey,
                )
            )
            x, y = ex, ey
            last_control, last_quadratic = None, (qx, qy)
        elif kind == "A":
            rx, ry, rotation = scanner.number(), scanner.number(), scanner.number()
            large_arc, sweep = scanner.flag(), scanner.flag()
            ex, ey = scanner.number() + ox, scanner.number() + oy
            commands.extend(
                arc_to_cubics(x, y, rx, ry, rotation, large_arc, sweep, ex, ey)
            )
            x, y = ex, ey
            last_control = last_quadratic = None
    return commands


def _ellipse(cx: float, cy: float, rx: float, ry: float) -> list[Command]:
    kx, ky = rx * _KAPPA, ry * _KAPPA
    return [
        ("M", cx + rx, cy),
        ("C", cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry),
        ("C", cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy),
        ("C", cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry),
        ("C", cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy),
        ("Z",),
    ]


def _points(value: str) -> list[float]:
    return [float(item) for item in _NUMBER.findall(value)]


def element_commands(tag: str, attributes: dict[str, str]) -> list[Command]:
    """Convert one SVG element to absolute commands."""

    def number(name: str, default: float = 0.0) -> float:
        return float(attributes.get(name, default) or default)

    if tag == "path":
        return parse_path(attributes.get("d", ""))
    if tag == "circle":
        return _ellipse(number("cx"), number("cy"), number("r"), number("r"))
    if tag == "ellipse":
        return _ellipse(number("cx"), number("cy"), number("rx"), number("ry"))
    if tag == "line":
        return [("M", number("x1"), number("y1")), ("L", number("x2"), number("y2"))]
    if tag in {"polyline", "polygon"}:
        values = _points(attributes.get("points", ""))
        pairs = list(zip(values[0::2], values[1::2]))
        if not pairs:
            return []
        commands: list[Command] = [("M", *pairs[0])]
        commands.extend(("L", px, py) for px, py in pairs[1:])
        if tag == "polygon":
            commands.append(("Z",))
        return commands
    if tag == "rect":
        x, y = number("x"), number("y")
        width, height = number("width"), number("height")
        rx = attributes.get("rx")
        ry = attributes.get("ry")
        radius_x = float(rx if rx is not None else ry or 0)
        radius_y = float(ry if ry is not None else rx or 0)
        radius_x, radius_y = min(radius_x, width / 2), min(radius_y, height / 2)
        if not radius_x or not radius_y:
            return [
                ("M", x, y),
                ("L", x + width, y),
                ("L", x + width, y + height),
                ("L", x, y + height),
                ("Z",),
            ]
        kx, ky = radius_x * _KAPPA, radius_y * _KAPPA
        right, bottom = x + width, y + height
        return [
            ("M", x + radius_x, y),
            ("L", right - radius_x, y),
            (
                "C",
                right - radius_x + kx,
                y,
                right,
                y + radius_y - ky,
                right,
                y + radius_y,
            ),
            ("L", right, bottom - radius_y),
            (
                "C",
                right,
                bottom - radius_y + ky,
                right - radius_x + kx,
                bottom,
                right - radius_x,
                bottom,
            ),
            ("L", x + radius_x, bottom),
            (
                "C",
                x + radius_x - kx,
                bottom,
                x,
                bottom - radius_y + ky,
                x,
                bottom - radius_y,
            ),
            ("L", x, y + radius_y),
            ("C", x, y + radius_y - ky, x + radius_x - kx, y, x + radius_x, y),
            ("Z",),
        ]
    raise ValueError(f"Unsupported SVG element: {tag}")


def _a(tag: str, **attributes: Any) -> etree._Element:
    element = etree.Element(qn(f"a:{tag}"))
    for name, value in attributes.items():
        element.set(name, str(value))
    return element


def custom_geometry(
    paths: list[list[Command]],
    *,
    width: float,
    height: float,
    filled: bool = False,
    stroked: bool = True,
    scale: int = 1000,
) -> etree._Element:
    """Build ``a:custGeom`` for paths expressed in a ``width`` x ``height`` box."""
    geometry = _a("custGeom")
    for name in ("avLst", "gdLst", "ahLst", "cxnLst"):
        geometry.append(_a(name))
    geometry.append(_a("rect", l="l", t="t", r="r", b="b"))
    path_list = _a("pathLst")
    w, h = max(1, round(width * scale)), max(1, round(height * scale))

    def point(parent: etree._Element, px: float, py: float) -> None:
        parent.append(_a("pt", x=round(px * scale), y=round(py * scale)))

    for commands in paths:
        if not commands:
            continue
        attributes: dict[str, Any] = {"w": w, "h": h}
        if not filled:
            attributes["fill"] = "none"
        if not stroked:
            attributes["stroke"] = "0"
        path = _a("path", **attributes)
        for command in commands:
            if command[0] == "M":
                node = _a("moveTo")
                point(node, command[1], command[2])
            elif command[0] == "L":
                node = _a("lnTo")
                point(node, command[1], command[2])
            elif command[0] == "C":
                node = _a("cubicBezTo")
                for offset in (1, 3, 5):
                    point(node, command[offset], command[offset + 1])
            else:
                node = _a("close")
            path.append(node)
        path_list.append(path)
    geometry.append(path_list)
    return geometry


def apply_geometry(shape: Any, geometry: etree._Element) -> None:
    """Replace a shape's preset geometry with custom geometry in place."""
    properties = shape._element.spPr
    preset = properties.find(qn("a:prstGeom"))
    if preset is not None:
        preset.addprevious(geometry)
        properties.remove(preset)
    else:
        transform = properties.find(qn("a:xfrm"))
        if transform is not None:
            transform.addnext(geometry)
        else:
            properties.insert(0, geometry)


def set_outline(
    shape: Any, color: str | None, width_emu: int, *, round_caps: bool = True
) -> None:
    """Write an explicit outline so theme line styles cannot leak in."""
    properties = shape._element.spPr
    existing = properties.find(qn("a:ln"))
    if existing is not None:
        properties.remove(existing)
    line = _a("ln", w=max(0, int(width_emu)))
    if color is None:
        line.append(_a("noFill"))
    else:
        if round_caps:
            line.set("cap", "rnd")
        fill = _a("solidFill")
        fill.append(_a("srgbClr", val=color))
        line.append(fill)
        if round_caps:
            line.append(_a("round"))
    # a:ln follows the fill element inside spPr.
    anchor = None
    for tag in (
        "a:noFill",
        "a:solidFill",
        "a:gradFill",
        "a:blipFill",
        "a:pattFill",
        "a:grpFill",
    ):
        anchor = properties.find(qn(tag))
        if anchor is not None:
            break
    if anchor is not None:
        anchor.addnext(line)
    else:
        geometry = properties.find(qn("a:custGeom"))
        if geometry is None:
            geometry = properties.find(qn("a:prstGeom"))
        if geometry is not None:
            geometry.addnext(line)
        else:
            properties.append(line)


def drop_theme_style(shape: Any) -> None:
    """Remove ``p:style`` so theme effects (shadows, glow) are not inherited."""
    style = shape._element.find(qn("p:style"))
    if style is not None:
        shape._element.remove(style)
