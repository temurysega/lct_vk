from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .service import generate_deck
from .utils import read_json, resolve_workspace, write_json

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def job_directory(job_id: str, workspace: str | Path | None = None) -> Path:
    root = (resolve_workspace(workspace) / "jobs").resolve()
    directory = (root / job_id).resolve()
    if directory.parent != root:
        raise ValueError("Invalid job id")
    return directory


def create_job(
    *,
    template_id: str,
    slide_count: int | None,
    offline: bool,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    directory = job_directory(job_id, workspace)
    directory.mkdir(parents=True, exist_ok=False)
    record = {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "template_id": template_id,
        "slide_count": slide_count,
        "offline": offline,
        "created_at": _now(),
        "updated_at": _now(),
    }
    write_json(directory / "job.json", record)
    return record


def update_job(
    job_id: str,
    updates: dict[str, Any],
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    directory = job_directory(job_id, workspace)
    with _LOCK:
        record = read_json(directory / "job.json")
        record.update(updates)
        record["updated_at"] = _now()
        write_json(directory / "job.json", record)
    return record


def get_job(job_id: str, workspace: str | Path | None = None) -> dict[str, Any]:
    path = job_directory(job_id, workspace) / "job.json"
    if not path.is_file():
        raise FileNotFoundError(f"Job not found: {job_id}")
    return read_json(path)


def run_generation_job(
    job_id: str,
    *,
    template_id: str,
    content: str | Path,
    slide_count: int | None,
    offline: bool,
    workspace: str | Path | None = None,
) -> None:
    try:
        update_job(
            job_id,
            {"status": "running", "stage": "starting", "progress": 2},
            workspace,
        )

        def progress(stage: str, percent: int) -> None:
            update_job(
                job_id,
                {"status": "running", "stage": stage, "progress": percent},
                workspace,
            )

        result = generate_deck(
            template=template_id,
            content=content,
            workspace=workspace,
            slide_count=slide_count,
            offline=offline,
            progress=progress,
        )
        update_job(
            job_id,
            {
                "status": "completed",
                "stage": "completed",
                "progress": 100,
                "presentation_id": result["presentation_id"],
                "output": result["output"],
                "qa": {
                    "status": result["qa"]["status"],
                    "score": result["qa"]["score"],
                },
            },
            workspace,
        )
    except Exception as exc:  # noqa: BLE001 - persisted jobs must expose terminal state
        update_job(
            job_id,
            {
                "status": "failed",
                "stage": "failed",
                "error": str(exc),
            },
            workspace,
        )
