from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PIIMapping(Base):
    __tablename__ = "pii_mappings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    pii_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    original_value: Mapped[str] = mapped_column(Text, nullable=False)
    masked_value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
