"""Convert the compact base + trained LoRA to GGUF, test CPU, and publish to Drive."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

if __package__:
    from .check_gguf import check
    from .checkpoints import atomic_json, file_hash
else:
    from check_gguf import check
    from checkpoints import atomic_json, file_hash


def validate_adapter(adapter: Path, profile: dict) -> dict:
    run = json.loads((adapter / "training_run.json").read_text(encoding="utf-8"))
    config = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
    if run["status"] != "completed" or run["base_model"] != profile["base_model"]:
        raise ValueError(
            "Export needs a completed 1.5B run. The old 7B adapter is incompatible."
        )
    if run["base_revision"] != profile["base_revision"]:
        raise ValueError(
            "Training base revision differs from the CPU deployment profile"
        )
    if (
        config["base_model_name_or_path"] != run["base_model"]
        or config["revision"] != run["base_revision"]
    ):
        raise ValueError(
            "Adapter and training metadata refer to different base weights"
        )
    inventory = json.loads((adapter / "SHA256SUMS.json").read_text(encoding="utf-8"))
    required = {"adapter_model.safetensors", "adapter_config.json", "training_run.json"}
    if not required.issubset(inventory):
        raise ValueError("Adapter checksum inventory is incomplete")
    for name, expected in inventory.items():
        path = (adapter / name).resolve()
        if path.parent != adapter.resolve() or file_hash(path) != expected:
            raise ValueError(f"Adapter file checksum mismatch: {name}")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drive-root", type=Path, required=True)
    parser.add_argument("--llama-root", type=Path, required=True)
    parser.add_argument(
        "--local-root", type=Path, default=Path("/content/lct-cpu-export")
    )
    args = parser.parse_args()
    root, llama = args.drive_root.resolve(), args.llama_root.resolve()
    profile = json.loads(
        (root / "scripts/cpu_profile.json").read_text(encoding="utf-8")
    )
    if (
        subprocess.check_output(
            ["git", "-C", str(llama), "rev-parse", "HEAD"], text=True
        ).strip()
        != profile["llama_cpp_revision"]
    ):
        raise ValueError("llama.cpp must match the pinned CPU runtime revision")
    latest = json.loads((root / "weights/latest.json").read_text(encoding="utf-8"))
    adapter = (root / latest["adapter_dir"]).resolve()
    if not adapter.is_relative_to(root):
        raise ValueError("Adapter must be inside this Drive bundle")
    run = validate_adapter(adapter, profile)
    manifest_path = root / "data/manifest.json"
    if run.get("dataset_sha256") != file_hash(manifest_path):
        raise ValueError("Export dataset does not match the completed training run")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or file_hash(path) != expected:
            raise ValueError(f"Package checksum mismatch: {relative}")
    local = args.local_root.resolve() / run["run_id"]
    local.mkdir(parents=True, exist_ok=True)
    from huggingface_hub import snapshot_download

    base = Path(
        snapshot_download(
            run["base_model"],
            revision=run["base_revision"],
            allow_patterns=[
                "*.json",
                "*.safetensors",
                "*.txt",
                "*.model",
                "*.jinja",
                "LICENSE*",
            ],
            local_dir=local / "hf-base",
        )
    )
    portable = local / "cpu"
    portable.mkdir(exist_ok=True)
    repair_note = Path(__file__).with_name("export_repair.json")
    if repair_note.is_file():
        shutil.copy2(repair_note, portable / "export_repair.json")
    full = local / "base-f16.gguf"
    quantized = portable / "base-q4_k_m.gguf"
    lora = portable / "layout-lora-f16.gguf"
    suffix = ".exe" if sys.platform == "win32" else ""
    binaries = llama / "build/bin"
    commands = [
        [
            sys.executable,
            str(llama / "convert_hf_to_gguf.py"),
            str(base),
            "--outfile",
            str(full),
            "--outtype",
            "f16",
        ],
        [
            str(binaries / ("llama-quantize" + suffix)),
            str(full),
            str(quantized),
            "Q4_K_M",
        ],
        [
            sys.executable,
            str(llama / "convert_lora_to_gguf.py"),
            "--base",
            str(base),
            "--outfile",
            str(lora),
            "--outtype",
            "f16",
            str(adapter),
        ],
    ]
    for command in commands:
        subprocess.run(command, check=True)
    for path in (quantized, lora):
        with path.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                raise ValueError(f"Conversion did not produce GGUF: {path}")
    if quantized.stat().st_size > 1400 * 1024**2:
        raise ValueError("Quantized model is too large for this 4 GB profile")
    report = check(
        binaries / ("llama-server" + suffix),
        quantized,
        lora,
        root / "data/validation.jsonl",
        root / "data/planner_smoke.json",
        portable / "cpu_report.json",
    )
    if (
        not report["within_model_memory_budget"]
        or not report["planner_probe"]["valid_structure"]
    ):
        raise ValueError(
            "CPU export did not pass memory/planner checks; see the local cpu_report.json"
        )
    # The deployment decision above is frozen using validation only. Test is
    # evaluated afterwards and never changes adapter enablement or parameters.
    if (root / "data/test.jsonl").exists():
        test_report = check(
            binaries / ("llama-server" + suffix),
            quantized,
            lora,
            root / "data/test.jsonl",
            root / "data/planner_smoke.json",
            portable / "test_report.json",
        )
        test_report.pop("enable_layout_adapter", None)
        test_report["selection_split"] = "validation"
        test_report["frozen_enable_layout_adapter"] = report["enable_layout_adapter"]
        test_report["purpose"] = (
            "Final held-out design systems; do not tune on this report"
        )
        atomic_json(portable / "test_report.json", test_report)
    for name in (
        "training_run.json",
        "metrics.json",
        "requirements.freeze.txt",
        "dataset_manifest.json",
    ):
        shutil.copy2(adapter / name, portable / name)
    shutil.copy2(root / "scripts/cpu_profile.json", portable / "cpu_profile.json")
    deployment_readme = root / "CPU_README.md"
    shutil.copy2(
        deployment_readme if deployment_readme.exists() else root / "README.md",
        portable / "README.md",
    )
    shutil.copy2(llama / "LICENSE", portable / "LICENSE.llama.cpp")
    if (base / "LICENSE").exists():
        shutil.copy2(base / "LICENSE", portable / "LICENSE.model")
    (portable / "export-requirements.freeze.txt").write_text(
        subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True),
        encoding="utf-8",
    )
    (portable / "deployment.env").write_text(
        "INFERENCE_LAYOUT_MODEL="
        + ("lct-layout" if report["enable_layout_adapter"] else "")
        + "\n",
        encoding="utf-8",
    )
    inventory = {
        p.name: {"bytes": p.stat().st_size, "sha256": file_hash(p)}
        for p in portable.iterdir()
        if p.is_file() and p.name != "bundle.json"
    }
    bundle = {
        "schema_version": 1,
        "status": "complete",
        "run_id": run["run_id"],
        "base_model": run["base_model"],
        "base_revision": run["base_revision"],
        "llama_cpp_revision": profile["llama_cpp_revision"],
        "files": inventory,
    }
    atomic_json(portable / "bundle.json", bundle)
    destination = root / "exports" / (run["run_id"] + "-" + uuid4().hex[:6])
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(portable, destination / "cpu")
    for name, expected in inventory.items():
        if file_hash(destination / "cpu" / name) != expected["sha256"]:
            raise ValueError(f"Drive upload incomplete: {name}")
    archive = destination / "cpu_bundle.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
        for path in sorted((destination / "cpu").iterdir()):
            output.write(path, "cpu/" + path.name)
    atomic_json(
        root / "exports/latest.json",
        {
            "archive": archive.relative_to(root).as_posix(),
            "sha256": file_hash(archive),
            "run_id": run["run_id"],
            "enable_layout_adapter": report["enable_layout_adapter"],
        },
    )
    print(f"CPU bundle ready: {archive}", flush=True)


if __name__ == "__main__":
    main()
