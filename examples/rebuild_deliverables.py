"""Rebuild the nine published decks (3 templates × 3 variants) on the current commit.

One command on the machine with the organisers' templates and the model server:

    python examples/rebuild_deliverables.py            # preflight, run, publish, verify
    python examples/rebuild_deliverables.py --check    # preflight only

Steps, each stopping at the first failure:

1. preflight: committed and unchanged code, config and inputs; the three source
   templates by SHA-256; LibreOffice; a reachable model server;
2. ``examples/run_dataset.py`` with ``examples/brief_demo.json``;
3. ``examples/collect_deliverables.py`` into ``deliverables/``;
4. ``examples/export_run_evidence.py`` into ``reports/final-dataset/evidence.json``;
5. ``examples/export_run_evidence.py --verify``.

An existing workspace of the config is moved aside first, so every attempt
analyses the templates from scratch and its time is comparable with the
published runs. Model sampling differs between attempts: when a gate of the
config fails nothing is published, and the command can simply be run again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "examples" / "brief_demo.json"
# The organisers' templates; the same hashes are listed in reports/final-dataset/README.md.
EXPECTED_TEMPLATES = {
    "VK Tech шаблон.pptx": "cbbe3aa6a21d23cebc4d1383b93dd07a9cea460d683de1903567b616c839485d",
    "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx": "1b8883114486c69dff706e9c4fd9382727c4506c2cfa3ec34f86987f1e852f2f",
    "Шаблон презентации VK Education.pptx": "9ef2323ed5f49f464aee5ae7065f1f57bb92c8be3a635be507d59819e285cfe0",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_templates(directory: Path) -> list[str]:
    """Exactly the three organisers' PPTX files, byte for byte."""
    if not directory.is_dir():
        return [f"Template folder not found: {directory}"]
    errors = []
    present = {path.name: path for path in directory.glob("*.pptx")}
    for name, expected in EXPECTED_TEMPLATES.items():
        if name not in present:
            errors.append(f"Template missing: {name}")
        elif sha256(present[name]) != expected:
            errors.append(f"Template differs from the organisers' file (SHA-256): {name}")
    # run_dataset.py builds every PPTX of the folder; a fourth one breaks the 3 × 3 set.
    errors += [f"Unexpected PPTX in the template folder: {name}" for name in sorted(present) if name not in EXPECTED_TEMPLATES]
    return errors


def check_committed(root: Path, paths: list[str]) -> list[str]:
    """The run must be tied to a commit: no local edits in code, config or inputs."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", *paths],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError:
        return ["git is not installed: the run cannot be tied to a commit"]
    if result.returncode != 0:
        return [f"git status failed: {result.stderr.strip()}"]
    changed = sorted(line[3:] for line in result.stdout.splitlines() if line.strip())
    return [f"Uncommitted change, commit or revert it first: {path}" for path in changed]


def normalize_line_endings(root: Path, paths: list[str]) -> list[str]:
    """Rewrite committed text inputs checked out with CRLF before .gitattributes.

    Evidence hashes are taken over file bytes, and .gitattributes fixes LF for
    them on every OS; an older Windows checkout keeps CRLF until the files are
    written again. The files are unchanged against the commit, so rewriting them
    from the index loses nothing.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--eol", "--", *paths],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    stale = []
    for record in listed.stdout.split("\0"):
        info, _, path = record.partition("\t")
        # "i/lf w/crlf attr/text eol=lf": LF in Git, CRLF on disk.
        if path and "w/crlf" in info.split() and "eol=lf" in info:
            stale.append(path)
    if not stale:
        return []
    # git diff compares after the eol conversion: a real edit stays and is reported later.
    edited = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", *stale],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    ).stdout.split("\n")
    stale = [path for path in stale if path not in edited]
    if stale:
        subprocess.run(["git", "checkout-index", "--force", "--index", "--", *stale], cwd=root, check=True)
    return stale


def models_url(base_url: str) -> str:
    return base_url.rstrip("/").removesuffix("/chat/completions") + "/models"


