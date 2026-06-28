from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.query_impact_service import analyze_query_impact
from app.services.query_validation_service import validate_query

logger = logging.getLogger(__name__)

ALLOWED_OPERATIONS = {"SELECT", "INSERT", "UPDATE", "DELETE"}
NEVER_EXECUTE_PATTERNS = (
    r"\bDROP\s+DATABASE\b",
    r"\bDROP\s+TABLE\b",
    r"\bTRUNCATE\b",
    r"\bALTER\s+DATABASE\b",
    r"\bALTER\s+SYSTEM\b",
)


@dataclass
class QueryExecutionResult:
    success: bool
    execution_time_ms: int
    rows_returned: int
    rows_affected: int
    columns: list[str]
    data: list[dict]
    message: str
    operation_type: str
    blocked: bool
    risk_level: str
    validation_errors: list[str]
    warnings: list[str]


def _extract_operation(sql: str) -> str:
    match = re.match(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", sql, flags=re.IGNORECASE)
    return match.group(1).upper() if match else "UNKNOWN"


def _safe_to_execute(sql: str) -> bool:
    return not any(re.search(pattern, sql, flags=re.IGNORECASE) for pattern in NEVER_EXECUTE_PATTERNS)


def _paginate_select(sql: str, page: int, page_size: int) -> str:
    offset = (page - 1) * page_size
    if re.search(r"\blimit\b", sql, flags=re.IGNORECASE) or re.search(r"\boffset\b", sql, flags=re.IGNORECASE):
        return sql.rstrip(";")
    return f"{sql.rstrip(';')}\nLIMIT {page_size} OFFSET {offset}"


def execute_query(
    db: Session,
    sql: str,
    page: int = 1,
    page_size: int = 50,
    approval: bool = False,
    tenant_id: str | None = None,
) -> QueryExecutionResult:
    """
    Execute a SQL statement only after validation and impact analysis.

    The execution flow is intentionally staged so future transaction support can reuse
    the same preflight checks before opening write transactions.
    """

    validation = None
    impact = None
    start = perf_counter()

    try:
        print("========================")
        print("Generated SQL:", sql)
        print("sql entering execute_query():", sql)
        print("Executing SQL:", sql)
        logger.info("Executing SQL statement")
        validation = validate_query(sql)
        logger.info("Validation complete valid=%s blocked=%s", validation.valid, validation.blocked)
        impact = analyze_query_impact(db, sql)
        logger.info("Impact analysis complete risk=%s", impact.risk_level)
        operation = _extract_operation(sql)

        # Hard safety gate: never allow destructive schema/database statements.
        if not _safe_to_execute(sql):
            return QueryExecutionResult(
                success=False,
                execution_time_ms=0,
                rows_returned=0,
                rows_affected=0,
                columns=[],
                data=[],
                message="Query blocked by safety policy.",
                operation_type=operation,
                blocked=True,
                risk_level="HIGH",
                validation_errors=validation.errors,
                warnings=validation.warnings + impact.warnings,
            )

        # Supported operations only. This keeps the engine narrow and safe for demo usage.
        if operation not in ALLOWED_OPERATIONS:
            return QueryExecutionResult(
                success=False,
                execution_time_ms=0,
                rows_returned=0,
                rows_affected=0,
                columns=[],
                data=[],
                message="Unsupported SQL operation.",
                operation_type=operation,
                blocked=True,
                risk_level="HIGH",
                validation_errors=validation.errors + ["Unsupported SQL operation."],
                warnings=validation.warnings + impact.warnings,
            )

        # Respect upstream validation and impact signals.
        if validation.blocked or impact.risk_level == "HIGH":
            if not approval:
                return QueryExecutionResult(
                    success=False,
                    execution_time_ms=0,
                    rows_returned=0,
                    rows_affected=0,
                    columns=[],
                    data=[],
                    message="Execution blocked due to validation or high risk.",
                    operation_type=operation,
                    blocked=True,
                    risk_level=impact.risk_level if impact.risk_level == "HIGH" else validation.risk_level,
                    validation_errors=validation.errors,
                    warnings=validation.warnings + impact.warnings,
                )

        if operation == "SELECT":
            paginated_sql = _paginate_select(sql, page=page, page_size=page_size)
            print("sql after _paginate_select():", paginated_sql)
            print("Executing paginated SQL:", paginated_sql)
            print("Executed SQL:", paginated_sql)
            print("final SQL sent into db.execute(text(...)):", paginated_sql)
            result = db.execute(text(paginated_sql))
            columns = list(result.keys())
            rows = [dict(row._mapping) for row in result.fetchall()]
            print("Rows fetched:", len(rows))
            print("Returned columns:", columns)
            print("========================")
            logger.info("Rows fetched=%s", len(rows))

            execution_time_ms = int((perf_counter() - start) * 1000)
            return QueryExecutionResult(
                success=True,
                execution_time_ms=execution_time_ms,
                rows_returned=len(rows),
                rows_affected=0,
                columns=columns,
                data=rows,
                message="Query executed successfully",
                operation_type=operation,
                blocked=False,
                risk_level=impact.risk_level,
                validation_errors=validation.errors,
                warnings=validation.warnings + impact.warnings,
            )

        # Non-SELECT statements are executed in a transaction boundary and committed.
        result = db.execute(text(sql))
        db.commit()
        rows_affected = result.rowcount if result.rowcount is not None else 0
        logger.info("Rows affected=%s", rows_affected)
        execution_time_ms = int((perf_counter() - start) * 1000)
        return QueryExecutionResult(
            success=True,
            execution_time_ms=execution_time_ms,
            rows_returned=0,
            rows_affected=rows_affected,
            columns=[],
            data=[],
            message="Query executed successfully",
            operation_type=operation,
            blocked=False,
            risk_level=impact.risk_level,
            validation_errors=validation.errors,
            warnings=validation.warnings + impact.warnings,
        )
    except Exception as exc:
        logger.exception("Unhandled exception in execute_query")
        print("Execution exception:", exc)
        db.rollback()
        execution_time_ms = int((perf_counter() - start) * 1000)
        validation_errors = validation.errors if validation else [str(exc)]
        warnings = []
        if validation:
            warnings.extend(validation.warnings)
        if impact:
            warnings.extend(impact.warnings)
        return QueryExecutionResult(
            success=False,
            execution_time_ms=execution_time_ms,
            rows_returned=0,
            rows_affected=0,
            columns=[],
            data=[],
            message=f"Query execution failed: {exc}",
            operation_type=operation,
            blocked=True,
            risk_level="HIGH",
            validation_errors=validation_errors,
            warnings=warnings,
        )
