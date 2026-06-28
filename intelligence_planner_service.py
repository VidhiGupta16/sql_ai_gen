from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.services.database_discovery_service import discover_database_objects
from app.services.llm_provider import ProviderContext, get_provider
from app.services.metadata_prompt_builder import build_metadata_only_prompt
from app.services.query_cost_service import estimate_query_cost
from app.services.query_optimization_service import optimize_sql
from app.services.query_validation_service import validate_query
from app.services.virtual_schema_service import build_virtual_schema


@dataclass
class RankedPlan:
    sql: str
    reasoning: str
    confidence: float
    schema_score: int
    validation_score: int
    cost_score: int
    optimization_score: int


def _template_sql(prompt: str, object_name: str, columns: list[str]) -> list[str]:
    column_list = ", ".join(columns[:3]) if columns else "*"
    base = f"SELECT {column_list}\nFROM {object_name}"
    lowered = prompt.lower()
    candidates = [f"{base};", f"SELECT *\nFROM {object_name}\nLIMIT 10;"]
    if any(token in lowered for token in ["top", "highest", "largest", "recent"]):
        candidates.append(f"{base}\nORDER BY {columns[0] if columns else '1'} DESC\nLIMIT 10;")
    if any(token in lowered for token in ["count", "how many", "number of"]):
        candidates.append(f"SELECT COUNT(*) AS total_count\nFROM {object_name};")
    return list(dict.fromkeys(candidates))


def _score_candidate(sql: str, prompt: str, table_count: int, validation, cost, optimized_sql: str) -> RankedPlan:
    validation_score = 100 if validation.valid else max(0, 40 - len(validation.errors) * 10)
    cost_score = max(0, 100 - int(cost.estimated_cost * 2))
    optimization_score = 100 if optimized_sql.strip() == sql.strip() else max(60, 95 - len(optimized_sql))
    schema_score = 100 if table_count else 0
    confidence = round((schema_score + validation_score + cost_score + optimization_score) / 400, 2)
    reasoning = "Ranked by metadata correctness, validation, cost, and optimization score."
    return RankedPlan(
        sql=sql,
        reasoning=reasoning,
        confidence=confidence,
        schema_score=schema_score,
        validation_score=validation_score,
        cost_score=cost_score,
        optimization_score=optimization_score,
    )


def generate_ranked_plans(db: Session, prompt: str, provider_name: str | None = None) -> dict:
    discovered = discover_database_objects(db)
    virtual_objects, mapping = build_virtual_schema(discovered)
    metadata_prompt = build_metadata_only_prompt(prompt, virtual_objects)
    provider = get_provider(provider_name)
    provider.generate_plans(ProviderContext(prompt=prompt, metadata_prompt=metadata_prompt))

    if not virtual_objects:
        return {
            "prompt": prompt,
            "virtual_schema": {"objects": [], "mapping": mapping.real_to_virtual},
            "candidates": [],
            "selected": None,
            "provider": provider.__class__.__name__.replace("Provider", "").lower() or "heuristic",
        }

    first_object = virtual_objects[0]
    virtual_name = first_object["virtual_name"]
    real_name = first_object["real_schema"] + "." + first_object["real_name"] if first_object["real_schema"] else first_object["real_name"]
    virtual_columns = [column["virtual_name"] for column in first_object["columns"]]

    virtual_candidates = _template_sql(prompt, virtual_name, virtual_columns)
    real_candidates = [
        candidate.replace(virtual_name, real_name).replace("COUNT(*) AS total_count", "COUNT(*) AS total_count")
        for candidate in virtual_candidates
    ]

    ranked: list[RankedPlan] = []
    for candidate in real_candidates[:5]:
        validation = validate_query(candidate)
        optimized = optimize_sql(candidate).optimized_sql
        cost_estimate, _, _, _, _ = estimate_query_cost(db, candidate)
        cost = SimpleNamespace(estimated_cost=cost_estimate)
        ranked.append(_score_candidate(candidate, prompt, len(virtual_objects), validation, cost, optimized))

    ranked.sort(key=lambda item: (item.schema_score, item.validation_score, item.cost_score, item.optimization_score, item.confidence), reverse=True)

    return {
        "prompt": prompt,
        "virtual_schema": {"objects": virtual_objects, "mapping": mapping.real_to_virtual},
        "candidates": [
            {
                "sql": item.sql,
                "reasoning": item.reasoning,
                "confidence": item.confidence,
                "schema_score": item.schema_score,
                "validation_score": item.validation_score,
                "cost_score": item.cost_score,
                "optimization_score": item.optimization_score,
            }
            for item in ranked
        ],
        "selected": (
            {
                "sql": ranked[0].sql,
                "reasoning": ranked[0].reasoning,
                "confidence": ranked[0].confidence,
                "schema_score": ranked[0].schema_score,
                "validation_score": ranked[0].validation_score,
                "cost_score": ranked[0].cost_score,
                "optimization_score": ranked[0].optimization_score,
            }
            if ranked
            else None
        ),
        "provider": provider.__class__.__name__.replace("Provider", "").lower() or "heuristic",
    }
