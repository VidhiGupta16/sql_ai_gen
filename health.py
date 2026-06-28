from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter()


@router.get("")
def health_check():
    return {"status": "ok", "service": "ai-sql-assistant-backend"}


@router.get("/database")
def health_database(db: Session = Depends(get_db)):
    result = db.execute(text("SELECT COUNT(*) FROM employees;"))
    employee_count = int(result.scalar_one())
    return {"database_connected": True, "employee_count": employee_count}


@router.get("/query")
def health_query(db: Session = Depends(get_db)):
    result = db.execute(text("SELECT * FROM employees LIMIT 5;"))
    return {"rows": [dict(row._mapping) for row in result.fetchall()]}
