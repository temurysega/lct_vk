from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from zipfile import BadZipFile

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from lxml.etree import XMLSyntaxError
from pydantic import BaseModel, Field

import frontend
from backend.auth import install_auth
from slide_agent import __version__
from slide_agent.analyzer import analyze_template
from slide_agent.config import InferenceSettings
from slide_agent.imagegen import ImageSettings
from slide_agent.jobs import create_job, get_job, job_directory, run_generation_job
from slide_agent.service import (
    configured_client,
    generate_deck,
    list_templates,
    repair_presentation,
)
from slide_agent.utils import read_json, resolve_workspace

WEB_ROOT = Path(frontend.__file__).parent / "dist"

app = FastAPI(
    title="predel",
    version=__version__,
    description="Template-adaptive PowerPoint analysis and generation API",
)
app.mount(
    "/assets",
    StaticFiles(directory=WEB_ROOT / "assets", check_dir=False),
    name="assets",
)
install_auth(app)


@app.get("/", include_in_schema=False)
def homepage() -> FileResponse:
    entry = WEB_ROOT / "index.html"
    if not entry.is_file():
        raise HTTPException(
            status_code=503,
            detail="Frontend is not built. Run: npm ci --prefix frontend && npm run build --prefix frontend",
        )
    return FileResponse(entry, headers={"Cache-Control": "no-cache"})


@app.get("/studio", include_in_schema=False)
def studio() -> FileResponse:
    return homepage()


def _presentation_dir(presentation_id: str) -> Path:
    root = (resolve_workspace() / "presentations").resolve()
    directory = (root / presentation_id).resolve()
    if directory.parent != root:
        raise HTTPException(status_code=400, detail="Invalid presentation id")
    if not (directory / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="Presentation not found")
    return directory


def _save_content_upload(file: UploadFile, directory: Path) -> Path:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded document has no filename")
    if Path(file.filename).suffix.lower() not in {
        ".md",
        ".txt",
        ".csv",
        ".json",
        ".pptx",
        ".pdf",
        ".docx",
        ".xlsx",
    }:
        raise HTTPException(400, "Неподдерживаемый формат материалов.")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / Path(file.filename).name
    _copy_upload(file, target, 50)
    return target


def _copy_upload(file: UploadFile, target: Path, max_mb: int) -> None:
    total = 0
    try:
        with target.open("wb") as stream:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > max_mb * 1024 * 1024:
                    raise HTTPException(413, f"Файл превышает лимит {max_mb} МБ.")
                stream.write(chunk)
        if not total:
            raise HTTPException(400, "Выбранный файл пуст.")
    except Exception:
        target.unlink(missing_ok=True)
        raise


IMAGE_UPLOAD_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_IMAGE_UPLOADS = 20


def _save_image_uploads(files: list[UploadFile] | None, directory: Path) -> list[Path]:
    saved: list[Path] = []
    uploads = [file for file in files or [] if file.filename]
    if len(uploads) > MAX_IMAGE_UPLOADS:
        raise HTTPException(
            400, f"Можно добавить не больше {MAX_IMAGE_UPLOADS} изображений."
        )
    if uploads:
        directory.mkdir(parents=True, exist_ok=True)
    for index, file in enumerate(uploads, 1):
        suffix = Path(file.filename).suffix.lower()
        if suffix not in IMAGE_UPLOAD_SUFFIXES:
            raise HTTPException(
                400, "Изображения принимаются в форматах PNG, JPG и WEBP."
            )
        # Keep the descriptive name for matching but never trust its path;
        # a colon on Windows would even address an alternate data stream.
        stem = re.sub(r"[^\w\- ]+", "_", Path(file.filename).stem)[:100] or "image"
        target = directory / f"{index:02d}-{stem}{suffix}"
        _copy_upload(file, target, 15)
        saved.append(target)
    return saved


def _save_inline_content(content: str, directory: Path) -> Path:
    # CLI accepts filesystem paths; web text must always remain literal input.
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "brief.md"
    target.write_text(content, encoding="utf-8")
    return target


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/config")
def client_config() -> dict:
    return {
        "model_configured": InferenceSettings.from_env().enabled,
        "image_generation_configured": ImageSettings.from_env().enabled,
    }


def _template_path(template_id: str) -> str:
    root = (resolve_workspace() / "templates").resolve()
    path = (root / template_id).resolve()
    if path.parent != root or not (path / "manifest.json").is_file():
        raise HTTPException(404, "Шаблон не найден. Добавьте свой PPTX или PDF.")
    return str(path)


@app.get("/v1/templates")
def templates() -> list[dict]:
    return list_templates()


