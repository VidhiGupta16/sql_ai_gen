from pydantic import BaseModel, Field


class QueryCostRequest(BaseModel):
    sql: str = Field(..., min_length=1, description="SQL statement to estimate cost for.")


class QueryCostResponse(BaseModel):
    estimated_cost: float
    estimated_rows: int
    scan_type: str
    risk: str
    recommendations: list[str]
