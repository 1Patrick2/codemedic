"""Preflight checks for controlled real-model proof runs."""

from __future__ import annotations

from codemedic.config import Settings, settings


class RealModelConfigError(ValueError):
    """Raised when a real-model run lacks explicit safe configuration."""


def ensure_real_model_configured(config: Settings = settings) -> None:
    """Fail closed unless Provider, model, and a non-placeholder key exist."""
    api_key = config.openai_api_key.strip()
    placeholders = {
        "",
        "your-opencode-api-key",
        "your-api-key",
        "changeme",
    }
    if api_key.lower() in placeholders:
        raise RealModelConfigError(
            "Real-model proof requires an explicit OPENAI_API_KEY; "
            "placeholder or empty credentials are not accepted."
        )
    if not config.openai_api_base.strip():
        raise RealModelConfigError("Real-model proof requires OPENAI_API_BASE.")
    if not config.openai_model_name.strip():
        raise RealModelConfigError("Real-model proof requires OPENAI_MODEL_NAME.")
