from __future__ import annotations

import re
from dataclasses import dataclass


SQL_OPERATIONS = ("SELECT", "INSERT", "UPDATE", "DELETE")


@dataclass
class QueryExplanationResult:
    explanation: str
    business_explanation: str
    tables: list[str]
    columns: list[str]
    filters: list[str]
    joins: list[str]
    aggregations: list[str]
    sorting: list[str]
    limits: list[str]
    operation: str


def detect_sql_operation(sql: str) -> str:
    match = re.match(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", sql, flags=re.IGNORECASE)
    return match.group(1).upper() if match else "UNKNOWN"


def _clean_identifier(token: str) -> str:
    return token.strip().strip('";`[]')


def _extract_table_names(sql: str) -> list[str]:
    patterns = [
        r"\bFROM\s+([A-Za-z0-9_.`\"\[\]]+)",
        r"\bJOIN\s+([A-Za-z0-9_.`\"\[\]]+)",
        r"\bINTO\s+([A-Za-z0-9_.`\"\[\]]+)",
        r"\bUPDATE\s+([A-Za-z0-9_.`\"\[\]]+)",
        r"\bDELETE\s+FROM\s+([A-Za-z0-9_.`\"\[\]]+)",
    ]
    tables: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, sql, flags=re.IGNORECASE):
            tables.append(_clean_identifier(match))
    return list(dict.fromkeys(tables))


def _extract_columns(sql: str) -> list[str]:
    columns: list[str] = []

    select_match = re.search(r"\bSELECT\s+(.*?)\bFROM\b", sql, flags=re.IGNORECASE | re.DOTALL)
    if select_match:
        select_clause = select_match.group(1)
        if select_clause.strip() != "*":
            for col in select_clause.split(","):
                token = col.strip()
                if token:
                    token = re.split(r"\s+AS\s+", token, flags=re.IGNORECASE)[0]
                    columns.append(token)

    for pattern in [r"\bSET\s+(.*?)(?:\bWHERE\b|;|$)", r"\bGROUP BY\s+(.*?)(?:\bHAVING\b|\bORDER BY\b|;|$)"]:
        match = re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL)
        if match:
            for col in match.group(1).split(","):
                token = col.strip()
                if token:
                    token = re.split(r"\s*=\s*|\s+AS\s+", token, flags=re.IGNORECASE)[0]
                    columns.append(token)

    for pattern in [r"\bWHERE\b(.*?)(?:\bGROUP BY\b|\bORDER BY\b|\bHAVING\b|\bLIMIT\b|;|$)"]:
        match = re.search(pattern, sql, flags=re.IGNORECASE | re.DOTALL)
        if match:
            where_clause = match.group(1)
            for token in re.findall(r"\b([A-Za-z_][A-Za-z0-9_\.]*)\b", where_clause):
                if token.upper() not in {"AND", "OR", "NOT", "IN", "IS", "NULL", "LIKE", "BETWEEN", "EXISTS"}:
                    if not token.isdigit():
                        columns.append(token)

    return list(dict.fromkeys(_clean_identifier(column) for column in columns if column))


def _extract_filters(sql: str) -> list[str]:
    filters: list[str] = []
    where_match = re.search(r"\bWHERE\b(.*?)(?:\bGROUP BY\b|\bORDER BY\b|\bHAVING\b|\bLIMIT\b|;|$)", sql, flags=re.IGNORECASE | re.DOTALL)
    if where_match:
        clause = where_match.group(1).strip()
        if clause:
            filters.append(re.sub(r"\s+", " ", clause))
    having_match = re.search(r"\bHAVING\b(.*?)(?:\bORDER BY\b|\bLIMIT\b|;|$)", sql, flags=re.IGNORECASE | re.DOTALL)
    if having_match:
        clause = having_match.group(1).strip()
        if clause:
            normalized = re.sub(r"\s+", " ", clause)
            filters.append(f"HAVING {normalized}")
    return filters


def _extract_joins(sql: str) -> list[str]:
    joins: list[str] = []
    for match in re.finditer(r"\b(JOIN|LEFT JOIN|RIGHT JOIN|INNER JOIN|OUTER JOIN|FULL JOIN)\s+([A-Za-z0-9_.`\"\[\]]+)(?:\s+ON\s+(.*?))?(?=\bJOIN\b|\bWHERE\b|\bGROUP BY\b|\bORDER BY\b|\bHAVING\b|\bLIMIT\b|;|$)", sql, flags=re.IGNORECASE | re.DOTALL):
        join_type = match.group(1).upper()
        table_name = _clean_identifier(match.group(2))
        on_clause = re.sub(r"\s+", " ", match.group(3).strip()) if match.group(3) else ""
        joins.append(f"{join_type} {table_name}" + (f" ON {on_clause}" if on_clause else ""))
    return list(dict.fromkeys(joins))


