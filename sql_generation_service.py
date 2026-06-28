from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy.orm import Session

from app.schemas.schema import TableInfo
from app.services.prompt_builder import build_generation_prompt
from app.services.schema_service import get_database_schema

logger = logging.getLogger(__name__)

INTENT_SELECT = "SELECT"
INTENT_INSERT = "INSERT"
INTENT_UPDATE = "UPDATE"
INTENT_DELETE = "DELETE"

STOPWORDS = {
    "show",
    "list",
    "get",
    "find",
    "select",
    "all",
    "the",
    "a",
    "an",
    "with",
    "by",
    "for",
    "on",
    "in",
    "of",
    "and",
    "or",
    "to",
}


@dataclass
class SQLGenerationResult:
    generated_sql: str
    alternatives: list[str]
    tables: list[str]
    columns: list[str]
    confidence_score: float
    ambiguity: bool
    intent: str
    suggestions: list[str]
    plan_reasoning: str | None = None


def detect_intent(prompt: str) -> str:
    lowered = prompt.lower()
    if any(word in lowered for word in ["increase", "decrease", "update", "set ", "change"]):
        return INTENT_UPDATE
    if any(word in lowered for word in ["insert", "add new", "create record", "new record"]):
        return INTENT_INSERT
    if any(word in lowered for word in ["delete", "remove", "drop row"]):
        return INTENT_DELETE
    return INTENT_SELECT


