from __future__ import annotations

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.schemas.schema import (
    ColumnInfo,
    DatabaseSchemaResponse,
    ForeignKeyInfo,
    IndexInfo,
    PrimaryKeyInfo,
    RelationshipInfo,
    TableInfo,
)

SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "mysql", "performance_schema", "sys"}


def _format_type(column_type: Any) -> str:
    try:
        return str(column_type)
    except Exception:
        return column_type.__class__.__name__


def _get_inspector(db: Session):
    bind = db.get_bind()
    if bind is None:
        raise ValueError("Database session is not bound to an engine.")
    return inspect(bind)


def _iter_user_schemas(inspector) -> list[str]:
    schema_names = inspector.get_schema_names()
    return [schema_name for schema_name in schema_names if schema_name not in SYSTEM_SCHEMAS]


def _iter_table_names(inspector, schema_name: str | None = None) -> list[str]:
    return inspector.get_table_names(schema=schema_name)


def get_tables(db: Session) -> list[dict[str, Any]]:
    inspector = _get_inspector(db)
    tables: list[dict[str, Any]] = []
    for schema_name in _iter_user_schemas(inspector):
        for table_name in _iter_table_names(inspector, schema_name):
            tables.append({"table": table_name, "schema": schema_name})
    return tables


def get_table_details(db: Session, table_name: str, schema: str | None = None) -> dict[str, Any]:
    inspector = _get_inspector(db)
    if schema is None:
        schema = inspector.default_schema_name

    if not inspector.has_table(table_name, schema=schema):
        raise ValueError(f"Table '{table_name}' not found in schema '{schema}'.")

    columns = inspector.get_columns(table_name, schema=schema)
    pk = inspector.get_pk_constraint(table_name, schema=schema)
    foreign_keys = inspector.get_foreign_keys(table_name, schema=schema)
    indexes = inspector.get_indexes(table_name, schema=schema)

    pk_columns = set(pk.get("constrained_columns", []) or [])

    relationship_items: list[RelationshipInfo] = []
    foreign_key_items: list[ForeignKeyInfo] = []

    for fk in foreign_keys:
        constrained_columns = fk.get("constrained_columns", []) or []
        referred_columns = fk.get("referred_columns", []) or []
        referred_table = fk.get("referred_table", "")
        referred_schema = fk.get("referred_schema")
        name = fk.get("name")

        foreign_key_items.append(
            ForeignKeyInfo(
                constrained_columns=constrained_columns,
                referred_schema=referred_schema,
                referred_table=referred_table,
                referred_columns=referred_columns,
                name=name,
            )
        )
        relationship_items.append(
            RelationshipInfo(
                source_table=table_name,
                source_columns=constrained_columns,
                target_table=referred_table,
                target_columns=referred_columns,
                constraint_name=name,
            )
        )

    column_items = [
        ColumnInfo(
            name=column["name"],
            type=_format_type(column.get("type")),
            primary_key=column["name"] in pk_columns,
            nullable=column.get("nullable"),
            default=str(column.get("default")) if column.get("default") is not None else None,
            autoincrement=column.get("autoincrement"),
            foreign_key=next(
                (
                    f"{fk.referred_schema + '.' if fk.referred_schema else ''}{fk.referred_table}.{fk.referred_columns[0]}"
                    for fk in foreign_key_items
                    if column["name"] in fk.constrained_columns and fk.referred_columns
                ),
                None,
            ),
        )
        for column in columns
    ]

    index_items = [
        IndexInfo(
            name=index.get("name", ""),
            columns=index.get("column_names", []) or [],
            unique=index.get("unique", False),
        )
        for index in indexes
    ]

    return {
        "table": table_name,
        "schema": schema,
        "columns": column_items,
        "primary_key": PrimaryKeyInfo(columns=list(pk_columns)),
        "foreign_keys": foreign_key_items,
        "indexes": index_items,
        "relationships": relationship_items,
    }


def get_relationships(db: Session) -> list[dict[str, Any]]:
    inspector = _get_inspector(db)
    relationships: list[RelationshipInfo] = []

    for schema_name in _iter_user_schemas(inspector):
        for table_name in _iter_table_names(inspector, schema_name):
            details = get_table_details(db, table_name, schema=schema_name)
            relationships.extend(details["relationships"])

    return [relationship.model_dump() for relationship in relationships]


def get_database_schema(db: Session) -> dict[str, Any]:
    inspector = _get_inspector(db)
    tables: list[TableInfo] = []
    all_relationships: list[RelationshipInfo] = []

    for schema_name in _iter_user_schemas(inspector):
        for table_name in _iter_table_names(inspector, schema_name):
            details = get_table_details(db, table_name, schema=schema_name)
            table_info = TableInfo(
                table=details["table"],
                schema=details["schema"],
                columns=details["columns"],
                primary_key=details["primary_key"],
                foreign_keys=details["foreign_keys"],
                indexes=details["indexes"],
                relationships=details["relationships"],
            )
            tables.append(table_info)
            all_relationships.extend(details["relationships"])

    return DatabaseSchemaResponse(tables=tables, relationships=all_relationships).model_dump()
