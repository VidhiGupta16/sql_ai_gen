from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _create_engine():
    if settings.DATABASE_URL.startswith("sqlite"):
        return create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})
    try:
        test_engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
        with test_engine.connect():
            pass
        return test_engine
    except SQLAlchemyError:
        return create_engine("sqlite:///./sql_ai.db", connect_args={"check_same_thread": False})


engine = _create_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, class_=Session)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