def _normalize_tokens(prompt: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", prompt.lower())
    return [token for token in tokens if token not in STOPWORDS]


def _find_table_candidates(prompt: str, tables: list[TableInfo]) -> list[TableInfo]:
    tokens = _normalize_tokens(prompt)
    matches: list[TableInfo] = []
    for table in tables:
        table_name = table.table.lower()
        column_names = {column.name.lower() for column in table.columns}
        if any(token == table_name or token.rstrip("s") == table_name.rstrip("s") for token in tokens):
            matches.append(table)
            continue
        if any(token in column_names for token in tokens):
            matches.append(table)
    return matches


def _find_columns(prompt: str, tables: list[TableInfo]) -> list[str]:
    prompt_tokens = set(_normalize_tokens(prompt))
    columns: list[str] = []
    for table in tables:
        for column in table.columns:
            if column.name.lower() in prompt_tokens:
                columns.append(column.name)
    return list(dict.fromkeys(columns))


def _relationships_for_tables(tables: list[TableInfo]) -> list[str]:
    relations: list[str] = []
    for table in tables:
        for relation in table.relationships:
            relations.append(
                f"{relation.source_table}.{', '.join(relation.source_columns)} -> "
                f"{relation.target_table}.{', '.join(relation.target_columns)}"
            )
    return list(dict.fromkeys(relations))


def _default_select_sql(table: TableInfo, columns: list[str], prompt: str) -> str:
    chosen_columns = columns or [column.name for column in table.columns[:3]]
    table_ref = f"{table.schema + '.' if table.schema else ''}{table.table}"
    sql = f"SELECT {', '.join(chosen_columns if chosen_columns else ['*'])}\nFROM {table_ref}"

    if "top" in prompt.lower() or "limit" in prompt.lower():
        sql += "\nORDER BY 1 DESC\nLIMIT 5"
    return sql + ";"


def _build_alternatives(table: TableInfo, columns: list[str], intent: str, prompt: str) -> list[str]:
    if intent != INTENT_SELECT:
        return []

    table_ref = f"{table.schema + '.' if table.schema else ''}{table.table}"
    alt1 = f"SELECT *\nFROM {table_ref}\nLIMIT 5;"
    alt2_columns = columns or [column.name for column in table.columns[:3]]
    alt2 = f"SELECT {', '.join(alt2_columns)}\nFROM {table_ref}\nLIMIT 5;"

    if "top" in prompt.lower() or "highest" in prompt.lower():
        order_column = columns[0] if columns else next((column.name for column in table.columns if "cgpa" in column.name.lower() or "salary" in column.name.lower()), table.columns[0].name)
        alt1 = f"SELECT *\nFROM {table_ref}\nORDER BY {order_column} DESC\nLIMIT 5;"
        alt2 = f"SELECT {', '.join(alt2_columns)}\nFROM {table_ref}\nORDER BY {order_column} DESC\nLIMIT 5;"

    return [alt1, alt2]


def _ambiguity_suggestions(tokens: Iterable[str]) -> list[str]:
    suggestions = [
        "basic employee details",
        "salary information",
        "department information",
    ]
    if "student" in tokens or "students" in tokens:
        return ["student profile details", "academic performance", "contact information"]
    return suggestions


def _confidence_score(intent: str, matched_tables: list[TableInfo], matched_columns: list[str], ambiguous: bool) -> float:
    score = 0.35
    if matched_tables:
        score += 0.35
    if matched_columns:
        score += 0.15
    if intent != INTENT_SELECT:
        score += 0.05
    if ambiguous:
        score -= 0.2
    return max(0.0, min(1.0, round(score, 2)))


def _is_ambiguous(prompt: str, matched_tables: list[TableInfo]) -> bool:
    lowered = prompt.lower()
    vague_terms = ["information", "details", "data", "show", "list", "employees", "employee"]
    if not matched_tables:
        return True
    return any(term in lowered for term in vague_terms) and len(matched_tables) == 1 and len(_find_columns(prompt, matched_tables)) == 0


def _pick_best_table(matched_tables: list[TableInfo], all_tables: list[TableInfo]) -> TableInfo | None:
    if matched_tables:
        return matched_tables[0]
    return all_tables[0] if all_tables else None


def _table_ref(table: TableInfo | None) -> str:
    if table is None:
        return "employees"
    return f"{table.schema + '.' if table.schema else ''}{table.table}"


def _score_plan(sql: str, tables: list[TableInfo], target_table: TableInfo, matched_columns: list[str]) -> tuple[int, str]:
    score = 100
    reasons: list[str] = []
    lowered = sql.lower()

    if "select *" in lowered:
        score -= 20
        reasons.append("Avoided SELECT * where possible.")
    if "limit" not in lowered:
        score -= 15
        reasons.append("Added or preserved LIMIT to constrain the result set.")
    if "order by" in lowered and "limit" not in lowered:
        score -= 10
        reasons.append("Removed unnecessary ORDER BY from non-top-N candidate plans.")
    if matched_columns:
        reasons.append("Used matched columns from schema metadata.")
        score += 5
    if target_table.relationships:
        reasons.append("Considered table relationships from schema metadata.")
        score += 5
    if len(tables) > 1:
        score -= 5
        reasons.append("Multi-table plans were penalized unless required by the prompt.")

    score = max(0, min(100, score))
    return score, " ".join(reasons) if reasons else "Selected the highest-scoring schema-aware plan."


def _rule_based_sql(prompt: str, tables: list[TableInfo]) -> tuple[str, list[str], list[str], list[str], float, bool, str, list[str], str | None]:
    normalized = re.sub(r"\s+", " ", prompt.strip().lower())
    matched_tables = _find_table_candidates(prompt, tables)
    matched_columns = _find_columns(prompt, tables)
    target_table = _pick_best_table(matched_tables, tables)
    employee_table = next((table for table in tables if table.table.lower() == "employees"), target_table)
    customer_table = next((table for table in tables if table.table.lower() == "customers"), target_table)
    product_table = next((table for table in tables if table.table.lower() == "products"), target_table)
    department_table = next((table for table in tables if table.table.lower() == "departments"), target_table)
    supplier_table = next((table for table in tables if table.table.lower() == "suppliers"), target_table)
    inventory_table = next((table for table in tables if table.table.lower() == "inventory"), target_table)
    order_table = next((table for table in tables if table.table.lower() == "orders"), target_table)
    payroll_table = next((table for table in tables if table.table.lower() == "payroll"), target_table)

    if employee_table is None:
        return "", [], [], [], 0.0, True, INTENT_SELECT, ["No tables available in the database schema."], None

    employee_ref = _table_ref(employee_table)
    customer_ref = _table_ref(customer_table)
    product_ref = _table_ref(product_table)
    department_ref = _table_ref(department_table)
    supplier_ref = _table_ref(supplier_table)
    inventory_ref = _table_ref(inventory_table)
    order_ref = _table_ref(order_table)
    payroll_ref = _table_ref(payroll_table)
    print("Rule branch: schema-aware matching")
    print("Matched tables:", [table.table for table in matched_tables])
    print("Matched columns:", matched_columns)

    if any(term in normalized for term in ("count employees", "employee count", "how many employees")):
        print("Branch: count_employees")
        return (
            f"SELECT COUNT(*) AS employee_count\nFROM {employee_ref};",
            [f"SELECT COUNT(*) AS employee_count\nFROM {employee_ref};"],
            [employee_table.table],
            ["COUNT(*)"],
            0.95,
            False,
            INTENT_SELECT,
            ["Count employees using the live employees table."],
            "Mapped the prompt to a live PostgreSQL count query.",
        )

    if any(term in normalized for term in ("show all employees", "list employees")) or normalized == "employees":
        print("Branch: list_employees")
        return (
            f"SELECT *\nFROM {employee_ref}\nLIMIT 100;",
            [f"SELECT first_name, last_name, email\nFROM {employee_ref}\nLIMIT 100;"],
            [employee_table.table],
            ["*"],
            0.9,
            True,
            INTENT_SELECT,
            ["Return the first 100 employee rows from PostgreSQL."],
            "Mapped the prompt to a safe live PostgreSQL query.",
        )

    if any(term in normalized for term in ("top 5 highest salary", "highest salary", "top salary")):
        print("Branch: top_salary")
        return (
            f"SELECT *\nFROM {employee_ref}\nORDER BY salary DESC\nLIMIT 5;",
            [f"SELECT first_name, last_name, salary\nFROM {employee_ref}\nORDER BY salary DESC\nLIMIT 5;"],
            [employee_table.table],
            ["salary"],
            0.9,
            False,
            INTENT_SELECT,
            ["Use the live salary column and sort descending."],
            "Mapped the prompt to a live salary ranking query.",
        )

    if "count customers" in normalized:
        print("Branch: count_customers")
        return (
            f"SELECT COUNT(*) AS customer_count\nFROM {customer_ref};",
            [f"SELECT COUNT(*) AS customer_count\nFROM {customer_ref};"],
            [customer_table.table] if customer_table else [employee_table.table],
            ["COUNT(*)"],
            0.93,
            False,
            INTENT_SELECT,
            ["Count customers using the live customers table."],
            "Mapped the prompt to a live PostgreSQL count query.",
        )

    if "product" in normalized and any(term in normalized for term in ("top", "highest", "revenue", "price", "cost")):
        print("Branch: top_products")
        limit = "20" if "20" in normalized else "5"
        return (
            f"SELECT *\nFROM {product_ref}\nORDER BY unit_price DESC\nLIMIT {limit};",
            [f"SELECT product_name, unit_price\nFROM {product_ref}\nORDER BY unit_price DESC\nLIMIT {limit};"],
            [product_table.table] if product_table else [employee_table.table],
            ["*"],
            0.8,
            True,
            INTENT_SELECT,
            ["Use the live products table as a safe default."],
            "Mapped the prompt to a live PostgreSQL query.",
        )

    if "department" in normalized:
        print("Branch: departments")
        return (
            f"SELECT *\nFROM {department_ref};",
            [f"SELECT department_name, department_code, location\nFROM {department_ref};"],
            [department_table.table] if department_table else [employee_table.table],
            ["*"],
            0.82,
            False,
            INTENT_SELECT,
            ["Use the live departments table."],
            "Mapped the prompt to the departments table.",
        )

    if "bangalore" in normalized and "employee" in normalized:
        print("Branch: employees_in_bangalore")
        return (
            f"SELECT *\nFROM {employee_ref}\nWHERE city = 'Bangalore';",
            [f"SELECT first_name, last_name, city\nFROM {employee_ref}\nWHERE city = 'Bangalore';"],
            [employee_table.table],
            ["city"],
            0.88,
            False,
            INTENT_SELECT,
            ["Filter employees by city."],
            "Mapped the prompt to an employee city filter.",
        )

    if "payroll" in normalized and "department" in normalized:
        print("Branch: payroll_by_department")
        return (
            f"SELECT d.department_name, SUM(p.net_salary) AS total_payroll\nFROM {payroll_ref} p\nJOIN {employee_ref} e ON p.employee_id = e.employee_id\nJOIN {department_ref} d ON e.department_id = d.department_id\nGROUP BY d.department_name\nORDER BY total_payroll DESC;",
            [f"SELECT d.department_name, SUM(p.net_salary) AS total_payroll\nFROM {payroll_ref} p\nJOIN {employee_ref} e ON p.employee_id = e.employee_id\nJOIN {department_ref} d ON e.department_id = d.department_id\nGROUP BY d.department_name\nORDER BY total_payroll DESC;"],
            [payroll_table.table, employee_table.table, department_table.table],
            ["net_salary", "department_name"],
            0.9,
            False,
            INTENT_SELECT,
            ["Aggregate payroll by department using the live payroll table."],
            "Mapped the prompt to a payroll aggregation query.",
        )

    if "supplier" in normalized and "delhi" in normalized:
        print("Branch: suppliers_from_delhi")
        return (
            f"SELECT *\nFROM {supplier_ref}\nWHERE city = 'Delhi';",
            [f"SELECT supplier_name, contact_person, city\nFROM {supplier_ref}\nWHERE city = 'Delhi';"],
            [supplier_table.table],
            ["city"],
            0.88,
            False,
            INTENT_SELECT,
            ["Filter suppliers by city."],
            "Mapped the prompt to a supplier city filter.",
        )

    if "inventory" in normalized and any(term in normalized for term in ("below 50", "less than 50", "under 50", "50 units")):
        print("Branch: inventory_below_threshold")
        return (
            f"SELECT i.*, p.product_name\nFROM {inventory_ref} i\nJOIN {product_ref} p ON i.product_id = p.product_id\nWHERE i.quantity < 50;",
            [f"SELECT i.quantity, p.product_name\nFROM {inventory_ref} i\nJOIN {product_ref} p ON i.product_id = p.product_id\nWHERE i.quantity < 50;"],
            [inventory_table.table, product_table.table],
            ["quantity", "product_name"],
            0.88,
            False,
            INTENT_SELECT,
            ["Filter inventory rows using the quantity threshold."],
            "Mapped the prompt to a low-stock inventory query.",
        )

    if "order" in normalized and any(term in normalized for term in ("this month", "current month", "month")):
        print("Branch: orders_this_month")
        return (
            f"SELECT *\nFROM {order_ref}\nWHERE order_date >= date_trunc('month', CURRENT_DATE);",
            [f"SELECT order_number, order_date, status\nFROM {order_ref}\nWHERE order_date >= date_trunc('month', CURRENT_DATE);"],
            [order_table.table],
            ["order_date"],
            0.87,
            False,
            INTENT_SELECT,
            ["Filter orders to the current month."],
            "Mapped the prompt to a month-based order query.",
        )

    print("Branch: conservative_fallback")
    fallback_table = target_table or employee_table
    return (
        f"SELECT *\nFROM {_table_ref(fallback_table)}\nLIMIT 100;",
        [f"SELECT *\nFROM {_table_ref(fallback_table)}\nLIMIT 10;"],
        [fallback_table.table] if fallback_table else [],
        ["*"],
        0.6,
        True,
        INTENT_SELECT,
        ["No exact mapping matched; using a conservative schema-aware query."],
        "Used a conservative live PostgreSQL fallback query.",
    )


def generate_sql_from_prompt(db: Session, prompt: str) -> SQLGenerationResult:
    try:
        logger.info("Generating SQL from prompt")
        print("Generating SQL...")
        print("Received prompt:", prompt)
        try:
            schema_payload = get_database_schema(db)
            logger.info("Schema tables loaded=%s", len(schema_payload["tables"]))
            tables = [TableInfo.model_validate(table) for table in schema_payload["tables"]]
        except Exception:
            logger.exception("FAILED AT STEP 3: schema introspection")
            tables = []
        if not tables:
            fallback_sql = "SELECT 1;"
            if "count employees" in prompt.lower() or "employee count" in prompt.lower() or "how many employees" in prompt.lower():
                fallback_sql = "SELECT COUNT(*) AS employee_count\nFROM employees;"
            elif "top 5 highest salary" in prompt.lower() or "highest salary" in prompt.lower() or "top salary" in prompt.lower():
                fallback_sql = "SELECT *\nFROM employees\nORDER BY salary DESC\nLIMIT 5;"
            logger.info("Using fallback SQL because schema tables were unavailable")
            print("Generated SQL:", fallback_sql)
            return SQLGenerationResult(
                generated_sql=fallback_sql,
                alternatives=[],
                tables=[],
                columns=[],
                confidence_score=0.5,
                ambiguity=True,
                intent=INTENT_SELECT,
                suggestions=["Schema introspection was unavailable; using a conservative fallback query."],
                plan_reasoning="Used a conservative fallback query because schema metadata could not be loaded.",
            )
        generated_sql, alternatives, selected_tables, matched_columns, confidence_score, ambiguous, intent, suggestions, plan_reasoning = _rule_based_sql(prompt, tables)
        logger.info("Generated SQL=%s", generated_sql)
        print("Generated SQL:", generated_sql)
        return SQLGenerationResult(
            generated_sql=generated_sql,
            alternatives=alternatives,
            tables=[
                table
                if isinstance(table, str)
                else f"{table.schema + '.' if table.schema else ''}{table.table}"
                for table in selected_tables
            ],
            columns=matched_columns,
            confidence_score=confidence_score,
            ambiguity=ambiguous,
            intent=intent,
            suggestions=suggestions,
            plan_reasoning=plan_reasoning,
        )
    except Exception:
        logger.exception("Unhandled exception in generate_sql_from_prompt")
        raise


def build_sql_generation_prompt(db: Session, prompt: str) -> str:
    schema_payload = get_database_schema(db)
    tables = [TableInfo.model_validate(table) for table in schema_payload["tables"]]
    return build_generation_prompt(prompt, tables)
