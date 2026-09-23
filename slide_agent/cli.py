from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

from .analyzer import analyze_template
from .exporter import find_libreoffice
from .imagegen import ImageSettings
from .pictograms import icon_manifest
from .qa import inspect_presentation
from .service import (
    configured_client,
    generate_deck,
    generate_variants,
    list_templates,
    run_pipeline,
)
from .utils import read_json, resolve_workspace


def _print(value: Any, as_json: bool = False) -> None:
    if as_json or isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="branddeck",
        description="Analyze arbitrary PPTX/PDF templates and generate branded presentations.",
    )
    parser.add_argument(
        "--workspace", help="Workspace directory (default: ./slide-workspace)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser(
        "analyze", help="Extract a template design system and pattern catalog"
    )
    analyze.add_argument("template", help="Path to a .pptx or .pdf template")
    analyze.add_argument("--name", help="Stable human-readable template name")
    analyze.add_argument(
        "--offline", action="store_true", help="Skip Inference API enhancement"
    )
    analyze.add_argument("--force", action="store_true", help="Rebuild cached analysis")

    generate = sub.add_parser(
        "generate", help="Generate a presentation from analyzed template"
    )
    generate.add_argument(
        "--template", help="Template id, analysis directory, or .pptx/.pdf path"
    )
    generate.add_argument(
        "--content", required=True, help="Content file path or inline text"
    )
    generate.add_argument("--output", help="Output .pptx path")
    generate.add_argument("--slides", type=int, help="Requested number of slides")
    generate.add_argument(
        "--offline", action="store_true", help="Use deterministic local planner"
    )
    generate.add_argument("--qa-retries", type=int, default=1, choices=range(4))

    run = sub.add_parser(
        "run", help="Analyze a template and generate a deck in one command"
    )
    run.add_argument(
        "--template", required=True, help="Path to a .pptx or .pdf template"
    )
    run.add_argument(
        "--content", required=True, help="Content file path or inline text"
    )
    run.add_argument("--output", help="Output .pptx path")
    run.add_argument("--slides", type=int, help="Requested number of slides")
    run.add_argument("--offline", action="store_true")

    variants = sub.add_parser(
        "variants", help="Generate three layouts with identical content"
    )
    variants.add_argument("--template", required=True)
    variants.add_argument("--content", required=True)
    variants.add_argument("--slides", type=int, default=10)
    variants.add_argument("--offline", action="store_true")
    for command in (run, generate, variants):
        command.add_argument(
            "--images",
            nargs="+",
            default=[],
            help="Pictures (PNG/JPG/WEBP) or folders to place on matching slides",
        )
        command.add_argument(
            "--export",
            choices=("pptx", "all"),
            default="pptx",
            help="Also export PDF and HTML using LibreOffice",
        )
        command.add_argument(
            "--mode",
            choices=("auto", "source", "brief"),
            default="auto",
            help="brief: expand a short brief with the model; source: lay out full material",
        )
        command.add_argument(
            "--purpose",
            choices=("feature", "product", "project", "initiative"),
            help="Purpose of the deck for brief mode (detected from the brief if omitted)",
        )

    inspect = sub.add_parser("inspect", help="Run structural QA on a generated PPTX")
    inspect.add_argument("presentation", help="Path to .pptx")
    inspect.add_argument(
        "--template", help="Analyzed template directory for style checks"
    )
    inspect.add_argument("--report", help="Path to write qa_report.json")

    sub.add_parser("templates", help="List analyzed templates")
    sub.add_parser("doctor", help="Check runtime and Inference API configuration")

    serve = sub.add_parser("serve", help="Start the REST API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    return parser


def _image_paths(values: list[str]) -> list[Path]:
    from .images import IMAGE_SUFFIXES

    paths: list[Path] = []
    for value in values:
        candidate = Path(value).expanduser()
        if candidate.is_dir():
            paths.extend(
                sorted(
                    p for p in candidate.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES
                )
            )
        elif candidate.is_file():
            paths.append(candidate)
        else:
            raise FileNotFoundError(f"Image not found: {value}")
    return paths


def _doctor() -> dict[str, Any]:
    client = configured_client()
    return {
        "python": sys.version.split()[0],
        "python_pptx": bool(importlib.util.find_spec("pptx")),
        "pillow": bool(importlib.util.find_spec("PIL")),
        "fastapi": bool(importlib.util.find_spec("fastapi")),
        "libreoffice": find_libreoffice(),
        "pdf_renderer": bool(importlib.util.find_spec("fitz")),
        "uvicorn": bool(importlib.util.find_spec("uvicorn")),
        "inference_configured": client is not None,
        "inference_model": os.getenv("INFERENCE_MODEL", ""),
        "image_generation_configured": ImageSettings.from_env().enabled,
        "pictograms": icon_manifest(),
        "workspace": str(resolve_workspace()),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = args.workspace
    try:
        if args.command == "analyze":
            output = analyze_template(
                args.template,
                workspace=workspace,
                name=args.name,
                client=configured_client(offline=args.offline),
                force=args.force,
            )
            manifest = read_json(output / "manifest.json")
            manifest["path"] = str(output.resolve())
            _print(manifest, args.json)
        elif args.command == "generate":
            result = generate_deck(
                template=args.template,
                content=args.content,
                workspace=workspace,
                output=args.output,
                slide_count=args.slides,
                offline=args.offline,
                qa_retries=args.qa_retries,
                export_formats=("pdf", "html") if args.export == "all" else (),
                images=_image_paths(args.images),
                mode=args.mode,
                purpose=args.purpose,
            )
            _print(result, args.json)
            return int(result["status"] == "failed")
        elif args.command == "run":
            result = run_pipeline(
                template_path=args.template,
                content=args.content,
                workspace=workspace,
                output=args.output,
                slide_count=args.slides,
                offline=args.offline,
                export_formats=("pdf", "html") if args.export == "all" else (),
                images=_image_paths(args.images),
                mode=args.mode,
                purpose=args.purpose,
            )
            _print(result, args.json)
            return int(result["status"] == "failed")
        elif args.command == "variants":
            result = generate_variants(
                template=args.template,
                content=args.content,
                workspace=workspace,
                slide_count=args.slides,
                offline=args.offline,
                export_formats=("pdf", "html") if args.export == "all" else (),
                images=_image_paths(args.images),
                mode=args.mode,
                purpose=args.purpose,
            )
            _print(result, args.json)
            return int(
                result["status"] == "failed" or result["diversity_status"] != "passed"
            )
        elif args.command == "inspect":
            design = None
            if args.template:
                design = read_json(Path(args.template) / "design_system.json")
            _print(
                inspect_presentation(
                    args.presentation,
                    design_system=design,
                    report_path=args.report,
                ),
                args.json,
            )
        elif args.command == "templates":
            _print(list_templates(workspace), True)
        elif args.command == "doctor":
            _print(_doctor(), True)
        elif args.command == "serve":
            if workspace:
                os.environ["BRANDDECK_WORKSPACE"] = str(resolve_workspace(workspace))
            try:
                import uvicorn
            except ImportError as exc:
                raise RuntimeError(
                    "Install API dependencies: pip install -e '.[api]'"
                ) from exc
            uvicorn.run(
                "slide_agent.api:app", host=args.host, port=args.port, reload=False
            )
        return 0
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
