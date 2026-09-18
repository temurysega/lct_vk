"""Export the actual PPTX through LibreOffice; never replace its native objects."""

from __future__ import annotations

import html
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def find_libreoffice() -> str | None:
    configured = os.getenv("BRANDDECK_LIBREOFFICE")
    candidates = [configured] if configured else []
    candidates += [shutil.which("soffice"), shutil.which("libreoffice")]
    if os.name == "nt":
        candidates += [
            str(
                Path(os.getenv("PROGRAMFILES", "C:/Program Files"))
                / "LibreOffice/program/soffice.com"
            )
        ]
    candidates += ["/Applications/LibreOffice.app/Contents/MacOS/soffice"]
    return next((str(p) for p in candidates if p and Path(p).is_file()), None)


def export_presentation(
    presentation: Path,
    output_dir: Path,
    *,
    formats: tuple[str, ...] = ("pdf", "html"),
    expected_slide_count: int | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """Produce PDF, self-contained HTML and previews from one office render.

    Missing export dependencies are reported explicitly. A schematic preview is
    never presented as an office render or as a successful PDF export.
    """
    if set(formats) - {"pdf", "html"}:
        raise ValueError("Export formats must be pdf and/or html")
    if not formats:
        return {"status": "skipped", "artifacts": {}, "previews": [], "issues": []}
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "status": "failed",
        "renderer": "libreoffice",
        "artifacts": {},
        "previews": [],
        "issues": [],
    }
    executable = find_libreoffice()
    if not executable:
        result["issues"].append(
            {
                "code": "export_unavailable",
                "severity": "error",
                "message": "Install LibreOffice or set BRANDDECK_LIBREOFFICE",
            }
        )
        return result
    try:
        import fitz

        # An isolated profile prevents collisions with the user's running office
        # application and permits concurrent API jobs. All arguments stay literal.
        with tempfile.TemporaryDirectory(prefix="branddeck-export-") as staging:
            stage = Path(staging)
            command = [
                executable,
                f"-env:UserInstallation={(stage / 'profile').as_uri()}",
                "--headless",
                "--convert-to",
                "pdf:impress_pdf_Export",
                "--outdir",
                str(stage),
                str(presentation.resolve()),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            rendered = stage / f"{presentation.stem}.pdf"
            if completed.returncode or not rendered.is_file():
                raise RuntimeError("LibreOffice did not produce a PDF")
            pdf = output_dir / "output.pdf"
            shutil.copy2(rendered, pdf)
        sections = []
        with fitz.open(pdf) as document:
            if (
                expected_slide_count is not None
                and len(document) != expected_slide_count
            ):
                raise RuntimeError(
                    f"Rendered {len(document)} pages; expected {expected_slide_count}"
                )
            result["slide_count"] = len(document)
            for index, page in enumerate(document, 1):
                preview = output_dir / f"slide-{index}.png"
                page.get_pixmap(
                    matrix=fitz.Matrix(1200 / page.rect.width, 1200 / page.rect.width),
                    alpha=False,
                ).save(preview)
                result["previews"].append(
                    {
                        "slide": index,
                        "path": str(preview),
                        "width": page.rect.width,
                        "height": page.rect.height,
                    }
                )
                if "html" in formats:
                    sections.append(
                        f'<section aria-label="Slide {index}">'
                        + page.get_svg_image(text_as_path=True)
                        + "</section>"
                    )
        if "pdf" in formats:
            result["artifacts"]["pdf"] = str(pdf)
        if "html" in formats:
            target = output_dir / "output.html"
            target.write_text(
                '<!doctype html><html lang="ru"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                f"<title>{html.escape(presentation.stem)}</title>"
                "<style>body{margin:0;background:#e9edf5}section{max-width:1200px;margin:24px auto;"
                "background:white;box-shadow:0 6px 28px #0002}svg{display:block;width:100%;height:auto}"
                "@media print{body{background:white}section{margin:0;break-after:page;box-shadow:none}}"
                "</style><body>" + "\n".join(sections) + "</body></html>",
                encoding="utf-8",
            )
            result["artifacts"]["html"] = str(target)
        result["status"] = "passed"
    except (
        ImportError,
        OSError,
        RuntimeError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as exc:
        result["issues"].append(
            {"code": "export_failed", "severity": "error", "message": str(exc)}
        )
    return result
