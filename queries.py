from fastapi import APIRouter, Depends, HTTPException, status
import logging
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.query_history import QueryHistory
from app.repositories.query_history_repository import create as create_history
from app.schemas.query_execution import QueryExecuteRequest, QueryExecuteResponse
from app.schemas.query_explanation import QueryExplainRequest, QueryExplainResponse
from app.schemas.query_generation import QueryGenerateRequest, QueryGenerateResponse
from app.schemas.query_optimization import QueryOptimizationRequest, QueryOptimizationResponse
from app.schemas.query_cost import QueryCostRequest, QueryCostResponse
from app.schemas.query_impact import QueryImpactRequest, QueryImpactResponse
from app.schemas.query_validation import QueryValidationRequest, QueryValidationResponse
from app.services.query_cost_service import estimate_query_cost
from app.services.query_execution_service import execute_query
from app.services.query_explanation_service import explain_sql
from app.services.query_impact_service import analyze_query_impact
from app.services.query_optimization_service import optimize_sql
from app.services.query_validation_service import validate_query
from app.services.security_service import (
    can_execute_sql,
    classify_query_risk,
    mask_prompt,
    validate_hallucination,
)
from app.services.sql_generation_service import generate_sql_from_prompt

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/generate", response_model=QueryGenerateResponse)
def generate_query(
    payload: QueryGenerateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("viewer", "analyst", "admin")),
):
    try:
        print("Received prompt:", payload.prompt)
        logger.info("POST /api/v1/query/generate received prompt=%s user_id=%s", payload.prompt, current_user.id)
        print("Masking prompt...")
        tenant_id = getattr(current_user, "tenant_id", None)
        try:
            masked_prompt = mask_prompt(db, payload.prompt, tenant_id)
            logger.info("Masked prompt=%s", masked_prompt)
        except Exception:
            logger.exception("FAILED AT STEP 2: mask_prompt")
            masked_prompt = payload.prompt
            logger.info("Falling back to original prompt after masking failure")
        print("Generating SQL...")
        result = generate_sql_from_prompt(db, masked_prompt)
        logger.info("Generated SQL=%s", result.generated_sql)
        print("Generated SQL:", result.generated_sql)
        return QueryGenerateResponse(
            generated_sql=result.generated_sql,
            alternatives=result.alternatives,
            tables=result.tables,
            columns=result.columns,
            confidence_score=result.confidence_score,
            ambiguity=result.ambiguity,
            intent=result.intent,
            suggestions=result.suggestions,
            plan_reasoning=result.plan_reasoning,
        )
    except Exception as e:
        logger.exception("Unhandled exception in POST /api/v1/query/generate")
        raise


@router.get("")
def list_queries(current_user=Depends(get_current_user)):
    return {"items": [], "message": "Use POST /api/v1/query/generate to generate SQL."}


@router.post("/explain", response_model=QueryExplainResponse)
def explain_query(
    payload: QueryExplainRequest,
    current_user=Depends(require_roles("viewer", "analyst", "admin")),
):
    try:
        logger.info("Explaining SQL=%s", payload.sql)
        result = explain_sql(payload.sql)
        return QueryExplainResponse(
            explanation=result.explanation,
            business_explanation=result.business_explanation,
            tables=result.tables,
            tables_used=result.tables,
            columns=result.columns,
            columns_used=result.columns,
            filters=result.filters,
            joins=result.joins,
            aggregations=result.aggregations,
            operation=result.operation,
            sorting=result.sorting,
            limits=result.limits,
        )
    except Exception:
        logger.exception("Unhandled exception in POST /api/v1/query/explain")
        raise


@router.post("/optimize", response_model=QueryOptimizationResponse)
def optimize_query(
    payload: QueryOptimizationRequest,
    current_user=Depends(require_roles("viewer", "analyst", "admin")),
):
    try:
        logger.info("Optimizing SQL=%s", payload.sql)
        result = optimize_sql(payload.sql)
        return QueryOptimizationResponse(
            optimized_sql=result.optimized_sql,
            optimization_score=result.optimization_score,
            issues=result.issues,
            recommendations=result.recommendations,
        )
    except Exception:
        logger.exception("Unhandled exception in POST /api/v1/query/optimize")
        raise


