"""Safe image preparation and optional incident classifier adapter."""
from __future__ import annotations

import importlib
import inspect
import os
import warnings
from io import BytesIO
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
ALLOWED_CATEGORIES = {"water", "electricity", "roads", "waste", "stormwater", "environmental", "other"}


class InvalidIncidentImage(ValueError):
    pass


def prepare_image(data: bytes) -> Image.Image:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise InvalidIncidentImage("Choose an image smaller than 10 MB.")
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            source = Image.open(BytesIO(data))
            if source.format not in ALLOWED_FORMATS:
                raise InvalidIncidentImage("Use a JPEG, PNG, or WebP image.")
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise InvalidIncidentImage("This image is too large to process safely.")
            source.verify()
            source = Image.open(BytesIO(data))
            normalized = ImageOps.exif_transpose(source).convert("RGB")
            normalized.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            clean = BytesIO()
            normalized.save(clean, format="JPEG", quality=88, optimize=True)
            clean.seek(0)
            return Image.open(clean).copy()
    except InvalidIncidentImage:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidIncidentImage("The selected file is not a valid supported image.") from exc


def analyze(image: Image.Image, description: str = "") -> dict[str, Any]:
    """Call an explicitly configured classifier or report that analysis is unavailable."""
    configured = os.getenv("ECOPULSE_INCIDENT_MODEL", "").strip()
    if not configured or ":" not in configured:
        return {"available": False, "reason": "No incident classification model is configured. You can still submit this report manually.", "model_name": "unavailable", "model_version": "none"}
    module_name, function_name = configured.rsplit(":", 1)
    try:
        predictor = getattr(importlib.import_module(module_name), function_name)
        params = inspect.signature(predictor).parameters
        raw = predictor(image, description=description) if "description" in params else predictor(image)
        if not isinstance(raw, dict):
            raise ValueError("Classifier must return a mapping")
        category = raw.get("category")
        if category not in ALLOWED_CATEGORIES:
            category = "other"
        confidence = raw.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            confidence = None
        observations = raw.get("observations")
        if not isinstance(observations, list) or not all(isinstance(item, str) for item in observations):
            observations = []
        observations = [item.strip()[:240] for item in observations[:8] if item.strip()]
        if not observations:
            return {"available": False, "reason": "The configured model returned no visual observations. You can still submit manually.", "model_name": str(raw.get("model_name", "configured"))[:100], "model_version": str(raw.get("model_version", "unknown"))[:100]}
        model_name = str(raw.get("model_name", module_name))[:100]
        model_version = str(raw.get("model_version", "unknown"))[:100]
        explanation = f"The configured image model reported: {'; '.join(observations)}. This may indicate a {category.replace('_', ' ')} concern, but the photograph alone cannot confirm the cause or verify an incident."
        return {"available": True, "category": category, "subcategory": str(raw.get("subcategory", ""))[:100], "observations": observations, "confidence": confidence, "severity": str(raw.get("severity", "unknown"))[:30], "recommended_next_steps": ["Review the suggested category and edit it if needed.", "Avoid approaching hazards such as exposed wiring."], "explanation": explanation, "model_name": model_name, "model_version": model_version}
    except Exception:
        return {"available": False, "reason": "Image analysis is temporarily unavailable. You can still submit this report manually.", "model_name": "configured", "model_version": "unknown"}
