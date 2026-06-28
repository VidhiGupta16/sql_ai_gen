from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"


class ValidationRule(Protocol):
    """Extensible contract for future database-specific validation rules."""

    def evaluate(self, sql: str) -> tuple[list[str], list[str], list[str], bool, str | None]:
        ...


@dataclass
class QueryValidationResult:
    valid: bool
    risk_level: str
    errors: list[str]
    warnings: list[str]
    optimization_suggestions: list[str]
    blocked: bool


class CoreSQLValidationRule:
    """Base validation rule covering syntax shape, safety, and quality heuristics.

    This rule is intentionally generic so future subclasses can specialize behavior
    per dialect (PostgreSQL, MySQL, etc.) without changing the API contract.
    """

    unsupported_patterns = (
        r"\bDROP\s+DATABASE\b",
        r"\bDROP\s+TABLE\b",
        r"\bTRUNCATE\b",
        r"\bALTER\s+DATABASE\b",
        r"\bALTER\s+SYSTEM\b",
    )

    def evaluate(self, sql: str) -> tuple[list[str], list[str], list[str], bool, str | None]:
        errors: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []
        blocked = False
        risk_level = RISK_LOW

        normalized = sql.strip().rstrip(";")
        lowered = normalized.lower()

        # Basic syntax-shape validation:
        # we only accept core CRUD statements for this module.
        if not re.match(r"^(select|insert|update|delete)\b", lowered):
            errors.append("Unsupported SQL statement detected.")
            blocked = True
            risk_level = RISK_HIGH
            return errors, warnings, suggestions, blocked, risk_level

        # Clause presence checks by statement type.
        if lowered.startswith("insert") and not re.search(r"\binto\b", lowered):
            errors.append("INSERT statement is missing INTO clause.")
            blocked = True
            risk_level = RISK_HIGH
        if lowered.startswith("update") and not re.search(r"\bset\b", lowered):
            errors.append("UPDATE statement is missing SET clause.")
            blocked = True
            risk_level = RISK_HIGH
        if lowered.startswith("delete") and not re.search(r"\bfrom\b", lowered):
            errors.append("DELETE statement is missing FROM clause.")
            blocked = True
            risk_level = RISK_HIGH

        # Hard safety rules: these statements should be blocked outright.
        if re.search(r"\bDROP\s+DATABASE\b", normalized, flags=re.IGNORECASE):
            errors.append("DROP DATABASE is blocked.")
            blocked = True
            risk_level = RISK_HIGH
        if re.search(r"\bDROP\s+TABLE\b", normalized, flags=re.IGNORECASE):
            errors.append("DROP TABLE is blocked.")
            blocked = True
            risk_level = RISK_HIGH
        if re.search(r"\bTRUNCATE\b", normalized, flags=re.IGNORECASE):
            errors.append("TRUNCATE is blocked.")
            blocked = True
            risk_level = RISK_HIGH
        if re.search(r"\bALTER\s+DATABASE\b", normalized, flags=re.IGNORECASE):
            errors.append("ALTER DATABASE is blocked.")
            blocked = True
            risk_level = RISK_HIGH
        if re.search(r"\bALTER\s+SYSTEM\b", normalized, flags=re.IGNORECASE):
            errors.append("ALTER SYSTEM is blocked.")
            blocked = True
            risk_level = RISK_HIGH

        # Detect malformed joins and obviously broken statements by looking for JOIN without ON/USING.
        join_matches = re.finditer(r"\b(?:LEFT|RIGHT|INNER|OUTER|FULL|CROSS)?\s*JOIN\b", normalized, flags=re.IGNORECASE)
        for match in join_matches:
            join_tail = normalized[match.end():]
            if re.search(r"\b(LEFT|RIGHT|INNER|OUTER|FULL)\s+JOIN\b", match.group(0), flags=re.IGNORECASE) and not re.search(
                r"\b(ON|USING)\b", join_tail, flags=re.IGNORECASE
            ):
                warnings.append("Possible malformed JOIN detected.")
                risk_level = max(risk_level, RISK_MEDIUM, key=lambda v: {"LOW": 0, "MEDIUM": 1, "HIGH": 2}[v])

        # Update/Delete without WHERE should be treated as high risk because they can affect all rows.
        if lowered.startswith("update") and not re.search(r"\bwhere\b", lowered):
            errors.append("UPDATE without WHERE is unsafe.")
            blocked = True
            risk_level = RISK_HIGH
        if lowered.startswith("delete") and not re.search(r"\bwhere\b", lowered):
            errors.append("DELETE without WHERE is unsafe.")
            blocked = True
            risk_level = RISK_HIGH

        # Cross joins without conditions can blow up row counts.
        if re.search(r"\bcross\s+join\b", lowered) and not re.search(r"\bon\b", lowered):
            warnings.append("CROSS JOIN without a condition may produce a large result set.")
            risk_level = RISK_MEDIUM if not blocked else risk_level

        # Query quality heuristics.
        if re.search(r"^\s*select\s+\*\s+from\b", normalized, flags=re.IGNORECASE | re.DOTALL):
            warnings.append("SELECT * detected")
            suggestions.append("Specify required columns explicitly")
            risk_level = RISK_MEDIUM if risk_level != RISK_HIGH else risk_level

        if lowered.startswith("select") and not re.search(r"\blimit\b", lowered):
            warnings.append("Missing LIMIT for a potentially large result set.")
            suggestions.append("Add LIMIT to constrain the result set when appropriate")
            risk_level = RISK_MEDIUM if risk_level != RISK_HIGH else risk_level

        if lowered.startswith("select") and re.search(r"\border\s+by\b", lowered) and not re.search(r"\b(cgpa|created_at|updated_at|name|id)\b", lowered):
            warnings.append("ORDER BY may be unnecessary or expensive without a strong use case.")
            suggestions.append("Remove ORDER BY if the sort order is not required")

        if lowered.startswith("select") and re.search(r"\bwhere\b.*\b(and|or)\b.*\b(and|or)\b", lowered):
            warnings.append("Redundant or repeated conditions may be present.")
            suggestions.append("Review WHERE conditions for redundancy")

        # Heuristic suggestions for large-table scans. We cannot know table size here without metadata,
        # so this is designed to be extended with table statistics in future rules.
        if lowered.startswith("select") and re.search(r"\bfrom\b", lowered) and not re.search(r"\bwhere\b", lowered):
            warnings.append("Missing WHERE filter on a large table may cause a broad scan.")
            suggestions.append("Add filtering conditions if appropriate")
            risk_level = RISK_MEDIUM if risk_level != RISK_HIGH else risk_level

        # Basic invalid syntax detection:
        # These checks are intentionally light-weight so the validator can run before execution
        # without requiring a database round trip.
        if lowered.startswith("select") and not re.search(r"\bfrom\b", lowered):
            errors.append("SELECT statement is missing a FROM clause.")
            blocked = True
            risk_level = RISK_HIGH

        return errors, warnings, suggestions, blocked, risk_level


def _risk_rank(level: str) -> int:
    return {"LOW": 0, "MEDIUM": 1, "HIGH": 2}.get(level, 0)


def validate_query(sql: str) -> QueryValidationResult:
    # The core rule is the first layer of a future rule chain.
    rule = CoreSQLValidationRule()
    errors, warnings, suggestions, blocked, risk_level = rule.evaluate(sql)

    valid = not errors and not blocked

    # If the query is syntactically valid but includes non-blocking advisories,
    # we keep it valid and surface the risk level for downstream consumers.
    if warnings and _risk_rank(risk_level) < _risk_rank(RISK_MEDIUM):
        risk_level = RISK_MEDIUM

    return QueryValidationResult(
        valid=valid,
        risk_level=risk_level,
        errors=errors,
        warnings=warnings,
        optimization_suggestions=list(dict.fromkeys(suggestions)),
        blocked=blocked,
    )
