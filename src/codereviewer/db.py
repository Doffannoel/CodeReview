from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import inspect
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from codereviewer.config import Settings
from codereviewer.models import Base, ReviewJob


class Database:
    def __init__(self, settings: Settings) -> None:
        database_target: str | URL = settings.database_url
        if settings.database_password_file or settings.database_password.get_secret_value():
            database_target = URL.create(
                "postgresql+asyncpg",
                username=settings.database_username,
                password=settings.database_password_value,
                host=settings.database_host,
                port=settings.database_port,
                database=settings.database_name,
            )
        self.engine: AsyncEngine = create_async_engine(database_target, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                # Serialize startup DDL when API and worker start together.
                await connection.exec_driver_sql("SELECT pg_advisory_xact_lock(746326001)")
            columns = await connection.run_sync(
                lambda conn: (
                    {column["name"] for column in inspect(conn).get_columns("review_jobs")}
                    if inspect(conn).has_table("review_jobs")
                    else set()
                )
            )
            if columns and "request_key" not in columns:
                if connection.dialect.name != "postgresql":
                    raise RuntimeError("Existing queues require PostgreSQL for the SRS migration")
                await connection.exec_driver_sql(
                    "ALTER TABLE review_jobs ADD COLUMN request_key VARCHAR(128) "
                    "NOT NULL DEFAULT 'automatic'"
                )
                await connection.exec_driver_sql(
                    "ALTER TABLE review_jobs DROP CONSTRAINT uq_review_job_target"
                )
                await connection.exec_driver_sql(
                    "ALTER TABLE review_jobs ADD CONSTRAINT uq_review_job_target UNIQUE "
                    "(repository_id, pull_request_number, head_sha, request_key)"
                )
            await connection.run_sync(Base.metadata.create_all)
            for index in ReviewJob.__table__.indexes:
                await connection.run_sync(
                    lambda conn, index=index: index.create(conn, checkfirst=True)
                )

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()
