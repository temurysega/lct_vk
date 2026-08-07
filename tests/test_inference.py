import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from slide_agent.api import health
from slide_agent.config import InferenceSettings
from slide_agent.llm import InferenceClient


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        assert request["model"] == "test-model"
        response = {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}
        payload = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


def test_openai_compatible_client():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = InferenceClient(
            InferenceSettings(
                base_url=f"http://127.0.0.1:{server.server_port}/v1",
                api_key="test",
                model="test-model",
            )
        )
        assert client.chat_json(system="system", user="user") == {"ok": True}
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_api_health():
    assert health()["status"] == "ok"
