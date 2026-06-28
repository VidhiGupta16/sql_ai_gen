from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ProviderContext:
    prompt: str
    metadata_prompt: str


@dataclass
class LLMPlanResult:
    candidates: list[dict]
    provider: str


class LLMProvider(ABC):
    @abstractmethod
    def generate_plans(self, context: ProviderContext) -> LLMPlanResult:
        raise NotImplementedError


class OpenAIProvider(LLMProvider):
    def generate_plans(self, context: ProviderContext) -> LLMPlanResult:
        return LLMPlanResult(candidates=[], provider="openai")


class GeminiProvider(LLMProvider):
    def generate_plans(self, context: ProviderContext) -> LLMPlanResult:
        return LLMPlanResult(candidates=[], provider="gemini")


class OllamaProvider(LLMProvider):
    def generate_plans(self, context: ProviderContext) -> LLMPlanResult:
        return LLMPlanResult(candidates=[], provider="ollama")


class HeuristicProvider(LLMProvider):
    def generate_plans(self, context: ProviderContext) -> LLMPlanResult:
        return LLMPlanResult(candidates=[], provider="heuristic")


def get_provider(name: str | None = None) -> LLMProvider:
    normalized = (name or "heuristic").lower()
    if normalized == "openai":
        return OpenAIProvider()
    if normalized == "gemini":
        return GeminiProvider()
    if normalized == "ollama":
        return OllamaProvider()
    return HeuristicProvider()
