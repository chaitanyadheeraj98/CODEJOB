from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime


def utc_now() -> datetime:
    return datetime.now(UTC)


class ExternalFeedSource(Base):
    __tablename__ = "external_feed_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True, default="default-owner")
    source_type: Mapped[str] = mapped_column(String(40), index=True, default="nvoids")
    base_url: Mapped[str] = mapped_column(String(500), default="https://www.nvoids.com/")
    enabled: Mapped[bool] = mapped_column(default=True)
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=45)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ExternalOpportunity(Base):
    __tablename__ = "external_opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True, default="default-owner")
    feed_source_id: Mapped[int] = mapped_column(Integer, ForeignKey("external_feed_sources.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True, default="nvoids")
    external_post_id: Mapped[str] = mapped_column(String(255), index=True)
    source_url: Mapped[str] = mapped_column(String(1200), default="")
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    recruiter_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    recruiter_phone: Mapped[str] = mapped_column(String(80), default="", index=True)
    recruiter_name: Mapped[str] = mapped_column(String(255), default="")
    company: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    work_mode: Mapped[str] = mapped_column(String(80), default="")
    visa_hints: Mapped[str] = mapped_column(String(255), default="")
    duration: Mapped[str] = mapped_column(String(255), default="")
    rate: Mapped[str] = mapped_column(String(255), default="")
    skills_text: Mapped[str] = mapped_column(Text, default="")
    raw_body: Mapped[str] = mapped_column(Text, default="")
    raw_html: Mapped[str] = mapped_column(Text, default="")
    dedupe_hash: Mapped[str] = mapped_column(String(80), index=True)
    parse_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    bridge_status: Mapped[str] = mapped_column(String(40), default="pending")
    bridge_target_opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ExternalScrapeRun(Base):
    __tablename__ = "external_scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True, default="default-owner")
    source_type: Mapped[str] = mapped_column(String(40), index=True, default="nvoids")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    deduped_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)
