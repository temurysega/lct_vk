"""Optional local test of Trainer -> LoRA weights -> reload, on a tiny random model.

This does NOT train Qwen 7B, test CUDA/quantization, or create usable user weights.
"""

import json
import tempfile
from pathlib import Path

if __package__:
    from .checkpoints import complete_checkpoints, drive_callback, restore_checkpoint
else:
    from checkpoints import complete_checkpoints, drive_callback, restore_checkpoint


def main():
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from transformers import (
        AutoModelForCausalLM,
        DataCollatorForSeq2Seq,
        PreTrainedTokenizerFast,
        Qwen2Config,
        Qwen2ForCausalLM,
        Trainer,
        TrainerCallback,
        TrainingArguments,
        set_seed,
    )

    torch.set_num_threads(2)
    torch.manual_seed(42)
    root = Path(__file__).resolve().parents[1] / "slide-workspace/training-check"
    root.mkdir(parents=True, exist_ok=True)
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=Tokenizer(
            WordLevel({"<pad>": 0, "<unk>": 1, "</s>": 2, "fact": 3})
        ),
        pad_token="<pad>",
        unk_token="<unk>",
        eos_token="</s>",
    )
    with tempfile.TemporaryDirectory(dir=root) as scratch:
        scratch = Path(scratch)
        model = Qwen2ForCausalLM(
            Qwen2Config(
                vocab_size=32,
                hidden_size=32,
                intermediate_size=64,
                num_hidden_layers=1,
                num_attention_heads=4,
                num_key_value_heads=2,
                pad_token_id=0,
                eos_token_id=2,
            )
        )
        model.save_pretrained(scratch / "base")
        model = get_peft_model(
            model,
            LoraConfig(
                r=4,
                lora_alpha=8,
                task_type="CAUSAL_LM",
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
        model.save_pretrained(scratch / "initial-adapter")
        rows = [
            {
                "input_ids": [3, 4, 5, 2],
                "attention_mask": [1] * 4,
                "labels": [-100, -100, 5, 2],
            },
            {"input_ids": [3, 6, 2], "attention_mask": [1] * 3, "labels": [-100, 6, 2]},
        ]

        def make_trainer(current_model, output, callbacks=()):
            return Trainer(
                model=current_model,
                processing_class=tokenizer,
                train_dataset=rows,
                data_collator=DataCollatorForSeq2Seq(
                    tokenizer=tokenizer, label_pad_token_id=-100
                ),
                args=TrainingArguments(
                    output_dir=str(output),
                    use_cpu=True,
                    max_steps=4,
                    per_device_train_batch_size=2,
                    learning_rate=0.01,
                    report_to="none",
                    save_strategy="steps",
                    save_steps=2,
                    save_total_limit=2,
                    disable_tqdm=True,
                    dataloader_pin_memory=False,
                ),
                callbacks=list(callbacks),
            )

        trainer = make_trainer(model, scratch / "uninterrupted")
        result = trainer.train()
        assert any(
            p.detach().abs().sum().item() > 0
            for n, p in model.named_parameters()
            if "lora_B" in n
        )
        model.eval()
        ids = torch.tensor([[3, 4, 5]])
        with torch.no_grad():
            expected = model(input_ids=ids).logits
        model.save_pretrained(scratch / "adapter", safe_serialization=True)
        restored = PeftModel.from_pretrained(
            AutoModelForCausalLM.from_pretrained(scratch / "base"), scratch / "adapter"
        )
        restored.eval()
        with torch.no_grad():
            actual = restored(input_ids=ids).logits
        assert torch.allclose(expected, actual, atol=1e-6)

        class SimulatedDisconnect(RuntimeError):
            pass

        class DisconnectAfterSave(TrainerCallback):
            def on_save(self, args, state, control, **kwargs):
                if state.global_step == 2:
                    raise SimulatedDisconnect("Pretend the Colab runtime was lost")

        def initial_model():
            set_seed(42)
            return PeftModel.from_pretrained(
                AutoModelForCausalLM.from_pretrained(scratch / "base"),
                scratch / "initial-adapter",
                is_trainable=True,
            )

        spec = {"model": "tiny-random", "max_steps": 4, "seed": 42}
        drive_run = scratch / "simulated-drive/run"
        interrupted = make_trainer(
            initial_model(),
            scratch / "old-session",
            [
                drive_callback(drive_run, spec),
                DisconnectAfterSave(),
            ],
        )
        try:
            interrupted.train()
        except SimulatedDisconnect:
            pass
        else:
            raise AssertionError("The interrupted training must stop at step 2")
        snapshot, info = complete_checkpoints(drive_run)[0]
        assert info["global_step"] == 2
        local_checkpoint = restore_checkpoint(snapshot, scratch / "new-session", spec)
        resumed_model = initial_model()
        resumed = make_trainer(
            resumed_model, scratch / "resumed", [drive_callback(drive_run, spec)]
        )
        resumed.train(resume_from_checkpoint=str(local_checkpoint))
        assert resumed.state.global_step == trainer.state.global_step == 4
        expected_parameters = dict(model.named_parameters())
        for name, parameter in resumed_model.named_parameters():
            if parameter.requires_grad:
                assert torch.allclose(
                    parameter, expected_parameters[name], atol=1e-6
                ), name
        assert resumed.lr_scheduler.state_dict() == trainer.lr_scheduler.state_dict()
        assert all(int(s["step"]) == 4 for s in resumed.optimizer.state.values())
        assert [m["global_step"] for _, m in complete_checkpoints(drive_run)] == [4, 3]
        report = {
            "kind": "tiny_random_cpu_model_only",
            "steps": 4,
            "loss": result.training_loss,
            "lora_weights_changed": True,
            "save_reload_logits_match": True,
            "interrupted_at_step": 2,
            "full_checkpoint_resume_matches_uninterrupted": True,
            "optimizer_and_scheduler_restored": True,
            "cuda_quantization_tested": False,
            "real_7b_training_run": False,
        }
        (root / "cpu-report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report))


if __name__ == "__main__":
    main()
