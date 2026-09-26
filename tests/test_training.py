from __future__ import annotations

import ast
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from slide_agent.config import InferenceSettings
from slide_agent.layout_selector import select_with_adapter, selection_payload
from slide_agent.llm import InferenceClient
from slide_agent.planner import _slide_requirements, assign_patterns
from training.build_notebook import ENVIRONMENT_SETUP, notebook
from training.data_utils import check_split, encode_record, load_records
from training.prepare_drive import make_record, split_records


def patterns():
    return [
        {
            "id": name,
            "layout_index": i,
            "master_index": 0,
            "roles": ["content"],
            "capacity": {
                "title_chars": 80,
                "body_chars": 500 + i * 50,
                "usable_body_zones": 1,
                "rhetorical_pattern": "content",
            },
        }
        for i, name in enumerate(("original-abc", "original-def", "original-ghi"))
    ]


def source_row(template="train", group="first"):
    sample = {"title": "Report", "role": "content", "bullets": ["A fact"], "body": ""}
    pool = patterns()
    return {
        "slide": sample,
        "requirements": _slide_requirements(sample),
        "positive": pool[0],
        "alternatives": pool[1:],
        "group": group,
        "source_slide": 1,
        "template": template,
        "template_sha256": template,
    }


def test_candidate_labels_are_temporary_and_inputs_bounded():
    row = source_row()
    row["slide"].update(title="x" * 1000, body="x" * 5000, bullets=["x" * 1000] * 30)
    payload, mapping = selection_payload(row["slide"], row["requirements"], patterns())
    assert len(payload["content"]["body"]) == 500
    assert len(payload["content"]["bullets"]) == 5
    assert "original-" not in json.dumps(payload)
    assert set(mapping.values()) == {p["id"] for p in patterns()}


def test_split_holds_out_entire_template_and_removes_shared_content():
    train, valid, dropped = split_records(
        [
            source_row(),
            source_row("heldout", "first"),
            source_row("heldout", "second"),
            source_row("heldout", "second"),
        ],
        "heldout",
    )
    assert len(train) == 3 and len(valid) == 1 and dropped == 2
    check_split(train, valid)
    with pytest.raises(ValueError, match="leakage"):
        check_split(train, train)


def test_dataset_rejects_answer_outside_candidates(tmp_path):
    row = make_record(source_row(), 0)
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(row), encoding="utf-8")
    assert len(load_records(path)) == 1
    row["messages"][-1]["content"] = '{"choice":"invented"}'
    path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="available candidate"):
        load_records(path)


def test_completion_only_labels_and_no_silent_truncation():
    class Tokenizer:
        def apply_chat_template(self, messages, tokenize, add_generation_prompt):
            text = "".join(f"<{m['role']}>{m['content']}<end>" for m in messages)
            if add_generation_prompt:
                text += "<assistant>"
            return [ord(c) for c in text]

    row = make_record(source_row(), 0)
    encoded = encode_record(Tokenizer(), row, 10000)
    answer_tokens = [n for n in encoded["labels"] if n != -100]
    assert (
        "".join(chr(n) for n in answer_tokens)
        == row["messages"][-1]["content"] + "<end>"
    )
    assert len(encoded["labels"]) == len(encoded["input_ids"])
    with pytest.raises(ValueError, match="No silent truncation"):
        encode_record(Tokenizer(), row, 4)


def test_adapter_choice_updates_real_layout_and_rejects_unknown(monkeypatch):
    monkeypatch.setenv("INFERENCE_LAYOUT_MODEL", "lct-layout")
    client = InferenceClient(InferenceSettings("http://localhost/v1", "test", "base"))
    calls = []

    def choose(self, **kwargs):
        calls.append(self.settings.model)
        payload = json.loads(kwargs["user"])
        labels = [c["label"] for c in payload["candidates"]]
        assert kwargs["schema"]["properties"]["choice"]["enum"] == labels
        # Select the candidate with the largest body capacity, independent of label order.
        target = max(payload["candidates"], key=lambda c: c["capacity"]["body_chars"])
        return {"choice": target["label"]}

    monkeypatch.setattr(InferenceClient, "chat_json", choose)
    plan = {"slides": [source_row()["slide"]]}
    result = assign_patterns(
        copy.deepcopy(plan), {"patterns": patterns()}, client=client
    )
    selected = result["slides"][0]
    assert selected["pattern_id"] == "original-ghi" and selected["layout_index"] == 2
    assert selected["pattern_selection"]["selector"]["mode"] == "adapter"
    assert calls == ["lct-layout"]
    offline = assign_patterns(copy.deepcopy(plan), {"patterns": patterns()})
    assert offline["slides"][0]["pattern_selection"]["selector"]["mode"] == "heuristic"
    assert calls == ["lct-layout"]
    monkeypatch.setattr(
        InferenceClient, "chat_json", lambda *a, **kw: {"choice": "../unknown"}
    )
    bad = assign_patterns(copy.deepcopy(plan), {"patterns": patterns()}, client=client)
    assert bad["slides"][0]["pattern_selection"]["selector"]["mode"] == "fallback"
    assert bad["slides"][0]["pattern_id"] == offline["slides"][0]["pattern_id"]


def test_bad_candidate_cannot_be_rescued_by_adapter(monkeypatch):
    monkeypatch.setenv("INFERENCE_LAYOUT_MODEL", "lct-layout")
    monkeypatch.setattr(
        InferenceClient,
        "chat_json",
        lambda *a, **kw: pytest.fail("Must not call model"),
    )
    pool = patterns()
    row = source_row()
    chosen, meta = select_with_adapter(
        row["slide"],
        row["requirements"],
        [(10, pool[0], [], []), (-40, pool[1], [], [])],
        InferenceClient(InferenceSettings("http://localhost/v1", "test", "base")),
    )
    assert chosen is None and meta["mode"] == "heuristic"


def test_drive_notebook_is_standalone_and_cells_parse():
    root = Path(__file__).resolve().parents[1]
    saved = json.loads(
        (root / "examples/BrandDeck_A100_Training_Drive.ipynb").read_text(
            encoding="utf-8"
        )
    )
    assert saved == notebook()
    for cell in saved["cells"]:
        if cell["cell_type"] == "code":
            source = "".join(cell["source"])
            ast.parse(source)
            assert "git clone" not in source
            assert not cell["outputs"] and cell["execution_count"] is None


def test_notebook_subprocess_error_displays_underlying_cause(capsys):
    namespace = {}
    exec(ENVIRONMENT_SETUP, namespace)  # noqa: S102 - execute our notebook helper
    with pytest.raises(subprocess.CalledProcessError):
        namespace["run_visible"](
            [
                sys.executable,
                "-c",
                "import sys; print('missing environment component', file=sys.stderr); sys.exit(1)",
            ]
        )
    assert "missing environment component" in capsys.readouterr().out
