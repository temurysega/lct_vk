"""Train and evaluate a QLoRA layout selector on an A100. No GitHub login needed."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from data_utils import check_split, encode_record, load_records

MODEL = "Qwen/Qwen2.5-7B-Instruct"


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def evaluate_choices(model, tokenizer, rows: list[dict]) -> dict:
    import torch

    model.eval()
    results = []
    for row in rows:
        ids = tokenizer.apply_chat_template(
            row["messages"][:-1],
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
        ).to(model.device)
        with torch.inference_mode():
            generated = model.generate(
                input_ids=ids,
                attention_mask=torch.ones_like(ids),
                max_new_tokens=48,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )
        text = tokenizer.decode(
            generated[0, ids.shape[1] :], skip_special_tokens=True
        ).strip()
        expected = json.loads(row["messages"][-1]["content"])["choice"]
        labels = {
            c["label"] for c in json.loads(row["messages"][1]["content"])["candidates"]
        }
        try:
            parsed = json.loads(text)
            choice = parsed.get("choice") if isinstance(parsed, dict) else None
            valid = isinstance(choice, str) and choice in labels
        except (ValueError, TypeError):
            choice, valid = None, False
        results.append(
            {
                "group": row["provenance"]["group"],
                "expected": expected,
                "choice": choice,
                "valid": valid,
                "correct": valid and choice == expected,
                "raw": text,
            }
        )
    return {
        "examples": len(results),
        "valid_json_rate": sum(r["valid"] for r in results) / len(results),
        "weak_label_accuracy": sum(r["correct"] for r in results) / len(results),
        "predictions": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drive-root", type=Path, default=Path("/content/drive/MyDrive/lct")
    )
    parser.add_argument("--local-root", type=Path, default=Path("/content/lct-work"))
    parser.add_argument("--epochs", type=float, default=2)
    parser.add_argument("--max-length", type=int, default=3072)
    parser.add_argument(
        "--revision", required=True, help="Pinned Hugging Face model commit"
    )
    args = parser.parse_args()
    if args.epochs <= 0 or args.max_length < 256:
        parser.error("Invalid epochs or context length")

    # Verify the uploaded inputs before spending GPU time. Copy dataset reads off Drive.
    root = args.drive_root
    manifest = json.loads((root / "data/manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"Invalid package file: {relative}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Package checksum mismatch: {relative}")
    local_data = args.local_root / "data"
    shutil.copytree(root / "data", local_data, dirs_exist_ok=True)
    train = load_records(local_data / "train.jsonl")
    validation = load_records(local_data / "validation.jsonl")
    check_split(train, validation)

    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainerCallback,
        TrainingArguments,
        set_seed,
    )

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("Select a GPU runtime supporting BF16, preferably A100")
    set_seed(42)
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    )
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    local_run = args.local_root / "runs" / run_id
    adapter_dir = root / "weights" / run_id / "adapter"
    metadata = {
        "run_id": run_id,
        "status": "running",
        "base_model": MODEL,
        "base_revision": args.revision,
        "gpu": torch.cuda.get_device_name(),
        "vram_bytes": torch.cuda.get_device_properties(0).total_memory,
        "epochs": args.epochs,
        "max_length": args.max_length,
        "seed": 42,
        "task": manifest["task"],
        "label_kind": manifest["label_kind"],
        "train_rows": len(train),
        "validation_rows": len(validation),
        "dataset_sha256": hashlib.sha256(
            (root / "data/manifest.json").read_bytes()
        ).hexdigest(),
        "packages": {
            p: importlib.metadata.version(p)
            for p in ("torch", "transformers", "peft", "accelerate", "bitsandbytes")
        },
        "lora": {"rank": 16, "alpha": 32, "dropout": 0.05},
        "quantization": "NF4 double quantization; BF16 compute",
        "adapter_dir": adapter_dir.relative_to(root).as_posix(),
    }
    write_json(run_dir / "run.json", metadata)
    (run_dir / "requirements.freeze.txt").write_text(
        subprocess.check_output([sys.executable, "-m", "pip", "freeze"], text=True),
        encoding="utf-8",
    )
    started = time.perf_counter()
    try:
        tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=args.revision)
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        tokenizer.padding_side = "right"
        encoded_train = [
            encode_record(tokenizer, row, args.max_length) for row in train
        ]
        encoded_validation = [
            encode_record(tokenizer, row, args.max_length) for row in validation
        ]
        model = AutoModelForCausalLM.from_pretrained(
            MODEL,
            revision=args.revision,
            torch_dtype=torch.bfloat16,
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
            ),
            device_map={"": 0},
            attn_implementation="sdpa",
        )
        model = prepare_model_for_kbit_training(
            model,
            use_gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False},
        )
        model = get_peft_model(
            model,
            LoraConfig(
                task_type="CAUSAL_LM",
                r=16,
                lora_alpha=32,
                lora_dropout=0.05,
                bias="none",
                target_modules=[
                    "q_proj",
                    "k_proj",
                    "v_proj",
                    "o_proj",
                    "gate_proj",
                    "up_proj",
                    "down_proj",
                ],
            ),
        )
        model.print_trainable_parameters()
        model.config.use_cache = True
        with model.disable_adapter():
            baseline = evaluate_choices(model, tokenizer, validation)
        write_json(run_dir / "baseline.json", baseline)
        model.config.use_cache = False

        class DriveCheckpoint(TrainerCallback):
            def on_save(self, args, state, control, **kwargs):
                checkpoint = Path(args.output_dir) / f"checkpoint-{state.global_step}"
                # Save only portable adapter weights, not multi-GB optimizer files.
                dest = run_dir / "checkpoints" / checkpoint.name
                dest.mkdir(parents=True, exist_ok=True)
                for filename in (
                    "adapter_config.json",
                    "adapter_model.safetensors",
                    "trainer_state.json",
                ):
                    if (checkpoint / filename).exists():
                        shutil.copy2(checkpoint / filename, dest / filename)
                tokenizer.save_pretrained(dest)

        trainer = Trainer(
            model=model,
            processing_class=tokenizer,
            train_dataset=encoded_train,
            eval_dataset=encoded_validation,
            data_collator=DataCollatorForSeq2Seq(
                tokenizer=tokenizer, padding=True, label_pad_token_id=-100
            ),
            args=TrainingArguments(
                output_dir=str(local_run),
                num_train_epochs=args.epochs,
                per_device_train_batch_size=1,
                per_device_eval_batch_size=1,
                gradient_accumulation_steps=8,
                learning_rate=1e-4,
                warmup_ratio=0.05,
                lr_scheduler_type="cosine",
                bf16=True,
                optim="adamw_torch",
                gradient_checkpointing=True,
                gradient_checkpointing_kwargs={"use_reentrant": False},
                eval_strategy="epoch",
                prediction_loss_only=True,
                save_strategy="epoch",
                save_total_limit=1,
                logging_steps=5,
                report_to="none",
                seed=42,
            ),
            callbacks=[DriveCheckpoint()],
        )
        train_result = trainer.train()
        model.save_pretrained(adapter_dir, safe_serialization=True)
        tokenizer.save_pretrained(adapter_dir)
        model.config.use_cache = True
        adapted = evaluate_choices(model, tokenizer, validation)
        heuristic = sum(
            r["heuristic_choice"] == json.loads(r["messages"][-1]["content"])["choice"]
            for r in validation
        ) / len(validation)
        metrics = {
            "baseline": baseline,
            "adapted": adapted,
            "heuristic_weak_label_accuracy": heuristic,
            "train_metrics": train_result.metrics,
            "recommended_for_visual_trial": adapted["weak_label_accuracy"]
            > baseline["weak_label_accuracy"]
            and adapted["weak_label_accuracy"] > heuristic
            and adapted["valid_json_rate"] == 1.0,
            "note": "Proxy labels, small held-out template. This is not a visual quality evaluation.",
        }
        write_json(run_dir / "metrics.json", metrics)
        metadata.update(
            status="completed",
            elapsed_seconds=round(time.perf_counter() - started, 2),
            peak_cuda_memory_bytes=torch.cuda.max_memory_allocated(),
        )
        write_json(run_dir / "run.json", metadata)
        write_json(adapter_dir / "training_run.json", metadata)
        # Set latest only after weights and evaluation were saved successfully.
        write_json(
            root / "weights/latest.json",
            {
                "run_id": run_id,
                "adapter_dir": metadata["adapter_dir"],
                "run_dir": run_dir.relative_to(root).as_posix(),
            },
        )
        archive = shutil.make_archive(
            str(root / "weights" / run_id / "adapter"), "zip", adapter_dir
        )
        print(
            json.dumps(
                {
                    "adapter": str(adapter_dir),
                    "archive": archive,
                    "metrics": str(run_dir / "metrics.json"),
                    "recommended_for_visual_trial": metrics[
                        "recommended_for_visual_trial"
                    ],
                },
                ensure_ascii=False,
            )
        )
    except Exception as exc:
        metadata.update(
            status="failed",
            error=str(exc),
            elapsed_seconds=round(time.perf_counter() - started, 2),
        )
        write_json(run_dir / "run.json", metadata)
        raise


if __name__ == "__main__":
    main()
