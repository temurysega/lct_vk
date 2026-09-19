"""Publish complete Trainer checkpoints to Drive and restore verified copies."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

REQUIRED = {
    "adapter_model.safetensors",
    "adapter_config.json",
    "optimizer.pt",
    "scheduler.pt",
    "rng_state.pth",
    "trainer_state.json",
    "training_args.bin",
}
MARKER = "COMPLETE.json"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".writing-" + uuid4().hex)
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def validate_checkpoint(directory: Path) -> dict:
    """Partial/corrupt copies are never accepted based on directory name alone."""
    directory = directory.resolve()
    manifest = json.loads((directory / MARKER).read_text(encoding="utf-8"))
    if manifest.get("version") != 1 or manifest.get("status") != "complete":
        raise ValueError("Unsupported or incomplete checkpoint")
    inventory = manifest["files"]
    if not REQUIRED <= set(inventory):
        raise ValueError("Checkpoint does not contain full optimizer/RNG state")
    actual = {
        p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()
    }
    if actual != set(inventory) | {MARKER}:
        raise ValueError("Checkpoint file inventory changed")
    for name, expected in inventory.items():
        path = (directory / name).resolve()
        if (
            not path.is_relative_to(directory)
            or not path.is_file()
            or path.is_symlink()
        ):
            raise ValueError(f"Invalid checkpoint path: {name}")
        if (
            path.stat().st_size != expected["bytes"]
            or file_hash(path) != expected["sha256"]
        ):
            raise ValueError(f"Checkpoint checksum mismatch: {name}")
    state = json.loads((directory / "trainer_state.json").read_text(encoding="utf-8"))
    if state["global_step"] != manifest["global_step"]:
        raise ValueError("Checkpoint step does not match Trainer state")
    return manifest


def complete_checkpoints(run_dir: Path) -> list[tuple[Path, dict]]:
    result = []
    for directory in (run_dir / "checkpoints").glob("checkpoint-*"):
        try:
            result.append((directory, validate_checkpoint(directory)))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(
        result, key=lambda item: (item[1]["global_step"], item[0].name), reverse=True
    )


def publish_checkpoint(
    source: Path, run_dir: Path, resume_spec: dict, keep: int = 2
) -> Path:
    if keep < 2:
        raise ValueError("Keep at least two recovery points")
    if not all((source / name).is_file() for name in REQUIRED):
        raise ValueError("Trainer checkpoint is missing full training state")
    state = json.loads((source / "trainer_state.json").read_text(encoding="utf-8"))
    parent = (run_dir / "checkpoints").resolve()
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / f"checkpoint-{state['global_step']:08d}-{uuid4().hex[:8]}"
    # Immutable destination: a broken upload never replaces the preceding recovery point.
    shutil.copytree(source, destination)
    inventory = {
        p.relative_to(destination).as_posix(): {
            "bytes": p.stat().st_size,
            "sha256": file_hash(p),
        }
        for p in destination.rglob("*")
        if p.is_file()
    }
    manifest = {
        "version": 1,
        "status": "complete",
        "global_step": state["global_step"],
        "resume_spec": resume_spec,
        "files": inventory,
    }
    # Compare to local files before publishing. The last-written marker is the commit point.
    for name, data in inventory.items():
        if file_hash(source / name) != data["sha256"]:
            raise ValueError("Drive copy differs from local checkpoint")
    atomic_json(destination / MARKER, manifest)
    validate_checkpoint(destination)
    atomic_json(
        run_dir / "resume_latest.json",
        {
            "checkpoint": destination.relative_to(run_dir.resolve()).as_posix(),
            "global_step": state["global_step"],
        },
    )
    # Remove only our older, complete snapshots after a replacement has been verified.
    for old, _ in complete_checkpoints(run_dir)[keep:]:
        resolved = old.resolve()
        if resolved.parent != parent or not re.fullmatch(
            r"checkpoint-\d+-[0-9a-f]{8}", resolved.name
        ):
            raise ValueError(
                "Refusing to remove a checkpoint outside the run directory"
            )
        shutil.rmtree(resolved)
    return destination


def restore_checkpoint(source: Path, local_run: Path, expected_spec: dict) -> Path:
    manifest = validate_checkpoint(source)
    if manifest["resume_spec"] != expected_spec:
        raise ValueError(
            "Training data/configuration changed; use the original settings or resume=none"
        )
    destination = local_run / ("resume-" + uuid4().hex)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    validate_checkpoint(destination)
    return destination


def find_run(root: Path, resume: str) -> tuple[Path, dict] | None:
    if resume == "none":
        return None
    runs = (root / "runs").resolve()
    if resume == "auto":
        paths = sorted(runs.glob("*/run.json"), reverse=True)
    else:
        candidate = (runs / resume).resolve()
        if candidate.parent != runs:
            raise ValueError("Invalid run ID")
        paths = [candidate / "run.json"]
    for path in paths:
        # Atomic run.json writes leave previous metadata intact if interrupted.
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("checkpoint_version") != 1:
            if resume != "auto":
                raise ValueError("This legacy run has no resumable checkpoints")
            continue
        return path.parent, data
    if resume != "auto":
        raise ValueError("Run not found")
    return None


def drive_callback(run_dir: Path, resume_spec: dict, keep: int = 2):
    """Use the same full-state publication in training and the CPU recovery check."""
    from transformers import TrainerCallback

    class DriveCheckpoint(TrainerCallback):
        def __init__(self):
            self.last_saved_step = None

        def on_step_end(self, args, state, control, **kwargs):
            # Establish the first recovery point before the periodic interval.
            if state.global_step == 1:
                control.should_save = True

        def on_epoch_end(self, args, state, control, **kwargs):
            if state.global_step != self.last_saved_step:
                control.should_save = True

        def on_save(self, args, state, control, **kwargs):
            if state.global_step == self.last_saved_step:
                return
            source = Path(args.output_dir) / f"checkpoint-{state.global_step}"
            destination = publish_checkpoint(source, run_dir, resume_spec, keep)
            self.last_saved_step = state.global_step
            print(f"Drive checkpoint verified: {destination}", flush=True)

    return DriveCheckpoint()
