import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from deploy.llama.setup_local import PROFILE, fetch, install_server


@pytest.fixture
def server():
    files: dict[str, bytes] = {}
    ranges: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = files[self.path]
            ranges.append(self.headers.get("Range"))
            start = 0
            if self.headers.get("Range"):
                start = int(self.headers["Range"].split("=")[1].rstrip("-"))
            self.send_response(206 if start else 200)
            self.send_header("Content-Length", str(len(body) - start))
            self.end_headers()
            self.wfile.write(body[start:])

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", files, ranges
    httpd.shutdown()


def test_download_resumes_and_verifies(server, tmp_path):
    base, files, ranges = server
    data = bytes(range(256)) * 400
    files["/model.gguf"] = data
    target = tmp_path / "models/model.gguf"
    target.parent.mkdir()
    target.with_name("model.gguf.part").write_bytes(data[:1000])
    digest = hashlib.sha256(data).hexdigest()

    fetch(base + "/model.gguf", target, len(data), digest)
    assert target.read_bytes() == data and ranges == ["bytes=1000-"]
    fetch(base + "/model.gguf", target, len(data), digest)
    assert len(ranges) == 1  # verified file is not downloaded again


def test_wrong_checksum_leaves_nothing(server, tmp_path):
    base, files, _ = server
    files["/model.gguf"] = b"tampered"
    target = tmp_path / "model.gguf"
    with pytest.raises(ValueError, match="Checksum mismatch"):
        fetch(base + "/model.gguf", target, 8, "0" * 64)
    assert list(tmp_path.iterdir()) == []


def test_server_release_is_unpacked_once(server, tmp_path):
    base, files, ranges = server
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("llama-server.exe", b"exe")
        bundle.writestr("ggml.dll", b"dll")
    files["/llama.zip"] = archive.getvalue()
    spec = {
        "bin": "fixtures/llama/bin",
        "assets": [
            {
                "url": base + "/llama.zip",
                "bytes": len(archive.getvalue()),
                "sha256": hashlib.sha256(archive.getvalue()).hexdigest(),
            }
        ],
    }
    bin_dir = install_server(spec, "b1", root=tmp_path)
    assert (bin_dir / "llama-server.exe").read_bytes() == b"exe"
    install_server(spec, "b1", root=tmp_path)
    assert len(ranges) == 1


def test_profile_pins_every_download():
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    assets = [
        (profile["download_url"], profile["gguf_sha256"]),
        *(
            (asset["url"], asset["sha256"])
            for device in profile["llama_cpp_windows"].values()
            for asset in device["assets"]
        ),
    ]
    for url, digest in assets:
        assert url.startswith("https://") and len(digest) == 64
        assert profile["llama_cpp_release"] in url or profile["gguf_revision"] in url
