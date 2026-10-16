"""Builds the configured LLM provider from settings."""
from __future__ import annotations

from groupflow.core.settings import LlmSettings

from .bedrock import BedrockProvider
from .openai_compat import OpenAiCompatibleProvider
from .types import LlmError, LlmRequest, LlmResponse


class MissingProvider:
    """Stands in when nothing is configured, so the app starts and explains what is missing."""

    id = "none"
    model = ""

    def __init__(self, label: str, reason: str):
        self.label = label
        self.reason = reason

    async def complete(self, req: LlmRequest) -> LlmResponse:
        raise LlmError(self.reason)


def create_provider(s: LlmSettings | None = None):
    s = s or LlmSettings()
    which = (s.provider or "deepseek").lower()
    if which == "deepseek":
        if not s.deepseek_api_key:
            return MissingProvider("DeepSeek", "No DeepSeek API key is set. Add DEEPSEEK_API_KEY=... to the .env file in the GroupFlow folder and restart.")
        return OpenAiCompatibleProvider(id="deepseek", label="DeepSeek", base_url=s.deepseek_base_url, api_key=s.deepseek_api_key,
                                        model=s.deepseek_model, thinking=s.deepseek_thinking, timeout_s=s.timeout_s)
    if which == "openai":
        if not s.openai_base_url or not s.openai_model:
            return MissingProvider("OpenAI-compatible", "Set OPENAI_COMPAT_BASE_URL, OPENAI_COMPAT_MODEL and (if needed) OPENAI_COMPAT_API_KEY in .env.")
        return OpenAiCompatibleProvider(id="openai", label="OpenAI-compatible", base_url=s.openai_base_url, api_key=s.openai_api_key or "",
                                        model=s.openai_model, thinking=None, timeout_s=s.timeout_s)
    if which == "bedrock":
        return BedrockProvider(model_id=s.bedrock_model_id, region=s.aws_region)
    return MissingProvider(which, f'Unknown AGENT_PROVIDER "{s.provider}". Use deepseek, bedrock or openai.')
