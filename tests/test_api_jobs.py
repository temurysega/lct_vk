from pathlib import Path

from fastapi.testclient import TestClient

from examples.create_demo_assets import create_template
from slide_agent.analyzer import analyze_template
from slide_agent.api import app
from slide_agent.utils import read_json


def test_async_job_accepts_document_upload(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("BRANDDECK_WORKSPACE", str(workspace))
    template = tmp_path / "brand.pptx"
    create_template(template, "clean-blue")
    analyzed = analyze_template(template, workspace=workspace)
    template_id = read_json(analyzed / "manifest.json")["template_id"]

    content = """# BrandDeck
## Проблема
- Подготовка презентаций занимает часы.
## Решение
- Анализировать шаблон и автоматически выбирать макеты.
## Результат
- Редактируемая презентация создаётся за минуты.
"""
    with TestClient(app) as client:
        response = client.post(
            "/v1/presentations/jobs",
            data={
                "template_id": template_id,
                "slide_count": "5",
                "offline": "true",
            },
            files={"content_file": ("brief.md", content, "text/markdown")},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        status = client.get(f"/v1/jobs/{job_id}")
        assert status.status_code == 200
        job = status.json()
        assert job["status"] == "completed"
        assert job["progress"] == 100
        assert job["qa"]["status"] in {"passed", "warning"}
        download = client.get(f"/v1/jobs/{job_id}/download")
        assert download.status_code == 200
        assert download.content.startswith(b"PK")
