from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.query_explanation_service import detect_sql_operation


RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"


@dataclass
class QueryImpactResult:
    estimated_rows: int
    estimated_cost: float
    risk_level: str
    warnings: list[str]
    tables_affected: list[str]
    operation_type: str


def _clean_identifier(identifier: str) -> str:
    return identifier.strip().strip('";`[]')


def _extract_tables(sql: str) -> list[str]:
    patterns = [
        r"\bFROM\s+([A-Za-z0-9_.`\"\[\]]+)",
        r"\bJOIN\s+([A-Za-z0-9_.`\"\[\]]+)",
        r"\bINTO\s+([A-Za-z0-9_.`\"\[\]]+)",
        r"\bUPDATE\s+([A-Za-z0-9_.`\"\[\]]+)",
        r"\bDELETE\s+FROM\s+([A-Za-z0-9_.`\"\[\]]+)",
        r"\bTRUNCATE\s+TABLE\s+([A-Za-z0-9_.`\"\[\]]+)",
        r"\bDROP\s+TABLE\s+([A-Za-z0-9_.`\"\[\]]+)",
    ]
    tables: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, sql, flags=re.IGNORECASE):
            tables.append(_clean_identifier(match))
    return list(dict.fromkeys(tables))


def _has_where_clause(sql: str) -> bool:
    return bool(re.search(r"\bWHERE\b", sql, flags=re.IGNORECASE))


def _has_select_star(sql: str) -> bool:
    return bool(re.search(r"^\s*SELECT\s+\*\s+FROM\b", sql, flags=re.IGNORECASE | re.DOTALL))


def _detect_risk(sql: str, operation: str, estimated_rows: int, has_index_signal: bool, is_full_scan: bool) -> tuple[str, list[str]]:
    warnings: list[str] = []

    # HIGH RISK: unsafe data definition or destructive data changes.
    if re.search(r"\b(TRUNCATE|DROP\s+TABLE)\b", sql, flags=re.IGNORECASE):
        warnings.append("Dangerous DDL/DML detected.")
        return RISK_HIGH, warnings

    # HIGH RISK: UPDATE/DELETE without a WHERE clause can affect every row.
    if operation in {"UPDATE", "DELETE"} and not _has_where_clause(sql):
        warnings.append(f"{operation} without WHERE clause can affect all rows.")
        return RISK_HIGH, warnings

    # MEDIUM RISK signals begin here.
    # SELECT * often pulls unneeded columns and can hurt performance.
    if _has_select_star(sql):
        warnings.append("SELECT * may fetch unnecessary columns.")

    # Full scans are often acceptable for small tables but should be flagged.
    if is_full_scan:
        warnings.append("Query may trigger a full table scan.")

    # If we cannot detect an index signal on a read query, flag it as potentially expensive.
    if not has_index_signal and operation == "SELECT":
        warnings.append("No obvious index usage detected.")

    if warnings:
        # A small filtered SELECT can still be considered low risk even if it has advisory warnings.
        if operation == "SELECT" and estimated_rows <= 1000 and not is_full_scan:
            return RISK_LOW, warnings
        return RISK_MEDIUM, warnings

    # LOW RISK: well-filtered reads with no concerning signals.
    return RISK_LOW, warnings


def _estimate_rows_from_explain(db: Session, sql: str) -> tuple[int, float, bool, bool]:
    """
    Try to use EXPLAIN when the target database supports it.
    The heuristic falls back gracefully if the statement cannot be explained.
    """

    estimated_rows = 25
    estimated_cost = 12.5
    has_index_signal = False
    is_full_scan = False

    try:
        result = db.execute(text(f"EXPLAIN {sql}"))
        plan_text = "\n".join(str(row[0]) for row in result.fetchall())
        plan_lower = plan_text.lower()

        # Basic plan parsing: extract a row estimate if present.
        row_match = re.search(r"rows=(\d+)", plan_lower)
        if row_match:
            estimated_rows = max(1, int(row_match.group(1)))

        cost_match = re.search(r"cost=([0-9.]+)\.\.([0-9.]+)", plan_lower)
        if cost_match:
            estimated_cost = float(cost_match.group(2))

        has_index_signal = "index scan" in plan_lower or "bitmap index scan" in plan_lower
        is_full_scan = "seq scan" in plan_lower or "full table scan" in plan_lower
    except Exception:
        # If the database does not allow EXPLAIN for a statement or the dialect differs,
        # we keep the heuristic defaults. This keeps the service safe and portable.
        pass

    return estimated_rows, estimated_cost, has_index_signal, is_full_scan


def _heuristic_adjustments(sql: str, operation: str, estimated_rows: int, estimated_cost: float) -> tuple[int, float]:
    lowered = sql.lower()

    if operation == "SELECT":
        if "limit 5" in lowered:
            estimated_rows = min(estimated_rows, 5)
            estimated_cost = min(estimated_cost, 12.5)
        elif "limit" in lowered:
            estimated_rows = min(estimated_rows, 25)
            estimated_cost = min(estimated_cost, 20.0)

        if "join" in lowered:
            estimated_cost *= 1.5
        if "group by" in lowered or "having" in lowered:
            estimated_cost *= 1.25
        if "*" in lowered:
            estimated_cost *= 1.1

    if operation in {"UPDATE", "DELETE"}:
        if _has_where_clause(sql):
            estimated_rows = max(1, estimated_rows // 2)
            estimated_cost *= 1.4
        else:
            estimated_rows = max(estimated_rows, 1000)
            estimated_cost *= 2.0

    if operation == "INSERT":
        estimated_cost *= 1.1

    return max(1, estimated_rows), round(max(1.0, estimated_cost), 2)


def analyze_query_impact(db: Session, sql: str) -> QueryImpactResult:
    operation = detect_sql_operation(sql)
    tables = _extract_tables(sql)

    estimated_rows, estimated_cost, has_index_signal, is_full_scan = _estimate_rows_from_explain(db, sql)
    estimated_rows, estimated_cost = _heuristic_adjustments(sql, operation, estimated_rows, estimated_cost)
    risk_level, warnings = _detect_risk(sql, operation, estimated_rows, has_index_signal, is_full_scan)

    # Fine-tune risk for obviously small, well-filtered reads.
    if operation == "SELECT" and _has_where_clause(sql) and not _has_select_star(sql) and estimated_rows <= 100:
        risk_level = RISK_LOW

    return QueryImpactResult(
        estimated_rows=estimated_rows,
        estimated_cost=estimated_cost,
        risk_level=risk_level,
        warnings=warnings,
        tables_affected=tables,
        operation_type=operation,
    )
