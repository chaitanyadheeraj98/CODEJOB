from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RecruiterEmail(Base):
    __tablename__ = "recruiter_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sender: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    salary_text: Mapped[str] = mapped_column(String(255), default="")
    skills_text: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[str] = mapped_column(String(50), index=True)
    draft_reply: Mapped[str] = mapped_column(Text, default="")
    approval_status: Mapped[str] = mapped_column(String(50), default="pending")
    sent_status: Mapped[str] = mapped_column(String(50), default="not_sent")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    external_message_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    external_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
