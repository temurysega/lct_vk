from __future__ import annotations

import json

import pytest

from training.checkpoints import (
    MARKER,
    REQUIRED,
    atomic_json,
    complete_checkpoints,
    find_run,
    publish_checkpoint,
    restore_checkpoint,
    validate_checkpoint,
)


def trainer_snapshot(path, step):
    path.mkdir(parents=True)
    for name in REQUIRED:
        (path / name).write_bytes(f"saved training state at {step}".encode())
    atomic_json(path / "trainer_state.json", {"global_step": step})
    (path / "tokenizer.json").write_text("{}", encoding="utf-8")
    return path


def test_full_checkpoint_restores_and_rejects_changed_training_settings(tmp_path):
    source = trainer_snapshot(tmp_path / "local", 10)
    spec = {"revision": "pinned", "epochs": 2}
    saved = publish_checkpoint(source, tmp_path / "drive", spec)
    restored = restore_checkpoint(saved, tmp_path / "new-session", spec)
    assert validate_checkpoint(restored)["global_step"] == 10
    assert (restored / "optimizer.pt").read_bytes() == (
        source / "optimizer.pt"
    ).read_bytes()
    with pytest.raises(ValueError, match="configuration changed"):
        restore_checkpoint(saved, tmp_path / "new-session", dict(spec, epochs=3))


def test_incomplete_and_corrupt_latest_copies_fall_back_to_complete_checkpoint(
    tmp_path,
):
    run = tmp_path / "drive"
    previous = publish_checkpoint(trainer_snapshot(tmp_path / "a", 10), run, {})
    latest = publish_checkpoint(trainer_snapshot(tmp_path / "b", 20), run, {})
    trainer_snapshot(run / "checkpoints/checkpoint-30-partial", 30)
    (latest / "optimizer.pt").write_bytes(b"corrupt")
    assert [p for p, _ in complete_checkpoints(run)] == [previous]
    with pytest.raises(ValueError, match="checksum"):
        validate_checkpoint(latest)


def test_rotation_preserves_two_recovery_points_and_no_partial_publish(tmp_path):
    run = tmp_path / "drive"
    for step in (1, 10, 20):
        publish_checkpoint(trainer_snapshot(tmp_path / f"local-{step}", step), run, {})
    assert [m["global_step"] for _, m in complete_checkpoints(run)] == [20, 10]
    broken = trainer_snapshot(tmp_path / "missing-optimizer", 30)
    (broken / "optimizer.pt").unlink()
    with pytest.raises(ValueError, match="missing full training state"):
        publish_checkpoint(broken, run, {})
    assert len(complete_checkpoints(run)) == 2
    pointer = json.loads((run / "resume_latest.json").read_text())
    assert pointer["global_step"] == 20
    assert (run / pointer["checkpoint"] / MARKER).is_file()


def test_interrupted_drive_upload_never_replaces_good_checkpoint(tmp_path, monkeypatch):
    from training import checkpoints

    run = tmp_path / "drive"
    good = publish_checkpoint(trainer_snapshot(tmp_path / "first", 1), run, {})
    real_copy = checkpoints.shutil.copytree

    def fail_after_copy(source, destination):
        real_copy(source, destination)
        raise OSError("Drive disconnected during upload")

    monkeypatch.setattr(checkpoints.shutil, "copytree", fail_after_copy)
    with pytest.raises(OSError, match="disconnected"):
        publish_checkpoint(trainer_snapshot(tmp_path / "second", 10), run, {})
    assert [p for p, _ in complete_checkpoints(run)] == [good]


def test_resume_auto_and_explicit_run_selection(tmp_path):
    atomic_json(
        tmp_path / "runs/20260918/run.json",
        {"checkpoint_version": 1, "status": "failed"},
    )
    atomic_json(
        tmp_path / "runs/20260919/run.json",
        {"checkpoint_version": 1, "status": "completed"},
    )
    assert find_run(tmp_path, "auto")[0].name == "20260919"
    assert find_run(tmp_path, "20260918")[1]["status"] == "failed"
    assert find_run(tmp_path, "none") is None
    with pytest.raises(ValueError, match="Invalid run ID"):
        find_run(tmp_path, "../../outside")
