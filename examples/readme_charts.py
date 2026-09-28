"""Build the README charts from two dataset_report.json files.

Usage:
    python examples/readme_charts.py \
        --before slide-workspace/brief-qwen3.5-9b-2026-09-25/dataset_report.json \
        --after slide-workspace/final-2026-09-26j/dataset_report.json

Each chart is written in a light and a dark variant (docs/charts/*-light.png,
*-dark.png) so the README can switch them with <picture>. The printed table is
the text version of the same numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PureWindowsPath

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, PathPatch, Rectangle
from matplotlib.path import Path as MplPath

ROOT = Path(__file__).resolve().parents[1]
NAMES = {
    "VK Tech шаблон": "VK Tech",
    "VK_WorkSpace_Клиентская_конференция_Шаблон_03": "VK WorkSpace",
    "Шаблон презентации VK Education": "VK Education",
}
ORDER = ["VK Tech", "VK WorkSpace", "VK Education"]
VARIANTS = ["balanced", "columns", "focus"]

# Reference data-viz palette: categorical slots 1-3, chrome and ink per mode.
THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "primary": "#0b0b0b",
        "secondary": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "band": "#f0efec",
        "series": ["#2a78d6", "#eb6834", "#1baf7a"],
    },
    "dark": {
        "surface": "#1a1a19",
        "primary": "#ffffff",
        "secondary": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "band": "#383835",
        "series": ["#3987e5", "#d95926", "#199e70"],
    },
}
DPI = 200
GAP_PT = 4 / DPI * 72  # 2 CSS px at 2x density
plt.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans"]


def load(path: Path) -> dict[str, dict]:
    report = json.loads(path.read_text(encoding="utf-8"))
    decks: dict[str, dict] = {}
    for run in report["runs"]:
        stem = PureWindowsPath(run["template"]).stem
        name = NAMES.get(stem, stem)
        variants = {v["variant"]["id"]: v for v in run["batch"]["variants"]}
        decks[name] = {
            "elapsed": float(run["elapsed_seconds"]),
            "llm": sum((run.get("brief_timings_seconds") or {}).values()),
            "variants": variants,
        }
    return decks


def frame(ax, theme: dict) -> None:
    ax.set_facecolor(theme["surface"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(theme["axis"])
    ax.tick_params(colors=theme["muted"], length=0, labelsize=9)
    ax.xaxis.grid(True, color=theme["grid"], linewidth=0.5)
    ax.set_axisbelow(True)


def heading(fig, theme: dict, title: str, subtitle: str) -> None:
    fig.text(0.02, 0.95, title, color=theme["primary"], fontsize=12.5,
             fontweight="bold", va="top")
    fig.text(0.02, 0.95 - 0.105 * 3.2 / fig.get_figheight(), subtitle,
             color=theme["secondary"], fontsize=9.5, va="top")


def legend(fig, theme: dict, items: list[tuple[str, str, str]], y: float) -> None:
    x = 0.02
    for label, color, kind in items:
        if kind == "dot":
            fig.add_artist(Line2D([x + 0.007], [y], marker="o", markersize=7,
                                  color=color, transform=fig.transFigure))
        else:
            fig.patches.append(
                FancyBboxPatch((x, y - 0.012), 0.014, 0.024,
                               boxstyle="round,pad=0,rounding_size=0.003",
                               transform=fig.transFigure, color=color, figure=fig)
            )
        text = fig.text(x + 0.022, y, label, color=theme["secondary"],
                        fontsize=9, va="center")
        fig.canvas.draw()
        width = text.get_window_extent().width / fig.bbox.width
        x += 0.022 + width + 0.03


def _end_rounded(ax, x: float, y: float, w: float, h: float) -> MplPath:
    """Bar path with a 4 CSS px rounded data end and a square start."""
    ax.figure.canvas.draw()
    px_per_x = ax.transData.transform((1, 0))[0] - ax.transData.transform((0, 0))[0]
    px_per_y = ax.transData.transform((0, 1))[1] - ax.transData.transform((0, 0))[1]
    rx, ry = 8 / px_per_x, 8 / px_per_y
    right, top = x + w, y + h
    vertices = [
        (x, y), (right - rx, y), (right, y), (right, y + ry), (right, top - ry),
        (right, top), (right - rx, top), (x, top), (x, y),
    ]
    codes = [
        MplPath.MOVETO, MplPath.LINETO, MplPath.CURVE3, MplPath.CURVE3,
        MplPath.LINETO, MplPath.CURVE3, MplPath.CURVE3, MplPath.LINETO,
        MplPath.CLOSEPOLY,
    ]
    return MplPath(vertices, codes)


def time_chart(after: dict[str, dict], theme: dict, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 3.2), dpi=DPI)
    fig.patch.set_facecolor(theme["surface"])
    fig.subplots_adjust(left=0.16, right=0.9, top=0.64, bottom=0.14)
    frame(ax, theme)
    labels = ["LLM: структура и текст", "Три варианта: сборка, экспорт, QA",
              "Анализ шаблона и прочее"]
    height = 0.42
    ax.set_xlim(0, 330)
    ax.set_ylim(-0.6, len(ORDER) - 0.1)
    for row, name in enumerate(reversed(ORDER)):
        deck = after[name]
        variants = sum(float(v["elapsed_seconds"]) for v in deck["variants"].values())
        parts = [deck["llm"], variants, max(0.0, deck["elapsed"] - deck["llm"] - variants)]
        left = 0.0
        for index, value in enumerate(parts):
            color = theme["series"][index]
            last = index == len(parts) - 1
            if last:
                ax.add_patch(PathPatch(
                    _end_rounded(ax, left, row - height / 2, value, height),
                    color=color, linewidth=0))
            else:
                ax.add_patch(Rectangle((left, row - height / 2), value, height,
                                       color=color, linewidth=0))
            if left:
                ax.add_patch(Rectangle((left - 0.9, row - height / 2 - 0.02), 1.8,
                                       height + 0.04, color=theme["surface"],
                                       linewidth=0))
            left += value
        ax.text(left + 5, row, f"{deck['elapsed']:.0f} с", va="center",
                color=theme["primary"], fontsize=9.5, fontweight="bold")
    ax.axvline(300, ymax=0.84, color=theme["secondary"], linewidth=0.75)
    ax.text(300, len(ORDER) - 0.3, "лимит ТЗ — 5 мин", ha="center",
            color=theme["secondary"], fontsize=8.5)
    ax.set_xticks(range(0, 301, 60))
    ax.set_xticklabels([f"{t} с" for t in range(0, 301, 60)])
    ax.set_yticks(range(len(ORDER)))
    ax.set_yticklabels(list(reversed(ORDER)), color=theme["secondary"], fontsize=9.5)
    heading(fig, theme, "Полный сценарий укладывается в 5 минут",
            "Краткий бриф → Qwen3.5-9B на RTX 2070 SUPER → три варианта с экспортом PPTX/PDF/HTML")
    legend(fig, theme, [(label, theme["series"][i], "bar") for i, label in enumerate(labels)], 0.73)
    fig.savefig(out, facecolor=theme["surface"])
    plt.close(fig)


def qa_chart(
    before: dict[str, dict], after: dict[str, dict], theme: dict, out: Path, revision: str
) -> None:
    rows = [(name, variant) for name in ORDER for variant in VARIANTS]
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=DPI)
    fig.patch.set_facecolor(theme["surface"])
    fig.subplots_adjust(left=0.27, right=0.84, top=0.72, bottom=0.1)
    frame(ax, theme)
    marker = {
        "markersize": 7,
        "markeredgewidth": GAP_PT,
        "markeredgecolor": theme["surface"],
        "zorder": 3,
    }
    positions = [len(rows) - 1 - i - (i // 3) * 0.4 for i in range(len(rows))]
    for index, (name, variant) in enumerate(rows):
        y = positions[index]
        old = before[name]["variants"][variant]["qa"]["score"]
        new = after[name]["variants"][variant]["qa"]["score"]
        ax.plot([old, new], [y, y], color=theme["axis"], linewidth=1.5, zorder=2)
        ax.plot([old], [y], "o", color=theme["muted"], **marker)
        ax.plot([new], [y], "o", color=theme["series"][0], **marker)
        ax.text(101.2, y, f"{old} → {new}", va="center", color=theme["secondary"],
                fontsize=9, fontfamily=["Segoe UI", "DejaVu Sans"])
        label = f"{name} · {variant}" if variant == "balanced" else variant
        ax.text(79.2, y, label, ha="right", va="center", fontsize=9,
                color=theme["primary"] if variant == "balanced" else theme["secondary"])
    ax.set_xlim(80, 100.6)
    ax.set_ylim(min(positions) - 0.7, max(positions) + 0.6)
    ax.set_xticks([80, 85, 90, 95, 100])
    ax.set_yticks([])
    heading(fig, theme, "Структурный QA тех же девяти колод до и после исправлений",
            f"Один бриф и три шаблона; «до» — прогон на коммите {revision}, «после» — финальный код")
    legend(fig, theme, [("До исправлений", theme["muted"], "dot"),
                        ("После исправлений", theme["series"][0], "dot")], 0.79)
    fig.savefig(out, facecolor=theme["surface"])
    plt.close(fig)


def fill_chart(after: dict[str, dict], theme: dict, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 3.1), dpi=DPI)
    fig.patch.set_facecolor(theme["surface"])
    fig.subplots_adjust(left=0.16, right=0.96, top=0.7, bottom=0.16)
    frame(ax, theme)
    ax.axvspan(25, 75, color=theme["band"], zorder=0, linewidth=0)
    ax.text(50, len(ORDER) - 0.28, "норма Приложения 1: 25–75 %", ha="center",
            color=theme["secondary"], fontsize=8.5)
    for row, name in enumerate(reversed(ORDER)):
        values = [
            slide["coverage"] * 100
            for variant in VARIANTS
            for slide in after[name]["variants"][variant]["qa"]["rendered_fill_audit"]["slides"]
        ]
        offsets = [((i * 37) % 11 - 5) / 22 for i in range(len(values))]
        ax.plot(values, [row + o for o in offsets], "o", color=theme["series"][0],
                markersize=6, markeredgewidth=GAP_PT, markeredgecolor=theme["surface"],
                linestyle="none", zorder=3)
        low = min(values)
        ax.text(low - 2, row, f"мин. {low:.0f} %", ha="right", va="center",
                color=theme["secondary"], fontsize=8.5)
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.6, len(ORDER) - 0.05)
    ax.set_xticks(range(0, 101, 25))
    ax.set_xticklabels([f"{t} %" for t in range(0, 101, 25)])
    ax.set_yticks(range(len(ORDER)))
    ax.set_yticklabels(list(reversed(ORDER)), color=theme["secondary"], fontsize=9.5)
    heading(fig, theme, "Ни один контентный слайд не заполнен меньше чем на четверть",
            "Доля слайда под новым контентом на PDF-рендере; точка — слайд одной из девяти колод")
    fig.savefig(out, facecolor=theme["surface"])
    plt.close(fig)


def table(before: dict[str, dict], after: dict[str, dict]) -> str:
    lines = [
        "| Шаблон | Весь сценарий, с | LLM, с | QA до → после (balanced / columns / focus) | Картинки | Мин. заполненность |",
        "|---|---:|---:|---|---|---:|",
    ]
    for name in ORDER:
        deck = after[name]
        qa = " / ".join(
            f"{before[name]['variants'][v]['qa']['score']} → {deck['variants'][v]['qa']['score']}"
            for v in VARIANTS
        )
        images = " / ".join(
            f"{deck['variants'][v]['images']['placed']} из {deck['variants'][v]['images']['available']}"
            for v in VARIANTS
        )
        low = min(
            slide["coverage"]
            for v in VARIANTS
            for slide in deck["variants"][v]["qa"]["rendered_fill_audit"]["slides"]
        )
        lines.append(
            f"| {name} | {deck['elapsed']:.0f} | {deck['llm']:.0f} | {qa} | {images} | {low * 100:.0f} % |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path,
                        default=ROOT / "slide-workspace/brief-qwen3.5-9b-2026-09-25/dataset_report.json")
    parser.add_argument("--after", type=Path,
                        default=ROOT / "slide-workspace/final-2026-09-26j/dataset_report.json")
    parser.add_argument("--out", type=Path, default=ROOT / "docs/charts")
    args = parser.parse_args()
    before, after = load(args.before), load(args.after)
    revision = str(
        json.loads(args.before.read_text(encoding="utf-8")).get("git_revision_at_start")
        or "238612e"
    )[:7]
    args.out.mkdir(parents=True, exist_ok=True)
    for mode, theme in THEMES.items():
        time_chart(after, theme, args.out / f"time-{mode}.png")
        qa_chart(before, after, theme, args.out / f"qa-{mode}.png", revision)
        fill_chart(after, theme, args.out / f"fill-{mode}.png")
    sys.stdout.reconfigure(encoding="utf-8")
    print(table(before, after))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
