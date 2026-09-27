from pathlib import Path

from fastapi.testclient import TestClient

from examples.create_demo_assets import create_template
from slide_agent.api import app


def test_async_job_accepts_document_upload(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("BRANDDECK_WORKSPACE", str(workspace))
    template = tmp_path / "brand.pptx"
    create_template(template, "clean-blue")

    content = """# BrandDeck
## Проблема
- Подготовка презентаций занимает часы.
## Решение
- Анализировать шаблон и автоматически выбирать макеты.
## Результат
- Редактируемая презентация создаётся за минуты.
"""
    with TestClient(app) as client:
        registration = client.post(
            "/api/auth/register",
            json={
                "username": "designer",
                "password": "test-password-123",
                "position": "Дизайнер",
            },
        )
        assert registration.status_code == 201
        with template.open("rb") as source:
            analyzed = client.post(
                "/v1/templates/analyze",
                files={"file": ("brand.pptx", source)},
                data={"offline": "true"},
            )
        assert analyzed.status_code == 200
        template_id = analyzed.json()["template_id"]
        jobs_dir = workspace / "users" / registration.json()["id"] / "jobs"
        for filename, payload in (("data.exe", b"bad"), ("empty.md", b"")):
            existing = set(jobs_dir.glob("*/job.json"))
            rejected = client.post(
                "/v1/presentations/jobs",
                data={"template_id": template_id, "offline": "true"},
                files={"content_file": (filename, payload)},
            )
            assert rejected.status_code == 400
            assert set(jobs_dir.glob("*/job.json")) == existing
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
        # Work persists under the account, including background job output.
        assert (
            workspace
            / "users"
            / registration.json()["id"]
            / "jobs"
            / job_id
            / "job.json"
        ).is_file()
        with TestClient(app) as stranger:
            assert (
                stranger.post(
                    "/api/auth/register",
                    json={
                        "username": "another",
                        "password": "test-password-123",
                        "position": "Менеджер",
                    },
                ).status_code
                == 201
            )
            assert stranger.get("/v1/templates").json() == []
            assert stranger.get(f"/v1/jobs/{job_id}").status_code == 404
            assert stranger.get(f"/v1/jobs/{job_id}/download").status_code == 404
            assert (
                stranger.get(
                    f"/v1/presentations/{job['presentation_id']}/download"
                ).status_code
                == 404
            )
            assert (
                stranger.post(
                    "/v1/presentations/jobs",
                    data={
                        "template_id": analyzed.json()["path"],
                        "content": content,
                        "offline": "true",
                    },
                ).status_code
                == 404
            )
