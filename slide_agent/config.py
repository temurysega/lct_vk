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
    max_output_tokens: int = 0
    parallel_requests: int = 1
    extra_body: str = ""

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
            max_output_tokens=int(os.getenv("INFERENCE_MAX_OUTPUT_TOKENS", "0")),
            parallel_requests=max(1, int(os.getenv("INFERENCE_PARALLEL", "1"))),
            extra_body=os.getenv("INFERENCE_EXTRA_BODY", "").strip(),
        )
