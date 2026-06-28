from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "mysql", "performance_schema", "sys", "sqlite_master"}


@dataclass
class DiscoveredObject:
    object_type: str
    name: str
    schema: str | None
    columns: list[dict[str, Any]]
    primary_key: list[str]
    indexes: list[str]
    relationships: list[dict[str, Any]]


def _get_inspector(db: Session):
    bind = db.get_bind()
    if bind is None:
        raise ValueError("Database session is not bound to an engine.")
    return inspect(bind)


def discover_database_objects(db: Session) -> list[DiscoveredObject]:
    inspector = _get_inspector(db)
    objects: list[DiscoveredObject] = []
    schema_names = [schema for schema in inspector.get_schema_names() if schema not in SYSTEM_SCHEMAS]
    if not schema_names:
        schema_names = [None]

    for schema_name in schema_names:
        try:
            table_names = inspector.get_table_names(schema=schema_name)
        except Exception:
            table_names = []
        try:
            view_names = inspector.get_view_names(schema=schema_name)
        except Exception:
            view_names = []

        for object_type, names in (("table", table_names), ("view", view_names)):
            for name in names:
                try:
                    columns = inspector.get_columns(name, schema=schema_name)
                    pk = inspector.get_pk_constraint(name, schema=schema_name).get("constrained_columns", []) or []
                    indexes = inspector.get_indexes(name, schema=schema_name)
                    foreign_keys = inspector.get_foreign_keys(name, schema=schema_name)
                except Exception:
                    continue

                relationships = [
                    {
                        "source_object": name,
                        "source_columns": fk.get("constrained_columns", []) or [],
                        "target_object": fk.get("referred_table", ""),
                        "target_columns": fk.get("referred_columns", []) or [],
                        "constraint_name": fk.get("name"),
                    }
                    for fk in foreign_keys
                ]

                objects.append(
                    DiscoveredObject(
                        object_type=object_type,
                        name=name,
                        schema=schema_name,
                        columns=[
                            {
                                "name": column["name"],
                                "type": str(column.get("type")),
                                "nullable": column.get("nullable"),
                                "primary_key": column["name"] in pk,
                                "foreign_key": next(
                                    (
                                        f"{fk.get('referred_table')}.{fk.get('referred_columns', [''])[0]}"
                                        for fk in foreign_keys
                                        if column["name"] in (fk.get("constrained_columns", []) or [])
                                        and fk.get("referred_columns")
                                    ),
                                    None,
                                ),
                                "default": str(column.get("default")) if column.get("default") is not None else None,
                            }
                            for column in columns
                        ],
                        primary_key=list(pk),
                        indexes=[idx.get("name", "") for idx in indexes if idx.get("name")],
                        relationships=relationships,
                    )
                )

    return objects
