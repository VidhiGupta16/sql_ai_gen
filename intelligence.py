from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DiscoveredColumn(BaseModel):
    name: str
    type: str
    nullable: bool | None = None
    primary_key: bool = False
    foreign_key: str | None = None
    default: str | None = None


class DiscoveredRelation(BaseModel):
    source_object: str
    source_columns: list[str]
    target_object: str
    target_columns: list[str]
    constraint_name: str | None = None


class DiscoveredObject(BaseModel):
    object_type: str = Field(description="table or view")
    name: str
    schema: str | None = None
    columns: list[DiscoveredColumn]
    primary_key: list[str] = Field(default_factory=list)
    indexes: list[str] = Field(default_factory=list)
    relationships: list[DiscoveredRelation] = Field(default_factory=list)


class DiscoveryResponse(BaseModel):
    objects: list[DiscoveredObject]


class VirtualColumn(BaseModel):
    virtual_name: str
    type: str
    nullable: bool | None = None
    primary_key: bool = False
    foreign_key: str | None = None


class VirtualObject(BaseModel):
    virtual_name: str
    virtual_type: str
    real_name: str
    real_schema: str | None = None
    columns: list[VirtualColumn]
    primary_key: list[str] = Field(default_factory=list)
    indexes: list[str] = Field(default_factory=list)
    relationships: list[DiscoveredRelation] = Field(default_factory=list)


class VirtualSchemaResponse(BaseModel):
    objects: list[VirtualObject]
    mapping: dict[str, str]


class PlanCandidate(BaseModel):
    sql: str
    reasoning: str
    confidence: float
    schema_score: int
    validation_score: int
    cost_score: int
    optimization_score: int


class PlanGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=3)


class PlanGenerationResponse(BaseModel):
    prompt: str
    virtual_schema: VirtualSchemaResponse
    candidates: list[PlanCandidate]
    selected: PlanCandidate
    provider: str
