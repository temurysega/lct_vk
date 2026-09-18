from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat


def powerpoint_render_enabled() -> bool:
    configured = os.getenv("BRANDDECK_POWERPOINT_QA", "auto").strip().lower()
    if configured in {"0", "false", "no", "off"}:
        return False
    if os.name != "nt" or shutil.which("powershell") is None:
        return False
    if configured == "auto":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "PowerPoint.Application"):
                pass
        except OSError:
            return False
    return configured in {"auto", "1", "true", "yes", "on"}


def _powershell_literal(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _slide_number(path: Path) -> int:
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else 1_000_000_000


def inspect_powerpoint_render(
    presentation: str | Path,
    output_dir: str | Path,
    *,
    expected_slide_count: int,
) -> dict[str, Any]:
    source = Path(presentation).resolve()
    target = Path(output_dir).resolve()
    if not powerpoint_render_enabled():
        return {
            "status": "skipped",
            "score": None,
            "renderer": "powerpoint_com",
            "issues": [],
            "previews": [],
        }
    target.mkdir(parents=True, exist_ok=True)
    script = f"""
$ErrorActionPreference = 'Stop'
$app = New-Object -ComObject PowerPoint.Application
try {{
  $deck = $app.Presentations.Open('{_powershell_literal(source)}', -1, 0, 0)
  try {{
    $deck.Export('{_powershell_literal(target)}', 'PNG', 1600, 900)
  }} finally {{
    $deck.Close()
  }}
}} finally {{
  $app.Quit()
  [System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($app) | Out-Null
}}
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    issues: list[dict[str, Any]] = []
    if completed.returncode != 0:
        message = (
            completed.stderr or completed.stdout or "PowerPoint export failed"
        ).strip()
        return {
            "status": "failed",
            "score": 0,
            "renderer": "powerpoint_com",
            "issues": [
                {
                    "severity": "error",
                    "code": "powerpoint_render_failed",
                    "message": message[-500:],
                }
            ],
            "previews": [],
        }

    previews = sorted(
        (path for path in target.iterdir() if path.suffix.lower() == ".png"),
        key=_slide_number,
    )
    if len(previews) != expected_slide_count:
        issues.append(
            {
                "severity": "error",
                "code": "powerpoint_slide_missing",
                "message": (
                    f"PowerPoint exported {len(previews)} of "
                    f"{expected_slide_count} expected slides"
                ),
            }
        )

    preview_reports: list[dict[str, Any]] = []
    for index, preview in enumerate(previews, 1):
        with Image.open(preview) as image:
            rgb = image.convert("RGB")
            reduced = rgb.resize((200, 112)).convert("L")
            deviation = float(ImageStat.Stat(reduced).stddev[0])
            quantized = rgb.resize((160, 90)).quantize(colors=32)
            histogram = quantized.getcolors(maxcolors=32 * 160 * 90) or []
            dominant = max((count for count, _ in histogram), default=0) / (160 * 90)
            width, height = rgb.size
        blank = deviation < 4.0 or dominant > 0.985
        if blank:
            issues.append(
                {
                    "severity": "error",
                    "code": "visually_blank",
                    "slide": index,
                    "message": "Rendered slide is visually blank or nearly uniform",
                }
            )
        preview_reports.append(
            {
                "slide": index,
                "path": str(preview),
                "width": width,
                "height": height,
                "luma_stddev": round(deviation, 2),
                "dominant_color_fraction": round(dominant, 3),
            }
        )

    error_count = sum(issue["severity"] == "error" for issue in issues)
    warning_count = sum(issue["severity"] == "warning" for issue in issues)
    score = max(0, 100 - error_count * 25 - warning_count * 5)
    status = "failed" if error_count else "warning" if warning_count else "passed"
    return {
        "status": status,
        "score": score,
        "renderer": "powerpoint_com",
        "issues": issues,
        "previews": preview_reports,
    }
