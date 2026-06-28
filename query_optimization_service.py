from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class OptimizationResult:
    optimized_sql: str
    optimization_score: int
    issues: list[str]
    recommendations: list[str]


def _has_limit(sql: str) -> bool:
    return bool(re.search(r"\blimit\b", sql, flags=re.IGNORECASE))


def optimize_sql(sql: str) -> OptimizationResult:
    issues: list[str] = []
    recommendations: list[str] = []
    score = 100
    optimized_sql = sql.strip()

    if re.search(r"select\s+\*", sql, flags=re.IGNORECASE):
        issues.append("SELECT * detected")
        recommendations.append("Select only required columns.")
        score -= 20

    if re.search(r"\border\s+by\b", sql, flags=re.IGNORECASE) and not re.search(r"\blimit\b", sql, flags=re.IGNORECASE):
        issues.append("ORDER BY without LIMIT")
        recommendations.append("Remove ORDER BY unless sorted output is required.")
        score -= 10

    if not _has_limit(sql) and re.search(r"^\s*select\b", sql, flags=re.IGNORECASE):
        issues.append("Missing LIMIT")
        recommendations.append("Add LIMIT to constrain large result sets.")
        score -= 15

    if re.search(r"\bjoin\b.*\bjoin\b", sql, flags=re.IGNORECASE | re.DOTALL):
        issues.append("Potential redundant joins")
        recommendations.append("Review joins for duplication and unused tables.")
        score -= 10

    if re.search(r"\bwhere\b.*(\bor\b|\band\b).*(\bor\b|\band\b)", sql, flags=re.IGNORECASE | re.DOTALL):
        issues.append("Possibly inefficient predicates")
        recommendations.append("Simplify repeated or broad predicates.")
        score -= 10

    if re.search(r"\b(seq scan|full table scan)\b", sql, flags=re.IGNORECASE):
        issues.append("Full table scan risk")
        recommendations.append("Add filtering columns or indexes where appropriate.")
        score -= 20

    if re.search(r"\bWHERE\b", sql, flags=re.IGNORECASE) and re.search(r"\bLIKE\s+'%.*%'\b", sql, flags=re.IGNORECASE):
        issues.append("Leading-wildcard predicate")
        recommendations.append("Avoid leading wildcards where possible; consider indexed predicates.")
        score -= 10

    score = max(0, min(100, score))
    return OptimizationResult(
        optimized_sql=optimized_sql,
        optimization_score=score,
        issues=list(dict.fromkeys(issues)),
        recommendations=list(dict.fromkeys(recommendations)),
    )
