"""Optional text-to-image generation through an OpenAI-compatible images API.

Disabled unless ``IMAGE_BASE_URL`` and ``IMAGE_MODEL`` are set. The service is
model-agnostic; the competition limit for this task is an open-weights model up
to 20B parameters under Apache 2.0/MIT (for example FLUX.1-schnell, 12B,
Apache 2.0). Failures never break a deck: the slide falls back to a native
vector illustration and the reason is recorded in the plan.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .images import ImageLibrary
from .prompt_config import load_prompt


class ImageGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ImageSettings:
    base_url: str
    api_key: str
    model: str
    size: str = "1024x768"
    timeout_seconds: int = 180
    max_per_deck: int = 3
    auto: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)

    @classmethod
    def from_env(cls) -> ImageSettings:
        return cls(
            base_url=os.getenv("IMAGE_BASE_URL", "").rstrip("/"),
            api_key=os.getenv("IMAGE_API_KEY", ""),
            model=os.getenv("IMAGE_MODEL", ""),
            size=os.getenv("IMAGE_SIZE", "1024x768"),
            timeout_seconds=int(os.getenv("IMAGE_TIMEOUT", "180")),
            max_per_deck=max(0, min(10, int(os.getenv("IMAGE_MAX_PER_DECK", "3")))),
            auto=os.getenv("IMAGE_AUTO", "0").strip().lower()
            in {"1", "true", "yes", "on"},
        )


class ImageGenerator:
    def __init__(self, settings: ImageSettings) -> None:
        self.settings = settings

    def _endpoint(self) -> str:
        url = self.settings.base_url
        return (
            url if url.endswith("/images/generations") else f"{url}/images/generations"
        )

    def generate(self, prompt: str) -> bytes:
        payload = {
            "model": self.settings.model,
            "prompt": prompt,
            "n": 1,
            "size": self.settings.size,
            "response_format": "b64_json",
        }
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
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
            item = body["data"][0]
        except (
            urllib.error.URLError,
            TimeoutError,
            KeyError,
            IndexError,
            ValueError,
        ) as exc:
            raise ImageGenerationError(f"Image API request failed: {exc}") from exc
        if item.get("b64_json"):
            try:
                return base64.b64decode(item["b64_json"], validate=True)
            except ValueError as exc:
                raise ImageGenerationError("Image API returned invalid base64") from exc
        url = str(item.get("url") or "")
        # Only fetch results served by the configured endpoint's own host.
        if (
            url
            and urllib.parse.urlsplit(url).netloc
            == urllib.parse.urlsplit(self._endpoint()).netloc
        ):
            try:
                with urllib.request.urlopen(
                    url, timeout=self.settings.timeout_seconds
                ) as response:
                    return response.read(25 * 1024 * 1024 + 1)
            except (urllib.error.URLError, TimeoutError) as exc:
                raise ImageGenerationError(f"Image download failed: {exc}") from exc
        raise ImageGenerationError(
            "Image API returned neither b64_json nor a same-host URL"
        )


def image_prompt(slide: dict[str, Any], design: dict[str, Any]) -> str:
    visual = slide.get("visual") or {}
    points = [str(item) for item in slide.get("bullets", [])[:4]]
    if visual.get("request"):
        points.insert(0, str(visual["request"]))
    palette = (
        ", ".join(
            f"#{item.get('hex')}"
            for item in design.get("colors", {}).get("theme", [])
            if str(item.get("role", "")).startswith("accent") and item.get("hex")
        )[:120]
        or "neutral corporate blue"
    )
    return load_prompt("image").format(
        topic=str(slide.get("title", ""))[:160],
        points="; ".join(points)[:600] or "overview",
        palette=palette,
    )


def generate_images(
    plan: dict[str, Any],
    design: dict[str, Any],
    library: ImageLibrary,
    *,
    offline: bool,
    settings: ImageSettings | None = None,
    generator: ImageGenerator | None = None,
) -> dict[str, Any]:
    """Fill image requests (and, with ``IMAGE_AUTO=1``, sparse text slides)."""
    settings = settings or ImageSettings.from_env()
    report: dict[str, Any] = {
        "enabled": settings.enabled and not offline,
        "model": settings.model or None,
        "generated": [],
        "errors": [],
    }
    if offline or not settings.enabled or settings.max_per_deck == 0:
        plan["image_generation"] = report
        return report
    generator = generator or ImageGenerator(settings)
    slides = plan.get("slides", [])
    targets = [
        index
        for index, slide in enumerate(slides)
        if isinstance(slide.get("visual"), dict)
        and slide["visual"].get("type") == "image"
        and not slide["visual"].get("asset_id")
    ]
    if settings.auto:
        plain = [
            index
            for index, slide in enumerate(slides)
            if index not in targets
            and not slide.get("visual")
            and str(slide.get("role", "content")) not in {"cover", "closing", "section"}
        ]
        plain.sort(
            key=lambda index: len(" ".join(map(str, slides[index].get("bullets", []))))
        )
        targets.extend(sorted(plain[: max(0, settings.max_per_deck - len(targets))]))
    for index in targets[: settings.max_per_deck]:
        slide = slides[index]
        prompt = image_prompt(slide, design)
        try:
            data = generator.generate(prompt)
        except ImageGenerationError as exc:
            report["errors"].append({"slide": index + 1, "error": str(exc)[:300]})
            continue
        asset = library.add(
            data,
            source="generated",
            label=str(slide.get("title", "")),
            context=prompt,
            origin=f"{settings.model}#slide-{index + 1}",
        )
        if asset is None:
            report["errors"].append(
                {"slide": index + 1, "error": "generated image was rejected"}
            )
            continue
        visual = slide.get("visual") or {}
        slide["visual"] = {
            "type": "image",
            "asset_id": asset.asset_id,
            "match": "generated",
            **({"caption": visual["caption"]} if visual.get("caption") else {}),
        }
        report["generated"].append(
            {
                "slide": index + 1,
                "asset_id": asset.asset_id,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            }
        )
    plan["assets"] = library.plan_assets()
    plan["image_generation"] = report
    return report
