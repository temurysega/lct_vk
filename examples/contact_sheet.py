"""Contact sheets (all slides of a deck on one PNG) for a run_dataset.py workspace."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image

from slide_agent.utils import read_json


def sheet(pngs: list[Path], output: Path, columns: int = 4, width: int = 480) -> None:
    tiles = []
    for path in pngs:
        image = Image.open(path).convert("RGB")
        tiles.append(image.resize((width, round(image.height * width / image.width))))
    height = max(tile.height for tile in tiles)
    rows = -(-len(tiles) // columns)
    gap = 8
    canvas = Image.new(
        "RGB",
        (columns * width + (columns - 1) * gap, rows * height + (rows - 1) * gap),
        "#8a8a8a",
    )
    for index, tile in enumerate(tiles):
        row, col = divmod(index, columns)
        canvas.paste(tile, (col * (width + gap), row * (height + gap)))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--variant", default="balanced")
    args = parser.parse_args()
    report = read_json(args.workspace / "dataset_report.json")
    for run in report["runs"]:
        for deck in run.get("batch", {}).get("variants", []):
            if deck["variant"]["id"] != args.variant:
                continue
            exports = Path(deck["presentation_dir"]) / "exports"
            pngs = sorted(
                exports.glob("slide-*.png"),
                key=lambda p: int(re.search(r"(\d+)", p.stem).group(1)),
            )
            name = re.sub(r"\W+", "-", Path(run["template"]).stem).strip("-").lower()
            target = args.output / f"{name}-{args.variant}.png"
            sheet(pngs, target)
            print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