def check_model(base_url: str, api_key: str | None, *, timeout: float = 10) -> list[str]:
    """The OpenAI-compatible server answers /models before a ten-minute run starts."""
    if not base_url:
        return ["INFERENCE_BASE_URL is not set in the config or the environment"]
    request = urllib.request.Request(models_url(base_url))
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return [
            (
                f"Model server is not reachable at {models_url(base_url)}: {exc}. "
                "Start it: powershell -File deploy/llama/start-llama.ps1"
            )
        ]
    return []


def move_aside(workspace: Path, *, now: datetime | None = None) -> Path | None:
    """Keep a previous attempt for inspection and start the new one empty."""
    if not workspace.exists():
        return None
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    target = workspace.with_name(f"{workspace.name}-before-{stamp}")
    workspace.rename(target)
    return target


def preflight(config_path: Path) -> list[str]:
    from slide_agent.exporter import find_libreoffice

    config = json.loads(config_path.read_text(encoding="utf-8"))
    tracked = [
        "slide_agent",
        config_path.resolve().relative_to(ROOT).as_posix(),
        config["content"],
    ]
    if config.get("images"):
        tracked.append(config["images"])
    rewritten = normalize_line_endings(ROOT, tracked)
    if rewritten:
        print(f"Line endings set to LF in {len(rewritten)} committed files (see .gitattributes)")
    errors = check_committed(ROOT, tracked)
    errors += check_templates((ROOT / config["templates"]).resolve())
    if find_libreoffice() is None:
        errors.append("LibreOffice is not found: PDF export and render checks need it")
    # run_dataset.py lets the config override the environment, so check the same URL.
    base_url = config.get("inference", {}).get("INFERENCE_BASE_URL") or os.getenv("INFERENCE_BASE_URL", "")
    errors += check_model(base_url, os.getenv("INFERENCE_API_KEY"))
    return errors


def step(title: str, *args: str) -> int:
    print(f"\n== {title}", flush=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=False).returncode


def summary(report_path: Path) -> list[str]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    lines = [
        (
            f"Commit {report.get('git_revision_at_start')}, "
            f"{report.get('started_at_utc')} – {report.get('finished_at_utc')}"
        ),
        "| Template | Three variants, s | LLM, s | QA balanced / columns / focus | Images |",
        "|---|---:|---:|---|---|",
    ]
    for run in report["runs"]:
        timings = run.get("brief_timings_seconds") or {}
        variants = run.get("batch", {}).get("variants", [])
        scores = " / ".join(str(deck["qa"]["score"]) for deck in variants)
        images = " / ".join(
            f"{deck['images']['placed']} of {deck['images']['available']}" for deck in variants
        )
        lines.append(
            f"| {Path(run['template']).stem} | {run['elapsed_seconds']:.0f} "
            f"| {sum(timings.values()):.0f} | {scores} | {images} |"
        )
    return lines


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--check", action="store_true", help="only run the preflight checks")
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    errors = preflight(config_path)
    if errors:
        print("Preflight failed:\n" + "\n".join(f"  - {error}" for error in errors))
        return 1
    print("Preflight passed: committed code and inputs, organisers' templates, LibreOffice, model server")
    if args.check:
        return 0
    workspace = ROOT / config["workspace"]
    previous = move_aside(workspace)
    if previous:
        print(f"Previous attempt moved to {previous}")
    report = workspace / "dataset_report.json"
    relative_config = config_path.relative_to(ROOT).as_posix()
    if step("Three templates × three variants", "examples/run_dataset.py", "--config", relative_config):
        print(
            f"\nThe run did not pass every gate of {relative_config}; nothing was published.\n"
            f"Details: {workspace / 'index.html'} and {report}.\n"
            "Model text differs between attempts: run this command again."
        )
        return 1
    if step("Publish decks", "examples/collect_deliverables.py", "--report", str(report)):
        return 1
    if step("Export evidence", "examples/export_run_evidence.py", "--report", str(report)):
        return 1
    if step("Verify evidence", "examples/export_run_evidence.py", "--verify"):
        return 1
    print("\n" + "\n".join(summary(report)))
    print(
        "\nDone. Update the numbers in README.md («Результаты») and reports/final-dataset/README.md,\n"
        "then commit deliverables/ and reports/final-dataset/evidence.json."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
