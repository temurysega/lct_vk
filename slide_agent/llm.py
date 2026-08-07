from __future__ import annotations

import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import InferenceSettings
from .utils import strip_code_fence


class InferenceError(RuntimeError):
    pass


@dataclass
class InferenceClient:
    """Small OpenAI-compatible chat-completions client.

    The competition Inference API can be configured without coupling the
    project to a particular SDK. Both ``https://host/v1`` and a full
    ``.../chat/completions`` URL are accepted.
    """

    settings: InferenceSettings

    def _endpoint(self) -> str:
        url = self.settings.base_url.rstrip("/")
        if url.endswith("/chat/completions"):
            return url
        return f"{url}/chat/completions"

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        json_mode: bool = False,
        max_tokens: int = 5000,
        image_paths: list[str] | None = None,
    ) -> str:
        if not self.settings.enabled:
            raise InferenceError(
                "Inference API is not configured. Set INFERENCE_BASE_URL and INFERENCE_MODEL."
            )

        user_content: str | list[dict[str, Any]] = user
        if image_paths:
            user_content = [{"type": "text", "text": user}]
            for path in image_paths:
                mime = mimetypes.guess_type(path)[0] or "image/png"
                with open(path, "rb") as stream:
                    encoded = base64.b64encode(stream.read()).decode("ascii")
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{encoded}"},
                    }
                )

        payload: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        last_error: Exception | None = None
        for attempt in range(self.settings.max_retries):
            request = urllib.request.Request(
                self._endpoint(),
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.settings.timeout_seconds
                ) as response:
                    body = json.loads(response.read().decode("utf-8"))
                content = body["choices"][0]["message"]["content"]
                if isinstance(content, list):
                    content = "".join(
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict)
                    )
                if not isinstance(content, str) or not content.strip():
                    raise InferenceError(
                        "Inference API returned an empty assistant message"
                    )
                return content.strip()
            except (
                urllib.error.HTTPError,
                urllib.error.URLError,
                TimeoutError,
                KeyError,
                ValueError,
            ) as exc:
                last_error = exc
                if (
                    isinstance(exc, urllib.error.HTTPError)
                    and exc.code < 500
                    and exc.code != 429
                ):
                    try:
                        details = exc.read().decode("utf-8", errors="replace")
                    except (OSError, ValueError):
                        details = str(exc)
                    raise InferenceError(
                        f"Inference API rejected the request ({exc.code}): {details}"
                    ) from exc
                if attempt + 1 < self.settings.max_retries:
                    time.sleep(min(2**attempt, 8))

        raise InferenceError(
            f"Inference API request failed after retries: {last_error}"
        )

    def chat_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 5000,
        image_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            raw = self.chat(
                system=system,
                user=user,
                json_mode=True,
                max_tokens=max_tokens,
                image_paths=image_paths,
            )
        except InferenceError as exc:
            if "rejected the request (400)" not in str(exc):
                raise
            raw = self.chat(
                system=system,
                user=user,
                json_mode=False,
                max_tokens=max_tokens,
                image_paths=image_paths,
            )
        try:
            parsed = json.loads(strip_code_fence(raw))
        except json.JSONDecodeError as exc:
            raise InferenceError(f"Model did not return valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise InferenceError("Model JSON response must be an object")
        return parsed
