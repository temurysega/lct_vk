from __future__ import annotations

import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from slide_agent.utils import resolve_workspace

ACCOUNT = {
    "username": "Designer",
    "password": "a-long-test-password",
    "position": "Дизайнер",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BRANDDECK_WORKSPACE", str(tmp_path))
    with TestClient(app) as value:
        yield value


def test_account_session_login_logout_and_hashes(client, tmp_path):
    assert client.get("/v1/templates").status_code == 401
    response = client.post("/api/auth/register", json=ACCOUNT)
    assert response.status_code == 201
    assert set(response.json()) == {"id", "username", "position"}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    token = client.cookies.get("predel_session")
    assert client.get("/api/auth/me").json() == response.json()
    assert client.get("/v1/templates").json() == []
    assert resolve_workspace() == tmp_path  # Request-local context must be reset.
    with sqlite3.connect(tmp_path / "accounts.sqlite3") as db:
        salt, password_hash = db.execute(
            "SELECT salt, password_hash FROM users"
        ).fetchone()
        assert salt and password_hash != ACCOUNT["password"]
        assert (
            db.execute("SELECT token_hash FROM sessions").fetchone()[0]
            == hashlib.sha256(token.encode()).hexdigest()
        )
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set("predel_session", token)
    assert client.get("/v1/templates").status_code == 401  # Revocation is server-side.
    client.cookies.clear()
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "designer", "password": "incorrect-password"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "designer", "password": ACCOUNT["password"]},
        ).status_code
        == 200
    )
    assert client.get("/v1/templates").status_code == 200


@pytest.mark.parametrize(
    "update",
    [
        {"position": " "},
        {"username": "ab"},
        {"username": "bad name"},
        {"password": "short"},
    ],
)
def test_registration_validation(client, update):
    assert (
        client.post("/api/auth/register", json={**ACCOUNT, **update}).status_code == 422
    )


def test_duplicate_names_and_expired_sessions(client, tmp_path):
    assert client.post("/api/auth/register", json=ACCOUNT).status_code == 201
    assert (
        client.post(
            "/api/auth/register", json={**ACCOUNT, "username": "designer"}
        ).status_code
        == 409
    )
    with sqlite3.connect(tmp_path / "accounts.sqlite3") as db:
        db.execute("UPDATE sessions SET expires=0")
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/v1/batches").status_code == 401


def test_cross_origin_requests_rejected(client):
    assert (
        client.post(
            "/api/auth/register",
            json=ACCOUNT,
            headers={"Origin": "https://unrelated.example"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/auth/register", json=ACCOUNT, headers={"Origin": "http://testserver"}
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/auth/logout", headers={"Origin": "https://unrelated.example"}
        ).status_code
        == 403
    )
    assert client.get("/api/auth/me").status_code == 200


def test_login_rate_limit(client):
    body = {"username": "unknown", "password": "incorrect-password"}
    for _ in range(20):
        assert client.post("/api/auth/login", json=body).status_code == 401
    response = client.post("/api/auth/login", json=body)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "600"


def test_web_text_cannot_read_server_file(client, tmp_path, monkeypatch):
    user = client.post("/api/auth/register", json=ACCOUNT).json()
    workspace = tmp_path / "users" / user["id"]
    template = workspace / "templates" / "demo"
    template.mkdir(parents=True)
    (template / "manifest.json").write_text("{}")
    secret = tmp_path / "private.md"
    secret.write_text("PRIVATE CONTENT")
    received = []

    def capture(_job, **kwargs):
        received.append(kwargs["content"].read_text())

    monkeypatch.setattr("backend.main.run_generation_job", capture)
    response = client.post(
        "/v1/presentations/jobs",
        data={"template_id": "demo", "content": str(secret), "offline": "true"},
    )
    assert response.status_code == 202
    assert received == [str(secret)]


def test_invalid_templates_and_bounds(client):
    client.post("/api/auth/register", json=ACCOUNT)
    assert (
        client.post(
            "/v1/templates/analyze",
            files={"file": ("bad.pptx", b"not a presentation")},
            data={"offline": "true"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/v1/templates/analyze", files={"file": ("empty.pptx", b"")}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/v1/presentations/jobs",
            data={"template_id": "missing", "content": "Text", "slide_count": 101},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/presentations/generate",
            data={"template_id": "missing", "content": "Text", "slide_count": 2},
        ).status_code
        == 422
    )
