from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResult:
    generated_sql: str
    alternatives: list[str]
    tables: list[str]
    columns: list[str]
    confidence_score: float
    ambiguity: bool
    intent: str
    suggestions: list[str]


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> LLMResult:
        raise NotImplementedError


class HeuristicLLMProvider(LLMProvider):
    def generate(self, prompt: str) -> LLMResult:
        raise NotImplementedError("Heuristic provider does not generate directly from free-form prompt.")
