"""Restore pinned, curated PPTX sources; never execute downloaded content."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--vk-templates", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    target = args.output.resolve()
    (target / "templates").mkdir(parents=True, exist_ok=True)
    for source in inventory["accepted"]:
        name = source["name"]
        if Path(name).name != name:
            raise ValueError("Expected a filename, not a path")
        path = target / "templates" / name
        if (
            path.exists()
            and hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
        ):
            continue
        temporary = path.with_suffix(".download")
        if source["source_url"].startswith("user-provided:"):
            shutil.copy2(args.vk_templates / name, temporary)
        else:
            subprocess.run(
                [
                    "curl",
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--retry",
                    "2",
                    "--max-time",
                    "180",
                    source["download_url"],
                    "--output",
                    str(temporary),
                ],
                check=True,
            )
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("Downloaded source differs from curated source: " + name)
        temporary.replace(path)
        print(name, flush=True)
    shutil.copy2(args.inventory, target / "curated_inventory.json")
    shutil.copytree(
        args.inventory.parent / "licenses", target / "evidence", dirs_exist_ok=True
    )
    print(target / "curated_inventory.json")


if __name__ == "__main__":
    main()
