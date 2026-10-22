from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewJob(Base):
    __tablename__ = "review_jobs"
    __table_args__ = (
        UniqueConstraint(
            "repository_id",
            "pull_request_number",
            "head_sha",
            "request_key",
            name="uq_review_job_target",
        ),
        Index(
            "uq_manual_review_request",
            "repository_id",
            "request_key",
            unique=True,
            postgresql_where=text("request_key <> 'automatic'"),
            sqlite_where=text("request_key <> 'automatic'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_deliveries.delivery_id"), nullable=False
    )
    installation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    repository_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    repository: Mapped[str] = mapped_column(String(255), nullable=False)
    pull_request_number: Mapped[int] = mapped_column(Integer, nullable=False)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str] = mapped_column(
        String(128), default="automatic", server_default="automatic"
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    runs: Mapped[list[ReviewRun]] = relationship(back_populates="job")


class ReviewRun(Base):
    __tablename__ = "review_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("review_jobs.id"), nullable=False)
    retrieval_mode: Mapped[str] = mapped_column(String(32), default="graph")
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), default="v1")
    status: Mapped[str] = mapped_column(String(32), default="processing")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    retrieval_latency_ms: Mapped[float] = mapped_column(Float, default=0)
    model_latency_ms: Mapped[float] = mapped_column(Float, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    job: Mapped[ReviewJob] = relationship(back_populates="runs")
    findings: Mapped[list[ReviewFinding]] = relationship(back_populates="run")
    contexts: Mapped[list[RetrievedContextRecord]] = relationship(back_populates="run")


class ReviewEvaluation(Base):
    __tablename__ = "review_evaluations"

    run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"), primary_key=True)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool] = mapped_column(nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False)


class ReviewFinding(Base):
    __tablename__ = "review_findings"
    __table_args__ = (UniqueConstraint("run_id", "fingerprint", name="uq_run_finding"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    start_line: Mapped[int | None] = mapped_column(Integer)
    end_line: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    github_comment_id: Mapped[int | None] = mapped_column(Integer)

    run: Mapped[ReviewRun] = relationship(back_populates="findings")


class RetrievedContextRecord(Base):
    __tablename__ = "retrieved_contexts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(ForeignKey("review_runs.id"), nullable=False)
    symbol_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    graph_distance: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    selected: Mapped[bool] = mapped_column(default=False)

    run: Mapped[ReviewRun] = relationship(back_populates="contexts")
