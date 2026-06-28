from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.orm import Session


def estimate_query_cost(db: Session, sql: str) -> tuple[float, int, str, str, list[str]]:
    estimated_cost = 0.0
    estimated_rows = 0
    scan_type = "UNKNOWN"
    recommendations: list[str] = []
    risk = "LOW"

    try:
        result = db.execute(text(f"EXPLAIN {sql}"))
        plan_text = "\n".join(str(row[0]) for row in result.fetchall())
        plan_lower = plan_text.lower()

        cost_match = re.search(r"cost=([0-9.]+)\.\.([0-9.]+)", plan_lower)
        rows_match = re.search(r"rows=(\d+)", plan_lower)
        if cost_match:
            estimated_cost = float(cost_match.group(2))
        if rows_match:
            estimated_rows = max(1, int(rows_match.group(1)))

        if "index scan" in plan_lower or "bitmap index scan" in plan_lower:
            scan_type = "INDEX_SCAN"
        elif "seq scan" in plan_lower or "full table scan" in plan_lower:
            scan_type = "FULL_TABLE_SCAN"
        elif "sort" in plan_lower:
            scan_type = "SORT"
        else:
            scan_type = "UNKNOWN"

        if "seq scan" in plan_lower:
            recommendations.append("Consider adding or using an index to avoid a full table scan.")
        if "sort" in plan_lower:
            recommendations.append("Consider reducing ORDER BY work or adding an index on the sort column.")
        if estimated_rows > 1000:
            recommendations.append("Estimated row count is high; add filters or LIMIT if possible.")

        if scan_type == "FULL_TABLE_SCAN" or estimated_cost >= 1000:
            risk = "HIGH"
        elif estimated_cost >= 100:
            risk = "MEDIUM"
    except Exception:
        estimated_cost = 0.0
        estimated_rows = 0
        scan_type = "UNKNOWN"
        risk = "UNKNOWN"
        recommendations.append("Cost estimation is unavailable for this statement.")

    return estimated_cost, estimated_rows, scan_type, risk, list(dict.fromkeys(recommendations))