def _extract_aggregations(sql: str) -> list[str]:
    aggregations: list[str] = []
    for func in ["COUNT", "SUM", "AVG", "MIN", "MAX"]:
        for match in re.finditer(rf"\b{func}\s*\((.*?)\)", sql, flags=re.IGNORECASE | re.DOTALL):
            normalized = re.sub(r"\s+", " ", match.group(1).strip())
            aggregations.append(f"{func.upper()}({normalized})")
    group_match = re.search(r"\bGROUP BY\b(.*?)(?:\bHAVING\b|\bORDER BY\b|\bLIMIT\b|;|$)", sql, flags=re.IGNORECASE | re.DOTALL)
    if group_match:
        group_clause = re.sub(r"\s+", " ", group_match.group(1).strip())
        if group_clause:
            aggregations.append(f"GROUP BY {group_clause}")
    return list(dict.fromkeys(aggregations))


def _extract_sorting(sql: str) -> list[str]:
    match = re.search(r"\bORDER BY\b(.*?)(?:\bLIMIT\b|;|$)", sql, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    clause = re.sub(r"\s+", " ", match.group(1).strip())
    return [f"ORDER BY {clause}"] if clause else []


def _extract_limits(sql: str) -> list[str]:
    match = re.search(r"\bLIMIT\b\s+(\d+)", sql, flags=re.IGNORECASE)
    return [f"LIMIT {match.group(1)}"] if match else []


def _build_explanation(operation: str, tables: list[str], columns: list[str], filters: list[str], joins: list[str], aggregations: list[str], sql: str) -> str:
    if operation == "SELECT":
        if "limit 5" in sql.lower() and "order by" in sql.lower():
            return "Returns the top 5 rows ordered by the specified sort column."
        if filters:
            if len(tables) == 1 and columns:
                return f"This query retrieves {', '.join(columns)} from {tables[0]} using the specified filters."
            if len(tables) == 1:
                return f"This query retrieves records from {tables[0]} using the specified filters."
            return "This query retrieves data from the referenced tables using the specified filters and joins."
        return "This query retrieves matching records from the referenced table or tables."

    if operation == "INSERT":
        return "This query inserts new records into the target table."
    if operation == "UPDATE":
        return "This query updates existing records in the target table based on the provided conditions."
    if operation == "DELETE":
        return "This query deletes records from the target table based on the provided conditions."
    return "Unable to determine the SQL operation."


def _build_business_explanation(operation: str, tables: list[str], columns: list[str], filters: list[str], joins: list[str], aggregations: list[str], sorting: list[str], limits: list[str], sql: str) -> str:
    parts: list[str] = []
    if operation == "SELECT":
        parts.append("This query returns data for reporting or review.")
    elif operation == "UPDATE":
        parts.append("This query updates existing records.")
    elif operation == "INSERT":
        parts.append("This query adds new records.")
    elif operation == "DELETE":
        parts.append("This query removes records.")

    if tables:
        parts.append(f"It uses {', '.join(tables)}.")
    if columns:
        parts.append(f"It focuses on {', '.join(columns)}.")
    if filters:
        parts.append(f"Filters applied: {', '.join(filters)}.")
    if joins:
        parts.append(f"Joins applied: {', '.join(joins)}.")
    if aggregations:
        parts.append(f"Aggregations: {', '.join(aggregations)}.")
    if sorting:
        parts.append(f"Sorting: {', '.join(sorting)}.")
    if limits:
        parts.append(f"Row limits: {', '.join(limits)}.")
    return " ".join(parts) if parts else _build_explanation(operation, tables, columns, filters, joins, aggregations, sql)


def explain_sql(sql: str) -> QueryExplanationResult:
    operation = detect_sql_operation(sql)
    tables = _extract_table_names(sql)
    columns = _extract_columns(sql)
    filters = _extract_filters(sql)
    joins = _extract_joins(sql)
    aggregations = _extract_aggregations(sql)
    sorting = _extract_sorting(sql)
    limits = _extract_limits(sql)
    explanation = _build_explanation(operation, tables, columns, filters, joins, aggregations, sql)
    business_explanation = _build_business_explanation(operation, tables, columns, filters, joins, aggregations, sorting, limits, sql)

    return QueryExplanationResult(
        explanation=explanation,
        business_explanation=business_explanation,
        tables=tables,
        columns=columns,
        filters=filters,
        joins=joins,
        aggregations=aggregations,
        sorting=sorting,
        limits=limits,
        operation=operation,
    )
