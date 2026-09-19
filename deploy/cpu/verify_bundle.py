"""Verify a downloaded CPU bundle before starting the VPS services (stdlib only)."""

import argparse
import hashlib
import json
from pathlib import Path


def verify(directory: Path, profile_path: Path) -> dict:
    directory = directory.resolve()
    bundle = json.loads((directory / "bundle.json").read_text(encoding="utf-8"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if bundle.get("status") != "complete":
        raise ValueError("CPU export is incomplete")
    for key in ("base_model", "base_revision", "llama_cpp_revision"):
        if bundle.get(key) != profile[key]:
            raise ValueError(f"Bundle does not match the runtime profile: {key}")
    required = {
        "base-q4_k_m.gguf",
        "layout-lora-f16.gguf",
        "deployment.env",
        "cpu_report.json",
    }
    if not required.issubset(bundle["files"]):
        raise ValueError("Missing mandatory CPU bundle files")
    for name, expected in bundle["files"].items():
        path = (directory / name).resolve()
        if path.parent != directory or not path.is_file():
            raise ValueError(f"Invalid bundle file: {name}")
        sha = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                sha.update(block)
        if (
            path.stat().st_size != expected["bytes"]
            or sha.hexdigest() != expected["sha256"]
        ):
            raise ValueError(f"Corrupted bundle file: {name}")
    return bundle


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=root / "models/cpu")
    args = parser.parse_args()
    result = verify(args.directory, root / "training/cpu_profile.json")
    print(f"CPU bundle verified: {result['run_id']}")
