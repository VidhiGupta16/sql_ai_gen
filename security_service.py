from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.pii_mapping import PIIMapping
from app.schemas.security import RiskAssessment
from app.services.schema_service import get_database_schema

logger = logging.getLogger(__name__)

RISK_ORDER = ["SAFE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
BLOCKED_PATTERNS = ("DROP", "TRUNCATE", "ALTER", "GRANT", "REVOKE")


@dataclass
class HallucinationResult:
    valid: bool
    errors: list[str]


def _risk(level: str) -> int:
    return RISK_ORDER.index(level)


def classify_query_risk(sql: str) -> RiskAssessment:
    try:
        logger.info("Classifying query risk")
        lowered = sql.lower().strip()
        reasons: list[str] = []
        level = "SAFE"

        if lowered.startswith("select"):
            level = "SAFE"
        if lowered.startswith("update"):
            level = "MEDIUM"
            reasons.append("UPDATE changes existing records.")
        if lowered.startswith("delete"):
            level = "HIGH"
            reasons.append("DELETE may remove data.")
        if any(keyword in lowered for keyword in BLOCKED_PATTERNS):
            level = "CRITICAL"
            reasons.append("Dangerous SQL keyword detected.")

        return RiskAssessment(level=level, score=_risk(level) * 25, reasons=reasons, blocked=level == "CRITICAL")
    except Exception:
        logger.exception("Unhandled exception in classify_query_risk")
        raise


def is_dangerous_sql(sql: str) -> bool:
    return any(re.search(rf"\b{keyword}\b", sql, flags=re.IGNORECASE) for keyword in BLOCKED_PATTERNS)


def validate_hallucination(db: Session, sql: str) -> HallucinationResult:
    try:
        logger.info("Validating hallucination")
        schema = get_database_schema(db)
        tables = {f"{table.get('schema')}.{table['table']}" if table.get("schema") else table["table"] for table in schema["tables"]}
        bare_tables = {table.split(".")[-1] for table in tables}
        columns = {
            (f"{table.get('schema')}.{table['table']}" if table.get("schema") else table["table"]): {column["name"] for column in table["columns"]}
            for table in schema["tables"]
        }

        errors: list[str] = []
        alias_map: dict[str, str] = {}
        from_matches = re.finditer(r"\b(FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][A-Za-z0-9_\.]*)(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?", sql, flags=re.IGNORECASE)
        for match in from_matches:
            table_name = match.group(2)
            alias = match.group(3)
            if alias:
                alias_map[alias] = table_name

        table_refs = re.findall(r"\b(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_\.]*)", sql, flags=re.IGNORECASE)

        for ref in table_refs:
            if ref not in tables and ref.split(".")[-1] not in bare_tables:
                errors.append(f"Referenced table '{ref}' does not exist.")

        select_match = re.search(r"\bSELECT\s+(.*?)\bFROM\b", sql, flags=re.IGNORECASE | re.DOTALL)
        if select_match and table_refs:
            first_table = table_refs[0].split(".")[-1]
            known_columns = columns.get(first_table, set()) or columns.get(next((table for table in columns if table.endswith(f".{first_table}")), first_table), set())
            if select_match.group(1).strip() != "*":
                for token in [part.strip().split(" AS ")[0] for part in select_match.group(1).split(",")]:
                    clean = token.split(".")[-1].strip('"`[]')
                    if clean and clean not in known_columns and not re.search(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", clean, flags=re.IGNORECASE):
                        errors.append(f"Referenced column '{clean}' does not exist on table '{first_table}'.")

        for alias, table_name in alias_map.items():
            if table_name.split(".")[-1] not in bare_tables:
                errors.append(f"Alias '{alias}' references unknown table '{table_name}'.")

        join_patterns = re.finditer(r"\bJOIN\s+([A-Za-z_][A-Za-z0-9_\.]*)(?:\s+(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?\s*ON\s+(.*?)(?=\bJOIN\b|\bWHERE\b|\bGROUP BY\b|\bORDER BY\b|\bLIMIT\b|;|$)", sql, flags=re.IGNORECASE | re.DOTALL)
        for match in join_patterns:
            join_table = match.group(1)
            join_alias = match.group(2)
            join_clause = match.group(3)
            join_table_name = join_table.split(".")[-1]
            if join_table_name not in bare_tables:
                errors.append(f"Join table '{join_table}' does not exist.")
            if join_alias:
                alias_map[join_alias] = join_table
            for identifier in re.findall(r"\b([A-Za-z_][A-Za-z0-9_\.]*)\b", join_clause):
                if identifier.upper() in {"AND", "OR", "ON", "USING", "NULL", "IS", "NOT", "IN", "EXISTS", "TRUE", "FALSE"}:
                    continue
                if "." in identifier:
                    alias_part, column_part = identifier.split(".", 1)
                    resolved_table = alias_map.get(alias_part, alias_part)
                    resolved_table_name = resolved_table.split(".")[-1]
                    if resolved_table_name not in bare_tables:
                        errors.append(f"Join alias '{alias_part}' does not resolve to a known table.")
                        continue
                    if column_part not in columns.get(resolved_table_name, set()) and column_part not in columns.get(resolved_table, set()):
                        errors.append(f"Join column '{identifier}' does not exist.")

        return HallucinationResult(valid=not errors, errors=errors)
    except Exception:
        logger.exception("Unhandled exception in validate_hallucination")
        raise


def mask_pii(db: Session, value: str, pii_type: str, tenant_id: str | None = None) -> str:
    try:
        masked = f"[{pii_type.upper()}_MASKED]"
        mapping = PIIMapping(tenant_id=tenant_id, pii_type=pii_type, original_value=value, masked_value=masked)
        db.add(mapping)
        db.commit()
        return masked
    except Exception:
        logger.exception("Unhandled exception in mask_pii")
        raise


def mask_prompt(db: Session, prompt: str, tenant_id: str | None = None) -> str:
    try:
        patterns = {
            "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
            "phone": r"\b(?:\+?\d{1,3}[- ]?)?\d{10}\b",
            "aadhaar": r"\b\d{4}\s?\d{4}\s?\d{4}\b",
            "pan": r"\b[A-Z]{5}\d{4}[A-Z]\b",
            "name": r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b",
        }

        masked = prompt
        for pii_type, pattern in patterns.items():
            for match in re.finditer(pattern, masked):
                masked = masked.replace(match.group(0), mask_pii(db, match.group(0), pii_type, tenant_id))
        return masked
    except Exception:
        logger.exception("Unhandled exception in mask_prompt")
        raise


def can_execute_sql(role: str, sql: str, approval: bool) -> bool:
    lowered = sql.lower().strip()
    if is_dangerous_sql(sql):
        return role.lower() == "admin" and approval
    if lowered.startswith("update") or lowered.startswith("delete") or lowered.startswith("insert"):
        return role.lower() == "admin" and approval
    if lowered.startswith("select") and role.lower() in {"admin", "analyst"}:
        return True
    return role.lower() == "admin"
