"""Optional local test of Trainer -> LoRA weights -> reload, on a tiny random model.

This does NOT train Qwen 7B, test CUDA/quantization, or create usable user weights.
"""

import json
import tempfile
from pathlib import Path


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
        TrainingArguments,
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
        rows = [
            {
                "input_ids": [3, 4, 5, 2],
                "attention_mask": [1] * 4,
                "labels": [-100, -100, 5, 2],
            },
            {"input_ids": [3, 6, 2], "attention_mask": [1] * 3, "labels": [-100, 6, 2]},
        ]
        trainer = Trainer(
            model=model,
            processing_class=tokenizer,
            train_dataset=rows,
            data_collator=DataCollatorForSeq2Seq(
                tokenizer=tokenizer, label_pad_token_id=-100
            ),
            args=TrainingArguments(
                output_dir=str(scratch / "run"),
                use_cpu=True,
                max_steps=2,
                per_device_train_batch_size=2,
                learning_rate=0.01,
                report_to="none",
                save_strategy="no",
                disable_tqdm=True,
                dataloader_pin_memory=False,
            ),
        )
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
        report = {
            "kind": "tiny_random_cpu_model_only",
            "steps": 2,
            "loss": result.training_loss,
            "lora_weights_changed": True,
            "save_reload_logits_match": True,
            "cuda_quantization_tested": False,
            "real_7b_training_run": False,
        }
        (root / "cpu-report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report))


if __name__ == "__main__":
    main()
