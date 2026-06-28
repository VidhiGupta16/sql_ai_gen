from fastapi import APIRouter, Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.repositories.query_history_repository import list_for_user
from app.schemas.query_history import QueryHistoryRead

router = APIRouter()


@router.get("", response_model=dict)
def list_history(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    try:
        items = list_for_user(db, current_user.id)
    except SQLAlchemyError:
        items = []
    return {"items": [QueryHistoryRead.model_validate(item) for item in items]}
