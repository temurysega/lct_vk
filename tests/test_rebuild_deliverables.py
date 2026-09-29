import hashlib
import json
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from examples import rebuild_deliverables as rebuild


def _template(folder, name, data):
    path = folder / name
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def test_templates_must_be_exactly_the_organisers_files(tmp_path, monkeypatch):
    expected = {
        "a.pptx": _template(tmp_path, "a.pptx", b"first"),
        "b.pptx": hashlib.sha256(b"second").hexdigest(),
        "c.pptx": hashlib.sha256(b"third").hexdigest(),
    }
    _template(tmp_path, "b.pptx", b"edited")
    _template(tmp_path, "extra.pptx", b"another template")
    monkeypatch.setattr(rebuild, "EXPECTED_TEMPLATES", expected)

    errors = rebuild.check_templates(tmp_path)

    assert errors == [
        "Template differs from the organisers' file (SHA-256): b.pptx",
        "Template missing: c.pptx",
        "Unexpected PPTX in the template folder: extra.pptx",
    ]
    assert rebuild.check_templates(tmp_path / "missing") == [
        f"Template folder not found: {tmp_path / 'missing'}"
    ]


def test_run_needs_committed_code_and_inputs(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / "code").mkdir()
    (tmp_path / "code" / "module.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    assert rebuild.check_committed(tmp_path, ["code", "config.json"]) == []

    (tmp_path / "config.json").write_text('{"workspace": "other"}', encoding="utf-8")
    (tmp_path / "code" / "new.py").write_text("", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not an input", encoding="utf-8")

    assert rebuild.check_committed(tmp_path, ["code", "config.json"]) == [
        "Uncommitted change, commit or revert it first: code/new.py",
        "Uncommitted change, commit or revert it first: config.json",
    ]


def test_old_crlf_checkout_is_rewritten_with_lf(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("config", "core.autocrlf", "false")
    (tmp_path / ".gitattributes").write_text("code/*.py text eol=lf\n", encoding="utf-8")
    (tmp_path / "code").mkdir()
    (tmp_path / "code" / "module.py").write_bytes(b"x = 1\ny = 2\n")
    (tmp_path / "code" / "other.py").write_bytes(b"z = 3\n")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    # A checkout made before .gitattributes existed, with autocrlf on Windows,
    # and a real local edit that must survive.
    (tmp_path / "code" / "module.py").write_bytes(b"x = 1\r\ny = 2\r\n")
    (tmp_path / "code" / "other.py").write_bytes(b"z = 4\r\n")

    assert rebuild.normalize_line_endings(tmp_path, ["code"]) == ["code/module.py"]
    assert (tmp_path / "code" / "module.py").read_bytes() == b"x = 1\ny = 2\n"
    assert (tmp_path / "code" / "other.py").read_bytes() == b"z = 4\r\n"
    assert rebuild.check_committed(tmp_path, ["code"]) == [
        "Uncommitted change, commit or revert it first: code/other.py"
    ]
    assert rebuild.normalize_line_endings(tmp_path, ["code"]) == []


@pytest.fixture
def model_server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            body = json.dumps({"data": [{"id": "qwen3.5-9b"}]}).encode()
            self.send_response(200 if self.path == "/v1/models" else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", seen
    httpd.shutdown()


def test_model_server_is_checked_before_the_run(model_server):
    base, seen = model_server

    assert rebuild.check_model(f"{base}/v1", "secret") == []
    assert rebuild.check_model(f"{base}/v1/chat/completions", None) == []
    assert seen == [("/v1/models", "Bearer secret"), ("/v1/models", None)]
    [error] = rebuild.check_model(f"{base}/other", None)
    assert error.startswith(f"Model server is not reachable at {base}/other/models")
    assert rebuild.check_model("", None) == [
        "INFERENCE_BASE_URL is not set in the config or the environment"
    ]


def test_previous_attempt_is_moved_aside(tmp_path):
    workspace = tmp_path / "final"
    assert rebuild.move_aside(workspace) is None
    workspace.mkdir()
    (workspace / "dataset_report.json").write_text("{}", encoding="utf-8")

    moved = rebuild.move_aside(
        workspace, now=datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    )

    assert moved == tmp_path / "final-before-20260929T120000Z"
    assert (moved / "dataset_report.json").is_file()
    assert not workspace.exists()
