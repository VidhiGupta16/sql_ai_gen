from sqlalchemy.orm import Session

from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.repositories import user_repository
from app.schemas.user import UserCreate


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = user_repository.get_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


def create_user(db: Session, payload: UserCreate) -> User:
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=get_password_hash(payload.password),
        role=payload.role,
        tenant_id=payload.tenant_id,
    )
    return user_repository.create(db, user)
