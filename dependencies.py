from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import ALGORITHM
from app.db.session import get_db
from app.repositories.user_repository import get_by_id
from app.schemas.token import TokenPayload

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


def get_current_user(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        subject = payload.get("sub")
        if subject is None:
            raise credentials_exception
        TokenPayload(
            sub=subject,
            role=payload.get("role"),
            token_type=payload.get("token_type"),
            tenant_id=payload.get("tenant_id"),
        )
    except JWTError as exc:
        raise credentials_exception from exc
    except ValidationError as exc:
        raise credentials_exception from exc

    try:
        user_id = int(subject)
        user = get_by_id(db, user_id)
    except (TypeError, ValueError, SQLAlchemyError) as exc:
        raise credentials_exception from exc
    except Exception as exc:
        raise credentials_exception from exc

    if user is None:
        raise credentials_exception
    return user


def require_roles(*roles: str):
    def _dependency(current_user=Depends(get_current_user)):
        if current_user.role not in roles and not current_user.is_superuser:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return current_user

    return _dependency
