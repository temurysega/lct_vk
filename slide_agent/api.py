from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import __version__
from .analyzer import analyze_template
from .jobs import create_job, get_job, job_directory, run_generation_job
from .service import configured_client, generate_deck, list_templates
from .utils import resolve_workspace

app = FastAPI(
    title="BrandDeck AI",
    version=__version__,
    description="Template-adaptive PowerPoint analysis and generation API",
)


def _save_content_upload(file: UploadFile, directory: Path) -> Path:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded document has no filename")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / Path(file.filename).name
    with target.open("wb") as stream:
        shutil.copyfileobj(file.file, stream)
    if target.stat().st_size > 50 * 1024 * 1024:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="Document exceeds the 50 MB limit")
    return target


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/v1/templates")
def templates() -> list[dict]:
    return list_templates()


@app.post("/v1/templates/analyze")
def analyze_endpoint(
    file: Annotated[UploadFile, File(description="PPTX or PDF template")],
    name: Annotated[str | None, Form()] = None,
    offline: Annotated[bool, Form()] = False,
) -> dict:
    if not file.filename or Path(file.filename).suffix.lower() not in {".pptx", ".pdf"}:
        raise HTTPException(status_code=400, detail="A .pptx or .pdf template is required")
    temp_dir = Path(tempfile.mkdtemp(prefix="branddeck-upload-"))
    temp_file = temp_dir / Path(file.filename).name
    try:
        with temp_file.open("wb") as stream:
            shutil.copyfileobj(file.file, stream)
        if temp_file.stat().st_size > 100 * 1024 * 1024:
            raise HTTPException(
                status_code=413, detail="Template exceeds the 100 MB upload limit"
            )
        output = analyze_template(
            temp_file,
            workspace=resolve_workspace(),
            name=name,
            client=configured_client(offline=offline),
        )
        from .utils import read_json

        manifest = read_json(output / "manifest.json")
        manifest["path"] = str(output.resolve())
        return manifest
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/v1/presentations/generate")
def generate_endpoint(
    template_id: Annotated[str, Form()],
    content: Annotated[str | None, Form()] = None,
    content_file: Annotated[UploadFile | None, File()] = None,
    slide_count: Annotated[int | None, Form()] = None,
    offline: Annotated[bool, Form()] = False,
) -> dict:
    temp_dir: Path | None = None
    try:
        content_input: str | Path
        if content_file is not None:
            temp_dir = Path(tempfile.mkdtemp(prefix="branddeck-content-"))
            content_input = _save_content_upload(content_file, temp_dir)
        elif content and content.strip():
            content_input = content
        else:
            raise HTTPException(
                status_code=400, detail="Provide content text or content_file"
            )
        return generate_deck(
            template=template_id,
            content=content_input,
            slide_count=slide_count,
            offline=offline,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/v1/presentations/jobs", status_code=202)
def create_generation_job_endpoint(
    background_tasks: BackgroundTasks,
    template_id: Annotated[str, Form()],
    content: Annotated[str | None, Form()] = None,
    content_file: Annotated[UploadFile | None, File()] = None,
    slide_count: Annotated[int | None, Form()] = None,
    offline: Annotated[bool, Form()] = False,
) -> dict:
    if content_file is None and not (content and content.strip()):
        raise HTTPException(
            status_code=400, detail="Provide content text or content_file"
        )
    if slide_count is not None and not 3 <= slide_count <= 100:
        raise HTTPException(
            status_code=400, detail="Slide count must be between 3 and 100"
        )
    record = create_job(
        template_id=template_id,
        slide_count=slide_count,
        offline=offline,
    )
    content_input: str | Path = content or ""
    if content_file is not None:
        content_input = _save_content_upload(
            content_file,
            job_directory(record["job_id"]) / "source",
        )
    background_tasks.add_task(
        run_generation_job,
        record["job_id"],
        template_id=template_id,
        content=content_input,
        slide_count=slide_count,
        offline=offline,
    )
    return record


@app.get("/v1/jobs/{job_id}")
def job_status_endpoint(job_id: str) -> dict:
    try:
        return get_job(job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/v1/jobs/{job_id}/download")
def job_download_endpoint(job_id: str) -> FileResponse:
    try:
        job = get_job(job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail="Job is not completed")
    output = Path(str(job.get("output", ""))).resolve()
    presentations_root = (resolve_workspace() / "presentations").resolve()
    if presentations_root not in output.parents:
        raise HTTPException(status_code=400, detail="Invalid job output path")
    if not output.is_file():
        raise HTTPException(status_code=404, detail="Presentation output is missing")
    return FileResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{job_id}.pptx",
    )


@app.get("/v1/presentations/{presentation_id}/download")
def download_endpoint(presentation_id: str) -> FileResponse:
    root = (resolve_workspace() / "presentations").resolve()
    run_dir = (root / presentation_id).resolve()
    if run_dir.parent != root:
        raise HTTPException(status_code=400, detail="Invalid presentation id")
    output = run_dir / "output.pptx"
    if not output.is_file():
        raise HTTPException(status_code=404, detail="Presentation not found")
    return FileResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{presentation_id}.pptx",
    )
