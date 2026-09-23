"""Download the pinned local model and llama.cpp server, verified by SHA-256.

The weights (5.7 GB) are not kept in git: GitHub rejects files over 2 GB even
with LFS. Every file is pinned in ``qwen3.5-9b.json`` by URL, size and SHA-256,
so each machine gets exactly the tested model and server::

    python deploy/llama/setup_local.py               # model + CUDA server (Windows)
    python deploy/llama/setup_local.py --device cpu  # model + CPU server (Windows)
    python deploy/llama/setup_local.py --no-server   # model only (Linux, Docker)

Interrupted downloads resume. A file appears under its final name only after
its checksum matches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROFILE = Path(__file__).with_name("qwen3.5-9b.json")
CHUNK = 8 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _verified(target: Path, size: int, digest: str) -> bool:
    """Hash a present file once; a stamp with size and mtime skips rehashing."""
    if not target.is_file() or target.stat().st_size != size:
        return False
    stamp = target.with_name(target.name + ".sha256")
    state = {"sha256": digest, "bytes": size, "mtime_ns": target.stat().st_mtime_ns}
    try:
        if json.loads(stamp.read_text(encoding="utf-8")) == state:
            return True
    except (OSError, ValueError):
        pass
    if sha256(target) != digest:
        raise ValueError(f"{target} has another SHA-256; delete it and run again")
    stamp.write_text(json.dumps(state) + "\n", encoding="utf-8")
    return True


def fetch(url: str, target: Path, size: int, digest: str) -> Path:
    if _verified(target, size, digest):
        print(f"OK {target.name}", flush=True)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    if offset > size:
        part.unlink()
        offset = 0
    if offset < size:
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            if offset and response.status != 206:
                offset = 0  # the server ignored Range: start over
            done, reported = offset, -1
            with part.open("ab" if offset else "wb") as stream:
                while block := response.read(CHUNK):
                    stream.write(block)
                    done += len(block)
                    percent = done * 100 // size
                    if percent // 10 != reported:
                        reported = percent // 10
                        print(f"{target.name}: {percent}%", flush=True)
    if part.stat().st_size != size or sha256(part) != digest:
        part.unlink()
        raise ValueError(f"Checksum mismatch for {url}; the partial file was removed")
    part.replace(target)
    _verified(target, size, digest)
    print(f"OK {target.name}", flush=True)
    return target


def install_server(spec: dict, release: str, root: Path = ROOT) -> Path:
    """Unpack the pinned llama.cpp Windows release into ``spec['bin']``."""
    bin_dir = root / spec["bin"]
    marker = bin_dir.parent / "release.json"
    try:
        installed = json.loads(marker.read_text(encoding="utf-8")).get("tag")
    except (OSError, ValueError):
        installed = None
    if installed == release and (bin_dir / "llama-server.exe").is_file():
        print(f"OK llama.cpp {release} in {spec['bin']}", flush=True)
        return bin_dir
    bin_dir.mkdir(parents=True, exist_ok=True)
    for asset in spec["assets"]:
        archive = fetch(
            asset["url"],
            bin_dir.parent / Path(asset["url"]).name,
            asset["bytes"],
            asset["sha256"],
        )
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(bin_dir)
    if not (bin_dir / "llama-server.exe").is_file():
        raise ValueError(f"llama-server.exe is missing after unpacking into {bin_dir}")
    marker.write_text(
        json.dumps(
            {"tag": release, "assets": [a["url"] for a in spec["assets"]]}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    return bin_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("gpu", "cpu"), default="gpu")
    parser.add_argument(
        "--no-server", action="store_true", help="Download only the model weights"
    )
    args = parser.parse_args()
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    fetch(
        profile["download_url"],
        ROOT / profile["model_path"],
        profile["gguf_bytes"],
        profile["gguf_sha256"],
    )
    if args.no_server:
        return 0
    if sys.platform != "win32":
        print(
            "Server binaries are pinned for Windows; on Linux use compose.llm-cpu.yaml",
            flush=True,
        )
        return 0
    install_server(
        profile["llama_cpp_windows"][args.device], profile["llama_cpp_release"]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
