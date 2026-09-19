from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class InferenceSettings:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: int = 120
    max_retries: int = 3
    vision_enabled: bool = True
    backend: str = "openai"
    context_tokens: int = 0
    max_output_tokens: int = 0
    lora_id: int | None = None
    lora_scale: float = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)

    @classmethod
    def from_env(cls) -> InferenceSettings:
        return cls(
            base_url=os.getenv("INFERENCE_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("INFERENCE_API_KEY", ""),
            model=os.getenv("INFERENCE_MODEL", ""),
            timeout_seconds=int(os.getenv("INFERENCE_TIMEOUT", "120")),
            max_retries=int(os.getenv("INFERENCE_MAX_RETRIES", "3")),
            vision_enabled=os.getenv("INFERENCE_VISION", "1").strip().lower()
            not in {"0", "false", "off", "no"},
            backend=os.getenv("INFERENCE_BACKEND", "openai"),
            context_tokens=int(os.getenv("INFERENCE_CONTEXT_TOKENS", "0")),
            max_output_tokens=int(os.getenv("INFERENCE_MAX_OUTPUT_TOKENS", "0")),
            lora_id=int(os.environ["INFERENCE_LORA_ID"])
            if os.getenv("INFERENCE_LORA_ID")
            else None,
        )
