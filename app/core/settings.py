"""Runtime settings, read from environment variables or a local .env file."""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SecretStr hides the value in logs and reprs. If unset, the Anthropic SDK
    # falls back to its own credential lookup (e.g. an `ant auth login` profile).
    anthropic_api_key: SecretStr | None = None

    llm_model: str = "claude-opus-5-5"
    # How hard the model thinks before answering. Receptionist chat rarely needs
    # deep reasoning, and lower effort means faster, cheaper replies.
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    llm_max_tokens: int = 16000  # ceiling, not a target: you pay for what is generated
    llm_timeout_seconds: float = 60.0  # SDK default is 10 min, far too long for a chat UI
    llm_max_retries: int = 2  # SDK retries 429/5xx/connection errors with backoff
