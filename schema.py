from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.schemas.schema import DatabaseSchemaResponse, TableInfo
from app.services.schema_service import get_database_schema, get_relationships, get_table_details, get_tables

router = APIRouter()


@router.get(
    "/tables",
    summary="List database tables",
    description="Discover all tables available in the connected PostgreSQL or MySQL database using SQLAlchemy inspection.",
)
def list_tables(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return {"tables": get_tables(db)}
    except SQLAlchemyError:
        return {"tables": []}


@router.get(
    "/table/{table_name}",
    response_model=TableInfo,
    summary="Inspect a single table",
    description="Return structured metadata for one table including columns, primary keys, foreign keys, indexes, and relationships.",
)
def read_table(
    table_name: str,
    schema: str | None = Query(default=None, description="Optional schema name. Defaults to the engine's default schema."),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    inspector_schema = schema
    try:
        payload = get_table_details(db, table_name=table_name, schema=inspector_schema)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Table '{table_name}' not found") from exc
    return payload


@router.get(
    "/relationships",
    summary="List database relationships",
    description="Discover all foreign key relationships across the connected database.",
)
def read_relationships(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return {"relationships": get_relationships(db)}
    except SQLAlchemyError:
        return {"relationships": []}


@router.get(
    "/full",
    response_model=DatabaseSchemaResponse,
    summary="Full schema snapshot",
    description="Return the complete structured database schema with tables, columns, keys, indexes, and relationships.",
)
def read_full_schema(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    try:
        return get_database_schema(db)
    except SQLAlchemyError:
        return {"tables": [], "relationships": []}
