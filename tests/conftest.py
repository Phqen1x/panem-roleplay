"""Shared pytest fixtures.

Service-layer tests need a real Postgres (JSONB/ARRAY columns aren't
representable in SQLite), so they run against `TEST_DATABASE_URL` (default:
a local `panem_test` database) with the full schema created once per
session and each test wrapped in a savepoint that's rolled back afterward.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from panem_shared.db.base import Base
from panem_shared.db.models import *  # noqa: F403  (register all tables on Base.metadata)

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://panem:panem@localhost:5432/panem_test"
)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _engine():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(loop_scope="session")
async def _db_conn(_engine) -> AsyncIterator[AsyncConnection]:
    """One connection + outer transaction per test, rolled back afterward
    so tests never see each other's writes. `db_session` and
    `db_session_factory` both bind to this same connection -- every session
    either fixture produces (including ones code-under-test opens itself
    via the factory) nests as a savepoint inside that one transaction."""
    async with _engine.connect() as conn:
        trans = await conn.begin()
        yield conn
        await trans.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(_db_conn: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """A session bound to the per-test connection/savepoint."""
    session_factory = async_sessionmaker(
        bind=_db_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
def db_session_factory(_db_conn: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    """For code under test that takes an `async_sessionmaker` and opens its
    own session(s) (e.g. `panem_sim.tick`'s one-transaction-per-call
    functions), rather than being handed an already-open session."""
    return async_sessionmaker(
        bind=_db_conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
