from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from codereviewer.config import Settings
from codereviewer.models import Base


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
            await connection.run_sync(Base.metadata.create_all)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def close(self) -> None:
        await self.engine.dispose()
