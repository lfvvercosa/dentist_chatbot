"""Claude implementation of LLMClient. The only module that imports the Anthropic SDK."""

from collections.abc import Sequence

import anthropic
from pydantic import ValidationError

from app.core.settings import Settings
from app.llm.client import ChatMessage, LLMClient, LLMError, LLMResult
from app.schemas.assistant import AssistantDecision

# If Claude's safety classifiers decline a request, the API retries it on a
# fallback model chosen by Anthropic instead of returning a refusal.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeClient(LLMClient):
    def __init__(self, sdk_client: anthropic.Anthropic, model: str, effort: str, max_tokens: int):
        self._sdk = sdk_client
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens

    def generate(self, system: str, messages: Sequence[ChatMessage]) -> LLMResult:
        try:
            response = self._sdk.beta.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                output_config={
                    "effort": self._effort,
                    "format": {
                        "type": "json_schema",
                        "schema": AssistantDecision.model_json_schema(),
                    },
                },
                betas=[_FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIStatusError as exc:  # the API answered with an error (4xx/5xx)
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:  # never reached the API (network, timeout)
            raise LLMError(f"Could not reach Claude API: {exc}") from exc

        # Check why the model stopped BEFORE trusting the content.
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined to answer")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude's answer was cut off by max_tokens")
        if response.stop_reason != "end_turn":
            raise LLMError("Claude returned an incomplete assistant decision")

        # content is a list of blocks (thinking, text, ...). Keep only the text.
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise LLMError("Claude returned no text")

        try:
            decision = AssistantDecision.model_validate_json(text)
        except ValidationError as exc:
            # Validation errors can include the patient's text; never log their details.
            raise LLMError("Claude returned an invalid assistant decision") from exc

        return LLMResult(
            text=decision.reply,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            escalation=decision.escalation,
            language=decision.language,
        )


def build_claude_client(settings: Settings) -> ClaudeClient:
    api_key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    sdk_client = anthropic.Anthropic(
        api_key=api_key,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    return ClaudeClient(
        sdk_client,
        model=settings.llm_model,
        effort=settings.llm_effort,
        max_tokens=settings.llm_max_tokens,
    )
