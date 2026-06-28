from __future__ import annotations

from dataclasses import dataclass, field

from app.services.database_discovery_service import DiscoveredObject


@dataclass
class VirtualSchemaMapping:
    real_to_virtual: dict[str, str] = field(default_factory=dict)
    virtual_to_real: dict[str, str] = field(default_factory=dict)
    column_real_to_virtual: dict[str, dict[str, str]] = field(default_factory=dict)
    column_virtual_to_real: dict[str, dict[str, str]] = field(default_factory=dict)


def _humanize(name: str) -> str:
    parts = [part for part in name.replace("-", "_").split("_") if part]
    return "".join(part.capitalize() for part in parts) or "Object"


def build_virtual_schema(objects: list[DiscoveredObject]) -> tuple[list[dict], VirtualSchemaMapping]:
    mapping = VirtualSchemaMapping()
    virtual_objects: list[dict] = []

    for index, obj in enumerate(objects, start=1):
        real_key = f"{obj.schema + '.' if obj.schema else ''}{obj.name}"
        virtual_name = f"{_humanize(obj.name)}{index:02d}"
        mapping.real_to_virtual[real_key] = virtual_name
        mapping.virtual_to_real[virtual_name] = real_key

        column_map: dict[str, str] = {}
        virtual_columns = []
        for col_index, column in enumerate(obj.columns, start=1):
            virtual_column = f"{_humanize(column['name'])}{col_index:02d}"
            column_map[column["name"]] = virtual_column
            virtual_columns.append(
                {
                    "virtual_name": virtual_column,
                    "type": column["type"],
                    "nullable": column.get("nullable"),
                    "primary_key": column.get("primary_key", False),
                    "foreign_key": column.get("foreign_key"),
                }
            )

        mapping.column_real_to_virtual[virtual_name] = column_map
        mapping.column_virtual_to_real[virtual_name] = {v: k for k, v in column_map.items()}

        virtual_objects.append(
            {
                "virtual_name": virtual_name,
                "virtual_type": "view" if obj.object_type == "view" else "table",
                "real_name": obj.name,
                "real_schema": obj.schema,
                "columns": virtual_columns,
                "primary_key": [column_map.get(column, column) for column in obj.primary_key],
                "indexes": obj.indexes,
                "relationships": obj.relationships,
            }
        )

    return virtual_objects, mapping
