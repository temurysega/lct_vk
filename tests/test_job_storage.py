"""Atomic job status survives concurrent polling on Windows."""

import threading
from concurrent.futures import ThreadPoolExecutor

from slide_agent import jobs, utils


def _sharing_violation(code: int = 5) -> PermissionError:
    error = PermissionError("The process cannot access the file")
    error.winerror = code
    return error


def test_job_polling_serializes_with_parallel_status_updates(tmp_path, monkeypatch):
    record = jobs.create_job(
        template_id="template-1", slide_count=10, offline=True, workspace=tmp_path
    )
    job_id = record["job_id"]
    reader_holds_file = threading.Event()
    release_reader = threading.Event()
    reader_thread: list[int] = []
    original_read = jobs.read_json
    original_replace = utils.os.replace
    replace_attempts: list[int] = []

    def held_read(path):
        value = original_read(path)
        if threading.get_ident() in reader_thread:
            reader_holds_file.set()
            assert release_reader.wait(5)
        return value

    def guarded_replace(source, target):
        replace_attempts.append(threading.get_ident())
        if reader_holds_file.is_set() and not release_reader.is_set():
            raise _sharing_violation()
        return original_replace(source, target)

    monkeypatch.setattr(jobs, "read_json", held_read)
    monkeypatch.setattr(utils.os, "replace", guarded_replace)

    def poll():
        reader_thread.append(threading.get_ident())
        return jobs.get_job(job_id, tmp_path)

    started = [threading.Event() for _ in range(3)]

    def update(index):
        started[index].set()
        return jobs.update_job(job_id, {f"step_{index}": index}, tmp_path)

    with ThreadPoolExecutor(max_workers=4) as executor:
        reader = executor.submit(poll)
        assert reader_holds_file.wait(5)
        writers = [executor.submit(update, index) for index in range(3)]
        try:
            assert all(event.wait(5) for event in started)
            # All writers have entered update_job but none may replace a JSON
            # file while the polling reader still holds it.
            assert not replace_attempts
        finally:
            release_reader.set()
        assert reader.result(timeout=5)["status"] == "queued"
        for writer in writers:
            writer.result(timeout=5)

    saved = jobs.get_job(job_id, tmp_path)
    assert all(saved[f"step_{index}"] == index for index in range(3))
    assert len(replace_attempts) == 3


def test_atomic_json_replace_retries_transient_windows_sharing_error(
    tmp_path, monkeypatch
):
    path = tmp_path / "job.json"
    utils.write_json(path, {"status": "queued"})
    original_replace = utils.os.replace
    attempts = 0
    pauses: list[float] = []

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise _sharing_violation(5 if attempts == 1 else 32)
        return original_replace(source, target)

    monkeypatch.setattr(utils.os, "replace", flaky_replace)
    monkeypatch.setattr(utils.time, "sleep", pauses.append)

    utils.write_json(path, {"status": "running", "progress": 50})

    assert attempts == 3
    assert pauses == [0.01, 0.02]
    assert utils.read_json(path) == {"status": "running", "progress": 50}
    assert not list(tmp_path.glob(".job.json.*"))
