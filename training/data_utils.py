"""CPU-testable validation and completion-only tokenization for LoRA training."""

import json
from pathlib import Path


def load_records(path: Path) -> list[dict]:
    records = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not records:
        raise ValueError(f"Empty dataset: {path}")
    for row in records:
        messages = row["messages"]
        if [m["role"] for m in messages] != ["system", "user", "assistant"]:
            raise ValueError("Expected system, user, assistant messages")
        payload = json.loads(messages[1]["content"])
        labels = [c["label"] for c in payload["candidates"]]
        answer = json.loads(messages[2]["content"])
        if len(labels) != len(set(labels)) or answer.get("choice") not in labels:
            raise ValueError("Target must name exactly one available candidate")
    return records


def check_split(train: list[dict], validation: list[dict], test=None) -> None:
    """Check all pairs; a partially populated family identifier fails closed."""
    parts = {"train": train, "validation": validation}
    if test is not None:
        parts["test"] = test
    fields = ["group", "template_sha256"]
    if any("design_family" in r["provenance"] for rows in parts.values() for r in rows):
        fields.append("design_family")
    for field in fields:
        seen = set()
        for name, rows in parts.items():
            values = {r["provenance"].get(field) for r in rows}
            if None in values or "" in values:
                raise ValueError(f"Missing {field} in {name}")
            if seen & values:
                raise ValueError(f"Split leakage in {field}: {name}")
            seen.update(values)


def encode_record(tokenizer, row: dict, max_length: int) -> dict:
    messages = row["messages"]
    prefix = tokenizer.apply_chat_template(
        messages[:-1], tokenize=True, add_generation_prompt=True
    )
    full = tokenizer.apply_chat_template(
        messages, tokenize=True, add_generation_prompt=False
    )
    if full[: len(prefix)] != prefix:
        raise ValueError("Chat template changed between prompt and completion")
    if len(full) > max_length:
        raise ValueError(
            f"Example needs {len(full)} tokens; max_length={max_length}. No silent truncation."
        )
    if len(full) <= len(prefix):
        raise ValueError("Training completion is empty")
    return {
        "input_ids": full,
        "attention_mask": [1] * len(full),
        "labels": [-100] * len(prefix) + full[len(prefix) :],
    }