@app.get("/v1/batches")
def batches_endpoint() -> list[dict]:
    root = resolve_workspace() / "batches"
    paths = sorted(
        root.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return [
        {
            "batch_id": batch["batch_id"],
            "status": batch["status"],
            "template_id": batch["variants"][0]["template_id"],
        }
        for batch in (read_json(p) for p in paths[:20])
    ]


@app.get("/v1/batches/{batch_id}")
def batch_endpoint(batch_id: str) -> dict:
    root = (resolve_workspace() / "batches").resolve()
    directory = (root / batch_id).resolve()
    if directory.parent != root:
        raise HTTPException(status_code=400, detail="Invalid batch id")
    if not (directory / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="Batch not found")
    return read_json(directory / "manifest.json")


@app.post("/v1/templates/analyze")
def analyze_endpoint(
    file: Annotated[UploadFile, File(description="PPTX or PDF template")],
    name: Annotated[str | None, Form()] = None,
    offline: Annotated[bool, Form()] = False,
) -> dict:
    if not file.filename or Path(file.filename).suffix.lower() not in {".pptx", ".pdf"}:
        raise HTTPException(
            status_code=400, detail="A .pptx or .pdf template is required"
        )
    temp_dir = Path(tempfile.mkdtemp(prefix="branddeck-upload-"))
    temp_file = temp_dir / Path(file.filename).name
    try:
        _copy_upload(file, temp_file, 100)
        output = analyze_template(
            temp_file,
            workspace=resolve_workspace(),
            name=name,
            client=configured_client(offline=offline),
        )
        manifest = read_json(output / "manifest.json")
        manifest["path"] = str(output.resolve())
        return manifest
    except (ValueError, BadZipFile, XMLSyntaxError, KeyError, SystemExit) as exc:
        raise HTTPException(
            400, "Не удалось прочитать шаблон. Проверьте файл PPTX или PDF."
        ) from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/v1/presentations/generate")
def generate_endpoint(
    template_id: Annotated[str, Form()],
    content: Annotated[str | None, Form(max_length=100000)] = None,
    content_file: Annotated[UploadFile | None, File()] = None,
    slide_count: Annotated[int | None, Form(ge=3, le=100)] = None,
    offline: Annotated[bool, Form()] = False,
    image_files: Annotated[list[UploadFile] | None, File()] = None,
    mode: Annotated[str, Form(pattern="^(auto|source|brief)$")] = "auto",
    purpose: Annotated[str | None, Form(max_length=40)] = None,
) -> dict:
    temp_dir: Path | None = None
    try:
        content_input: str | Path
        if content_file is not None:
            temp_dir = Path(tempfile.mkdtemp(prefix="branddeck-content-"))
            content_input = _save_content_upload(content_file, temp_dir)
        elif content and content.strip():
            temp_dir = Path(tempfile.mkdtemp(prefix="predel-content-"))
            content_input = _save_inline_content(content, temp_dir)
        else:
            raise HTTPException(
                status_code=400, detail="Provide content text or content_file"
            )
        images = _save_image_uploads(image_files, temp_dir / "images")
        return generate_deck(
            template=_template_path(template_id),
            content=content_input,
            slide_count=slide_count,
            offline=offline,
            images=images,
            mode=mode,
            purpose=purpose or None,
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
    content: Annotated[str | None, Form(max_length=100000)] = None,
    content_file: Annotated[UploadFile | None, File()] = None,
    slide_count: Annotated[int | None, Form(ge=3, le=100)] = None,
    offline: Annotated[bool, Form()] = False,
    variants: Annotated[bool, Form()] = False,
    export_all: Annotated[bool, Form()] = False,
    image_files: Annotated[list[UploadFile] | None, File()] = None,
    mode: Annotated[str, Form(pattern="^(auto|source|brief)$")] = "auto",
    purpose: Annotated[str | None, Form(max_length=40)] = None,
) -> dict:
    if content_file is None and not (content and content.strip()):
        raise HTTPException(
            status_code=400, detail="Provide content text or content_file"
        )
    if slide_count is not None and not 3 <= slide_count <= 100:
        raise HTTPException(
            status_code=400, detail="Slide count must be between 3 and 100"
        )
    template_path = _template_path(template_id)
    if len([file for file in image_files or [] if file.filename]) > MAX_IMAGE_UPLOADS:
        raise HTTPException(
            400, f"Можно добавить не больше {MAX_IMAGE_UPLOADS} изображений."
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
    else:
        content_input = _save_inline_content(
            content or "", job_directory(record["job_id"]) / "source"
        )
    images = _save_image_uploads(
        image_files, job_directory(record["job_id"]) / "images"
    )
    background_tasks.add_task(
        run_generation_job,
        record["job_id"],
        template_id=template_path,
        content=content_input,
        slide_count=slide_count,
        offline=offline,
        variants=variants,
        export_formats=("pdf", "html") if export_all else (),
        images=images,
        mode=mode,
        purpose=purpose or None,
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
def download_endpoint(
    presentation_id: str, format: Literal["pptx", "pdf", "html"] = "pptx"
) -> FileResponse:
    run_dir = _presentation_dir(presentation_id)
    output = (
        run_dir / "output.pptx"
        if format == "pptx"
        else run_dir / "exports" / f"output.{format}"
    )
    if not output.is_file():
        raise HTTPException(status_code=404, detail="Presentation not found")
    return FileResponse(
        output,
        media_type={
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "pdf": "application/pdf",
            "html": "text/html",
        }[format],
        filename=f"{presentation_id}.{format}",
    )


@app.get("/v1/presentations/{presentation_id}")
def presentation_endpoint(presentation_id: str) -> dict:
    return read_json(_presentation_dir(presentation_id) / "manifest.json")


@app.get("/v1/presentations/{presentation_id}/preview/{slide}")
def preview_endpoint(presentation_id: str, slide: int) -> FileResponse:
    directory = _presentation_dir(presentation_id)
    target = directory / "exports" / f"slide-{slide}.png"
    if slide < 1 or not target.is_file():
        raise HTTPException(status_code=404, detail="Slide preview not found")
    return FileResponse(target, media_type="image/png")


class RepairRequest(BaseModel):
    issue_ids: list[str] = Field(min_length=1, max_length=100)


@app.post("/v1/presentations/{presentation_id}/repair")
def repair_endpoint(presentation_id: str, body: RepairRequest) -> dict:
    _presentation_dir(presentation_id)
    try:
        return repair_presentation(presentation_id, body.issue_ids)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
