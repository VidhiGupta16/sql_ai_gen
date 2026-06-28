from __future__ import annotations


def build_metadata_only_prompt(user_prompt: str, virtual_schema: list[dict]) -> str:
    schema_lines: list[str] = []
    for obj in virtual_schema:
        columns = ", ".join(
            f"{column['virtual_name']}:{column['type']}"
            + (" pk" if column.get("primary_key") else "")
            + (f" fk->{column['foreign_key']}" if column.get("foreign_key") else "")
            for column in obj["columns"]
        )
        schema_lines.append(
            f"{obj['virtual_type']} {obj['virtual_name']} -> columns: {columns}"
        )

    return (
        "You are an enterprise SQL planning assistant.\n"
        "Use only metadata. Never use rows or PII.\n"
        "Return ranked candidate SQL plans with reasoning and confidence.\n\n"
        f"User prompt: {user_prompt}\n\n"
        "Virtual schema:\n"
        + "\n".join(schema_lines)
    )
