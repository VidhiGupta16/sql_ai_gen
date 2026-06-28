from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, hash_token
from app.db.session import get_db
from app.models.refresh_token import RefreshToken
from app.repositories.refresh_token_repository import create as create_refresh, get_by_hash, revoke
from app.repositories.user_repository import get_by_email
from app.schemas.token import RefreshTokenRequest, Token
from app.schemas.user import UserCreate, UserRead
from app.services.user_service import authenticate_user, create_user

router = APIRouter()


def _issue_tokens(db: Session, user, request: Request | None = None) -> Token:
    access_token = create_access_token(
        subject=user.id,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_token = create_refresh_token()
    refresh = RefreshToken(
        user_id=user.id,
        token_hash=hash_token(refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        user_agent=request.headers.get("user-agent") if request else None,
        ip_address=request.client.host if request and request.client else None,
    )
    create_refresh(db, refresh)
    return Token(access_token=access_token, refresh_token=refresh_token, expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60)


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    existing = get_by_email(db, payload.email)
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    return create_user(db, payload)


@router.post("/login", response_model=Token)
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    return _issue_tokens(db, user, request)


@router.post("/refresh", response_model=Token)
def refresh_token(payload: RefreshTokenRequest, request: Request, db: Session = Depends(get_db)):
    token_hash = hash_token(payload.refresh_token)
    token_record = get_by_hash(db, token_hash)
    if token_record is None or token_record.revoked or token_record.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user = token_record.user
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive user")

    revoke(db, token_record, replaced_by=token_hash)
    return _issue_tokens(db, user, request)


@router.post("/logout")
def logout(payload: RefreshTokenRequest, db: Session = Depends(get_db)):
    token_hash = hash_token(payload.refresh_token)
    token_record = get_by_hash(db, token_hash)
    if token_record:
        revoke(db, token_record)
    return {"message": "Logged out"}


@router.get("/me", response_model=UserRead)
def read_me(current_user=Depends(get_current_user)):
    return current_user
