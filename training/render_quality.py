"""Conservative rejection of visible text defects in a real LibreOffice PDF.

This filter detects selected defects; it is not a proof of visual perfection.
"""

import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from slide_agent.exporter import find_libreoffice
from training.curation import is_page_marker


def render_pdf(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        return
    executable = find_libreoffice()
    if not executable:
        raise RuntimeError("LibreOffice is required for corpus curation")
    with tempfile.TemporaryDirectory(prefix="ru-render-") as temp:
        stage = Path(temp)
        command = [
            executable,
            f"-env:UserInstallation={(stage / 'profile').as_uri()}",
            "--headless",
            "--convert-to",
            'pdf:impress_pdf_Export:{"ExportHiddenSlides":{"type":"boolean","value":"true"}}',
            "--outdir",
            str(stage),
            str(source.resolve()),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=180,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        pdf = stage / (source.stem + ".pdf")
        if result.returncode or not pdf.is_file():
            raise RuntimeError(f"No rendered PDF: {source.name}")
        shutil.copy2(pdf, destination)


def contrast_ratio(a, b) -> float:
    def luminance(rgb):
        v = [x / 255 for x in rgb]
        v = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]

    x, y = sorted((luminance(a), luminance(b)))
    return (y + 0.05) / (x + 0.05)


def audit_page(page) -> dict:
    import pymupdf as fitz

    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
    lines = []
    low_contrast = []
    outside = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            text = " ".join(s["text"] for s in spans).strip()
            if len(text) < 3 or is_page_marker(text):
                continue
            size = max(s["size"] for s in spans)
            rect = fitz.Rect(line["bbox"])
            if size < 12:
                continue
            lines.append((rect, text))
            if not (page.rect + (-1, -1, 1, 1)).contains(rect):
                outside.append(text[:100])
            for span in spans:
                if len(span["text"].strip()) < 3 or span["size"] < 12:
                    continue
                r = fitz.Rect(span["bbox"])
                colors = []
                for i in range(11):
                    x = r.x0 + (r.width * i / 10)
                    for y in (r.y0 - 1, r.y1 + 1):
                        if 0 <= x < pix.width and 0 <= y < pix.height:
                            colors.append(pix.pixel(int(x), int(y))[:3])
                if not colors:
                    continue
                background, votes = Counter(colors).most_common(1)[0]
                if votes < len(colors) * 0.45:
                    continue  # gradient/image background: require manual inspection
                c = span["color"]
                foreground = ((c >> 16) & 255, (c >> 8) & 255, c & 255)
                ratio = contrast_ratio(foreground, background)
                if ratio < 3:
                    low_contrast.append(
                        {"text": span["text"][:100], "ratio": round(ratio, 2)}
                    )
    overlaps = []
    for i, (left, text) in enumerate(lines):
        for right, other in lines[i + 1 :]:
            area = (left & right).get_area()
            if area > 0.18 * min(left.get_area(), right.get_area()):
                overlaps.append([text[:90], other[:90]])
    issues = {
        k: v
        for k, v in {
            "low_contrast": low_contrast,
            "text_overlap": overlaps,
            "text_outside_canvas": outside,
        }.items()
        if v
    }
    if not lines:
        issues["no_readable_text"] = True
    return {"status": "reject" if issues else "pass", "issues": issues}


def audit_pdf(path: Path) -> list[dict]:
    import pymupdf as fitz

    with fitz.open(path) as document:
        return [{"slide": i, **audit_page(page)} for i, page in enumerate(document, 1)]
