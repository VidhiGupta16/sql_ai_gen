from __future__ import annotations

from app.schemas.schema import TableInfo


def build_schema_context(tables: list[TableInfo]) -> str:
    lines: list[str] = []
    for table in tables:
        column_bits = []
        for column in table.columns:
            flags = []
            if column.primary_key:
                flags.append("pk")
            if column.foreign_key:
                flags.append(f"fk->{column.foreign_key}")
            flag_text = f" ({', '.join(flags)})" if flags else ""
            column_bits.append(f"{column.name}:{column.type}{flag_text}")
        lines.append(
            f"Table {table.schema + '.' if table.schema else ''}{table.table} | "
            f"columns: {', '.join(column_bits)}"
        )
    return "\n".join(lines)


def build_generation_prompt(user_prompt: str, tables: list[TableInfo]) -> str:
    schema_context = build_schema_context(tables)
    return (
        "You are a SQL generation assistant.\n"
        "Generate safe SQL based on the provided schema context.\n"
        "Return JSON with keys: generated_sql, alternatives, tables, columns, confidence_score, ambiguity, intent, suggestions.\n\n"
        f"User prompt: {user_prompt}\n\n"
        f"Schema context:\n{schema_context}"
    )
