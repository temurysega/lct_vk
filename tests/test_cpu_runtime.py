import ast
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from deploy.cpu.verify_bundle import verify
from slide_agent.config import InferenceSettings
from slide_agent.layout_selector import select_with_adapter
from slide_agent.llm import InferenceClient, InferenceError
from slide_agent.planner import plan_deck
from training.build_cpu_notebook import notebook
from training.checkpoints import file_hash
from training.export_cpu import validate_adapter

ROOT = Path(__file__).resolve().parents[1]


def test_cpu_transport_counts_context_and_isolates_adapter(monkeypatch):
    calls = []

    def respond(request, **kwargs):
        payload = json.loads(request.data)
        calls.append((request.full_url, payload))
        if request.full_url.endswith("/apply-template"):
            assert payload["messages"][1]["content"] == "complete source"
            body = {"prompt": "formatted source"}
        elif request.full_url.endswith("/tokenize"):
            assert payload["content"] == "formatted source"
            body = {"tokens": list(range(300))}
        else:
            body = {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"ok":true}'}}
                ]
            }
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr("urllib.request.urlopen", respond)
    settings = InferenceSettings(
        "http://local/v1",
        "",
        "lct-cpu",
        backend="llamacpp",
        context_tokens=512,
        max_output_tokens=2048,
        lora_id=0,
    )
    client = InferenceClient(settings)
    assert client.chat_json(system="system", user="complete source") == {"ok": True}
    request = calls[-1][1]
    assert request["max_tokens"] == 180
    assert request["lora"] == [{"id": 0, "scale": 0.0}]
    InferenceClient(replace(settings, lora_scale=1)).chat(
        system="s", user="complete source"
    )
    assert calls[-1][1]["lora"] == [{"id": 0, "scale": 1}]
    assert client.settings.lora_scale == 0
    calls.clear()
    with pytest.raises(InferenceError, match="context exceeded"):
        InferenceClient(replace(settings, context_tokens=350)).chat(
            system="s", user="complete source"
        )
    assert len(calls) == 2  # No completion request with an oversized source.


def test_incomplete_model_output_is_rejected(monkeypatch):
    body = {
        "choices": [
            {"finish_reason": "length", "message": {"content": '{"slides": []}'}}
        ]
    }
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **kw: io.BytesIO(json.dumps(body).encode())
    )
    with pytest.raises(InferenceError, match="incomplete"):
        InferenceClient(InferenceSettings("http://local/v1", "", "base")).chat(
            system="s", user="u"
        )


def test_cpu_layout_uses_base_alias_with_per_request_lora(monkeypatch):
    monkeypatch.setenv("INFERENCE_LAYOUT_MODEL", "lct-layout")
    seen = []

    def choose(self, **kwargs):
        seen.append(self.settings)
        return {"choice": "candidate-1"}

    monkeypatch.setattr(InferenceClient, "chat_json", choose)
    client = InferenceClient(
        InferenceSettings(
            "http://local/v1", "", "lct-cpu", backend="llamacpp", lora_id=0
        )
    )
    picked, metadata = select_with_adapter(
        {"title": "Report"}, {}, [(10, {"id": "one"}), (9, {"id": "two"})], client
    )
    assert picked in {"one", "two"} and metadata["mode"] == "adapter"
    assert seen[0].model == "lct-cpu" and seen[0].lora_scale == 1
    assert client.settings.lora_scale == 0


def test_cpu_planner_keeps_source_without_template_payload(monkeypatch):
    source = "Проект. Создано 120 презентаций. Следующий этап — пилот."
    seen = []

    def plan(self, **kwargs):
        seen.append(kwargs)
        return {
            "slides": [
                {"title": title, "role": role, "bullets": []}
                for title, role in [
                    ("Проект", "cover"),
                    ("120 презентаций", "content"),
                    ("Пилот", "closing"),
                ]
            ]
        }

    monkeypatch.setattr(InferenceClient, "chat_json", plan)
    result = plan_deck(
        source,
        pattern_catalog={},
        design_system={},
        slide_count=3,
        client=InferenceClient(
            InferenceSettings("http://local/v1", "", "lct-cpu", backend="llamacpp")
        ),
    )
    assert result["planner"]["mode"] == "inference"
    assert json.loads(seen[0]["user"]) == {"source": source, "requested_slide_count": 3}
    assert seen[0]["max_tokens"] == 2048


def test_cpu_notebook_matches_generator_and_parses():
    saved = json.loads(
        (ROOT / "examples/BrandDeck_CPU_Training_Export.ipynb").read_text(
            encoding="utf-8"
        )
    )
    assert saved == notebook()
    for cell in saved["cells"]:
        if cell["cell_type"] == "code":
            ast.parse("".join(cell["source"]))
            assert cell["outputs"] == [] and cell["execution_count"] is None


def test_export_rejects_old_7b_adapter(tmp_path):
    (tmp_path / "training_run.json").write_text(
        json.dumps({"status": "completed", "base_model": "Qwen/Qwen2.5-7B-Instruct"})
    )
    (tmp_path / "adapter_config.json").write_text("{}")
    with pytest.raises(ValueError, match="7B adapter is incompatible"):
        validate_adapter(tmp_path, {"base_model": "Qwen/Qwen2.5-1.5B-Instruct"})


def test_vps_bundle_detects_corrupt_weights(tmp_path):
    profile_path = ROOT / "training/cpu_profile.json"
    profile = json.loads(profile_path.read_text())
    inventory = {}
    for name in (
        "base-q4_k_m.gguf",
        "layout-lora-f16.gguf",
        "deployment.env",
        "cpu_report.json",
    ):
        path = tmp_path / name
        path.write_bytes(b"fixture")
        inventory[name] = {"bytes": path.stat().st_size, "sha256": file_hash(path)}
    bundle = dict(profile, status="complete", run_id="test", files=inventory)
    (tmp_path / "bundle.json").write_text(json.dumps(bundle))
    assert verify(tmp_path, profile_path)["run_id"] == "test"
    (tmp_path / "base-q4_k_m.gguf").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Corrupted bundle file"):
        verify(tmp_path, profile_path)
