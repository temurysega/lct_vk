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

if __package__:
    from .checkpoints import (
        atomic_json,
        complete_checkpoints,
        drive_callback,
        file_hash,
        find_run,
        restore_checkpoint,
    )
    from .data_utils import check_split, encode_record, load_records
else:
    from checkpoints import (
        atomic_json,
        complete_checkpoints,
        drive_callback,
        file_hash,
        find_run,
        restore_checkpoint,
    )
    from data_utils import check_split, encode_record, load_records

MODEL = "Qwen/Qwen2.5-7B-Instruct"


def write_json(path: Path, value) -> None:
    atomic_json(path, value)


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
        "--model", default=MODEL, choices=[MODEL, "Qwen/Qwen2.5-1.5B-Instruct"]
    )
    parser.add_argument("--save-steps", type=int, default=10)
    parser.add_argument("--keep-checkpoints", type=int, default=2)
    parser.add_argument(
        "--resume",
        default="auto",
        help="auto, none for a new run, or an existing run ID",
    )
    parser.add_argument(
        "--revision", required=True, help="Pinned Hugging Face model commit"
    )
    args = parser.parse_args()
    if (
        args.epochs <= 0
        or args.max_length < 256
        or args.save_steps < 1
        or args.keep_checkpoints < 2
    ):
        parser.error(
            "Invalid epochs/context/save interval; keep at least two checkpoints"
        )

    # Verify the uploaded inputs before spending GPU time. Copy dataset reads off Drive.
    root = args.drive_root.resolve()
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
    test = (
        load_records(local_data / "test.jsonl")
        if (local_data / "test.jsonl").exists()
        else None
    )
    check_split(train, validation, test)

    previous = find_run(root, args.resume)
    if previous:
        run_dir, metadata = previous
        if metadata["base_model"] != args.model:
            raise ValueError(
                "This run belongs to another base model; use a separate Drive folder or --resume none"
            )
        if (
            metadata.get("dataset_sha256")
            != hashlib.sha256((root / "data/manifest.json").read_bytes()).hexdigest()
        ):
            raise ValueError(
                "Dataset changed; use a separate Drive folder or --resume none"
            )
        if metadata["status"] == "completed":
            adapter = root / metadata["adapter_dir"]
            inventory = json.loads(
                (adapter / "SHA256SUMS.json").read_text(encoding="utf-8")
            )
            for name, expected in inventory.items():
                if file_hash(adapter / name) != expected:
                    raise ValueError(f"Final adapter checksum mismatch: {name}")
            if not adapter.with_suffix(".zip").is_file():
                raise ValueError("Completed run archive is missing")
            # Recover a termination between final run.json and latest.json publication.
            write_json(
                root / "weights/latest.json",
                {
                    "run_id": run_dir.name,
                    "adapter_dir": metadata["adapter_dir"],
                    "run_dir": run_dir.relative_to(root).as_posix(),
                },
            )
            print(
                f"Run {run_dir.name} already completed. Use --resume none for new training."
            )
            print(f"Weights: {root / metadata['adapter_dir']}")
            return
        # Resume uses the original base even if the public repository changed.
        args.revision = metadata["base_revision"]
        print(f"Resuming run {run_dir.name}; pinned base revision: {args.revision}")

    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        DataCollatorForSeq2Seq,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError("Select a GPU runtime supporting BF16, preferably A100")
    set_seed(42)
    packages = {
        p: importlib.metadata.version(p)
        for p in ("torch", "transformers", "peft", "accelerate", "bitsandbytes")
    }
    resume_spec = {
        "base_model": args.model,
        "base_revision": args.revision,
        "train_sha256": file_hash(local_data / "train.jsonl"),
        "validation_sha256": file_hash(local_data / "validation.jsonl"),
        "script_sha256": {
            name: file_hash(Path(__file__).with_name(name))
            for name in ("train_layout.py", "data_utils.py", "checkpoints.py")
        },
        "epochs": args.epochs,
        "max_length": args.max_length,
        "seed": 42,
        "batch_size": 1,
        "gradient_accumulation_steps": 8,
        "learning_rate": 1e-4,
        "warmup_ratio": 0.05,
        "scheduler": "cosine",
        "lora": {"rank": 16, "alpha": 32, "dropout": 0.05},
        "packages": packages,
    }
    if previous and metadata["resume_spec"] != resume_spec:
        raise ValueError(
            "Training data/configuration changed; restore original settings or use --resume none"
        )
    run_id = (
        previous[0].name
        if previous
        else (
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        )
    )
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=bool(previous))
    # Separate local attempts so stale local checkpoints cannot affect recovery.
    local_run = args.local_root / "runs" / run_id / uuid4().hex[:8]
    adapter_dir = root / "weights" / run_id / "adapter"
    snapshots = complete_checkpoints(run_dir) if previous else []
    resume_path = None
    if snapshots:
        resume_path = restore_checkpoint(snapshots[0][0], local_run, resume_spec)
        print(
            f"Restored full training state from step {snapshots[0][1]['global_step']}"
        )
    elif previous:
        if any((run_dir / "checkpoints").glob("checkpoint-*")):
            raise ValueError(
                "No valid complete checkpoint; restore a backup or use --resume none"
            )
        print("No checkpoint was reached; restarting this run at step 0.")
    previous_elapsed = metadata.get("elapsed_seconds", 0) if previous else 0
    metadata = {
        "run_id": run_id,
        "status": "running",
        "base_model": args.model,
        "base_revision": args.revision,
        "checkpoint_version": 1,
        "resume_spec": resume_spec,
        "resumed_from": snapshots[0][0].relative_to(root).as_posix()
        if snapshots
        else None,
        "save_steps": args.save_steps,
        "keep_checkpoints": args.keep_checkpoints,
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
        "packages": packages,
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
        tokenizer = AutoTokenizer.from_pretrained(args.model, revision=args.revision)
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
            args.model,
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
        if (run_dir / "baseline.json").exists():
            baseline = json.loads(
                (run_dir / "baseline.json").read_text(encoding="utf-8")
            )
        else:
            with model.disable_adapter():
                baseline = evaluate_choices(model, tokenizer, validation)
            write_json(run_dir / "baseline.json", baseline)
        model.config.use_cache = False

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
                save_strategy="steps",
                save_steps=args.save_steps,
                save_total_limit=2,
                save_only_model=False,
                logging_steps=5,
                report_to="none",
                seed=42,
            ),
            callbacks=[drive_callback(run_dir, resume_spec, args.keep_checkpoints)],
        )
        train_result = trainer.train(
            resume_from_checkpoint=str(resume_path) if resume_path else None
        )
        trainer.save_state()
        model.peft_config["default"].revision = args.revision
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
            status="exporting",
            elapsed_seconds=round(previous_elapsed + time.perf_counter() - started, 2),
            peak_cuda_memory_bytes=torch.cuda.max_memory_allocated(),
            global_step=trainer.state.global_step,
        )
        final_metadata = dict(metadata, status="completed")
        write_json(adapter_dir / "training_run.json", final_metadata)
        for name in ("metrics.json", "requirements.freeze.txt"):
            shutil.copy2(run_dir / name, adapter_dir / name)
        shutil.copy2(root / "data/manifest.json", adapter_dir / "dataset_manifest.json")
        inventory = {
            p.name: file_hash(p)
            for p in adapter_dir.iterdir()
            if p.is_file() and p.name != "SHA256SUMS.json"
        }
        write_json(adapter_dir / "SHA256SUMS.json", inventory)
        temporary_archive = shutil.make_archive(
            str(root / "weights" / run_id / ("adapter-writing-" + uuid4().hex)),
            "zip",
            adapter_dir,
        )
        archive = root / "weights" / run_id / "adapter.zip"
        Path(temporary_archive).replace(archive)
        # Publish only after weights, evaluation and the download archive exist.
        write_json(run_dir / "run.json", final_metadata)
        write_json(
            root / "weights/latest.json",
            {
                "run_id": run_id,
                "adapter_dir": metadata["adapter_dir"],
                "run_dir": run_dir.relative_to(root).as_posix(),
            },
        )
        print(
            json.dumps(
                {
                    "adapter": str(adapter_dir),
                    "archive": str(archive),
                    "metrics": str(run_dir / "metrics.json"),
                    "recommended_for_visual_trial": metrics[
                        "recommended_for_visual_trial"
                    ],
                },
                ensure_ascii=False,
            )
        )
    except BaseException as exc:
        metadata.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            error=str(exc),
            elapsed_seconds=round(previous_elapsed + time.perf_counter() - started, 2),
        )
        write_json(run_dir / "run.json", metadata)
        raise


if __name__ == "__main__":
    main()
