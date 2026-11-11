"""LLM providers behind one small interface.

Switching provider is configuration only (``ARGUS_LLM_PROVIDER``):
  * ``anthropic`` - Claude via the Anthropic API (agreed first choice)
  * ``azure``     - Azure OpenAI (planned once the Azure key is provided)
  * ``mock``      - offline assistant, nothing leaves the host (demo default)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from argus.config import Settings


class ProviderError(RuntimeError):
    pass


@dataclass
class LLMReply:
    text: str
    provider: str
    model: str


class LLMProvider(ABC):
    name = "base"
    external = True  # True when data leaves the host

    @abstractmethod
    def complete(self, system: str, messages: list[dict[str, str]]) -> LLMReply: ...


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("Install the 'anthropic' package: pip install argus[anthropic]") from exc
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
        self._model = model

    def complete(self, system: str, messages: list[dict[str, str]]) -> LLMReply:
        a = self._anthropic
        try:
            resp = self._client.beta.messages.create(
                model=self._model,
                max_tokens=8000,
                system=system,
                messages=messages,
                output_config={"effort": "low"},  # chat: fast, concise answers
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # server-side re-route if the model declines

            )
        except a.AuthenticationError as exc:
            raise ProviderError("Anthropic rejected the API key (check ANTHROPIC_API_KEY).") from exc
        except a.RateLimitError as exc:
            raise ProviderError("Anthropic rate limit reached, try again shortly.") from exc
        except a.APIStatusError as exc:
            raise ProviderError(f"Anthropic API error {exc.status_code}.") from exc
        except a.APIConnectionError as exc:
            raise ProviderError("Cannot reach the Anthropic API (network or proxy).") from exc
        if resp.stop_reason == "refusal":
            return LLMReply("I can't help with that request.", self.name, resp.model)
        text = "".join(b.text for b in resp.content if b.type == "text")
        return LLMReply(text, self.name, resp.model)


class AzureOpenAIProvider(LLMProvider):
    name = "azure"

    def __init__(self, endpoint: str, api_key: str, deployment: str, api_version: str):
        if not (endpoint and api_key and deployment):
            raise ProviderError("Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY and AZURE_OPENAI_DEPLOYMENT.")
        try:
            from openai import AzureOpenAI
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("Install the 'openai' package: pip install argus[azure]") from exc
        self._client = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
        self._deployment = deployment

    def complete(self, system: str, messages: list[dict[str, str]]) -> LLMReply:
        try:
            resp = self._client.chat.completions.create(
                model=self._deployment,
                messages=[{"role": "system", "content": system}, *messages],
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Azure OpenAI error: {exc.__class__.__name__}") from exc
        return LLMReply(resp.choices[0].message.content or "", self.name, self._deployment)


def build_provider(s: Settings) -> LLMProvider | None:
    """Return the configured external provider, or None for the offline mock assistant."""
    if s.llm_provider == "anthropic":
        return AnthropicProvider(s.anthropic_model)
    if s.llm_provider == "azure":
        return AzureOpenAIProvider(s.azure_endpoint, s.azure_api_key, s.azure_deployment, s.azure_api_version)
    return None
