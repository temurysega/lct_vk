"""Rebuild the bundled pictogram set from an installed ``lucide-react`` package.

Only icons listed in ``assets/icons/keywords.json`` are extracted, so the
runtime package stays small and needs neither Node.js nor network access::

    python -m slide_agent.icon_builder --lucide frontend/node_modules/lucide-react
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

ICON_ROOT = Path(__file__).with_name("assets") / "icons"
_ELEMENT = re.compile(r'\[\s*"(\w+)",\s*\{([^}]*)\}\s*\]')
_ATTRIBUTE = re.compile(r'(\w+):\s*"([^"]*)"')
_ALLOWED = {"path", "circle", "ellipse", "rect", "line", "polyline", "polygon"}


def parse_icon_module(source: str) -> list[list]:
    nodes = []
    for tag, attributes in _ELEMENT.findall(source):
        if tag not in _ALLOWED:
            raise ValueError(f"Unsupported SVG element: {tag}")
        values = {
            name: value
            for name, value in _ATTRIBUTE.findall(attributes)
            if name != "key"
        }
        nodes.append([tag, values])
    if not nodes:
        raise ValueError("No SVG elements found")
    return nodes


def build(lucide_root: Path, destination: Path = ICON_ROOT) -> dict:
    package = json.loads((lucide_root / "package.json").read_text(encoding="utf-8"))
    config = json.loads((destination / "keywords.json").read_text(encoding="utf-8"))
    icons = {}
    for name in sorted(config["icons"]):
        module = lucide_root / "dist" / "esm" / "icons" / f"{name}.js"
        icons[name] = parse_icon_module(module.read_text(encoding="utf-8"))
    bundle = {
        "package": package["name"],
        "version": package["version"],
        "license": package["license"],
        "view_box": [0, 0, 24, 24],
        "stroke_width": 2,
        "icons": icons,
    }
    (destination / "lucide.json").write_text(
        json.dumps(bundle, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    shutil.copyfile(lucide_root / "LICENSE", destination / "LICENSE-lucide.txt")
    return {"icons": len(icons), "version": package["version"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lucide", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.lucide.resolve()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