@router.post("/cost", response_model=QueryCostResponse)
def cost_query(
    payload: QueryCostRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("viewer", "analyst", "admin")),
):
    try:
        logger.info("Estimating cost for SQL=%s", payload.sql)
        estimated_cost, estimated_rows, scan_type, risk, recommendations = estimate_query_cost(db, payload.sql)
        return QueryCostResponse(
            estimated_cost=estimated_cost,
            estimated_rows=estimated_rows,
            scan_type=scan_type,
            risk=risk,
            recommendations=recommendations,
        )
    except Exception:
        logger.exception("Unhandled exception in POST /api/v1/query/cost")
        raise


@router.post("/impact", response_model=QueryImpactResponse)
def impact_query(
    payload: QueryImpactRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("viewer", "analyst", "admin")),
):
    try:
        logger.info("Analyzing impact for SQL=%s", payload.sql)
        result = analyze_query_impact(db, payload.sql)
        risk = classify_query_risk(payload.sql)
        return QueryImpactResponse(
            estimated_rows=result.estimated_rows,
            estimated_cost=result.estimated_cost,
            risk_level=risk.level,
            risk_score=risk.score,
            warnings=result.warnings,
            tables_affected=result.tables_affected,
            operation_type=result.operation_type,
            notes=[],
        )
    except Exception:
        logger.exception("Unhandled exception in POST /api/v1/query/impact")
        raise


@router.post("/validate", response_model=QueryValidationResponse)
def validate_sql_query(
    payload: QueryValidationRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("viewer", "analyst", "admin")),
):
    try:
        logger.info("Validating SQL=%s", payload.sql)
        result = validate_query(payload.sql)
        hallucination = validate_hallucination(db, payload.sql)
        if not hallucination.valid:
            result.errors.extend(hallucination.errors)
            result.blocked = True
            result.valid = False
        return QueryValidationResponse(
            valid=result.valid,
            risk_level=result.risk_level,
            errors=result.errors,
            warnings=result.warnings,
            optimization_suggestions=result.optimization_suggestions,
            blocked=result.blocked,
        )
    except Exception:
        logger.exception("Unhandled exception in POST /api/v1/query/validate")
        raise


@router.post("/execute", response_model=QueryExecuteResponse)
def execute_sql_query(
    payload: QueryExecuteRequest,
    db: Session = Depends(get_db),
    current_user=Depends(require_roles("analyst", "admin")),
):
    try:
        print("========================")
        print("payload.sql:", payload.sql)
        logger.info("Executing SQL=%s", payload.sql)
        risk = classify_query_risk(payload.sql)
        if risk.level in {"HIGH", "CRITICAL"} and not (current_user.role == "admin" and payload.approval):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="High risk queries require admin approval")

        if payload.sql.strip().lower().startswith(("update", "delete", "insert")) and current_user.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can execute write queries")

        if not can_execute_sql(current_user.role, payload.sql, payload.approval):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Query is not permitted for this role")

        hallucination = validate_hallucination(db, payload.sql)
        if not hallucination.valid:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=hallucination.errors)

        result = execute_query(
            db=db,
            sql=payload.sql,
            page=payload.page,
            page_size=payload.page_size,
            approval=payload.approval,
            tenant_id=payload.tenant_id or current_user.tenant_id,
        )
        logger.info("Rows fetched=%s", result.rows_returned)
        history = QueryHistory(
            user_id=current_user.id,
            prompt=payload.sql,
            generated_sql=payload.sql,
            risk_level=result.risk_level,
            execution_status="success" if result.success else ("blocked" if result.blocked else "failed"),
            status="executed" if result.success else "failed",
            tenant_id=payload.tenant_id or current_user.tenant_id,
        )
        create_history(db, history)
        return QueryExecuteResponse(
            success=result.success,
            execution_time_ms=result.execution_time_ms,
            rows_returned=result.rows_returned,
            rows_affected=result.rows_affected,
            columns=result.columns,
            data=result.data,
            message=result.message,
            operation_type=result.operation_type,
            blocked=result.blocked,
            risk_level=result.risk_level,
            validation_errors=result.validation_errors,
            warnings=result.warnings,
            risk_score=risk.score,
        )
    except Exception:
        logger.exception("Unhandled exception in POST /api/v1/query/execute")
        raise
