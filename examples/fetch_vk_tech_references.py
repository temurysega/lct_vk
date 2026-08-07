"""Download public VK Tech decks and convert them to editable PPTX fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

from slide_agent.pdf_importer import pdf_to_pptx
from slide_agent.utils import write_json

REFERENCES = [
    {
        "id": "vk-tech-private-cloud-2025",
        "title": "ПАК как опорная точка цифрового суверенитета",
        "speaker": "Станислав Погоржельский, VK Cloud / VK Tech",
        "source_page": "https://spb.dcforum.ru/archive/programm?day=1",
        "download_url": "https://spb.dcforum.ru/sites/default/files/spb/15.00-15.20_pogorzhelskiy_2025_17_06_ivent_cod_v_spb_v2_short-1.pdf",
    },
    {
        "id": "vk-tech-training-2026",
        "title": "Как сделать линейку тренингов на основе матрицы компетенций",
        "speaker": "Павел Лапаев и Владислав Грищенко, VK Tech",
        "source_page": "https://edu-forum.pro/",
        "yandex_public_url": "https://disk.360.yandex.ru/i/aKyEeARywFax5g",
    },
]


def _download_url(reference: dict[str, str]) -> str:
    if reference.get("download_url"):
        return reference["download_url"]
    public_url = reference["yandex_public_url"]
    api = (
        "https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key="
        + urllib.parse.quote(public_url, safe="")
    )
    with urllib.request.urlopen(api, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))["href"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default="external-fixtures/vk-tech")
    parser.add_argument(
        "--max-pages", type=int, help="Limit pages per source for quick checks"
    )
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for reference in REFERENCES:
        pdf_path = output / f"{reference['id']}.pdf"
        pptx_path = output / f"{reference['id']}.pptx"
        with urllib.request.urlopen(_download_url(reference), timeout=90) as response:
            payload = response.read()
        pdf_path.write_bytes(payload)
        conversion = pdf_to_pptx(pdf_path, pptx_path, max_pages=args.max_pages)
        pptx_payload = pptx_path.read_bytes()
        manifest.append(
            {
                **reference,
                "pdf_sha256": hashlib.sha256(payload).hexdigest(),
                "pdf_size_bytes": len(payload),
                "pptx_sha256": hashlib.sha256(pptx_payload).hexdigest(),
                "pptx_size_bytes": len(pptx_payload),
                "conversion": conversion,
            }
        )
        print(f"Prepared {reference['id']}: {conversion['slide_count']} slides")
    write_json(output / "manifest.json", {"references": manifest})


if __name__ == "__main__":
    main()
