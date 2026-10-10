import asyncio
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

import httpx2
import pytest
from fastapi import Depends
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.requests import Request

from career_copilot import main
from career_copilot.db.models import User
from career_copilot.db.session import create_session_factory, get_session
from career_copilot.settings import Settings

pytestmark = pytest.mark.database


async def test_async_requests_cleanup_and_shutdown(
    db_engine: AsyncEngine,
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "create_engine", lambda _: db_engine)
    disposed = []
    event.listen(
        db_engine.sync_engine, "engine_disposed", lambda _: disposed.append(True)
    )
    app = main.create_app(test_settings)
    observed_sessions = []

    @app.post("/test/transaction/{mode}")
    async def transaction(
        mode: str,
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> dict[str, str]:
        observed_sessions.append(session)
        user = User(identity_issuer="fixture-issuer", identity_subject=uuid4().hex)
        if mode == "commit":
            async with session.begin():
                session.add(user)
                await session.flush()
        else:
            session.add(user)
            await session.flush()
            if mode == "fail":
                raise RuntimeError("fixture failure")
        return {"id": str(user.id)}

    async with app.router.lifespan_context(app):
        assert app.state.engine.dialect.is_async
        transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            assert (await client.post("/test/transaction/commit")).status_code == 200
            assert (
                await client.post("/test/transaction/uncommitted")
            ).status_code == 200
            assert (await client.post("/test/transaction/fail")).status_code == 500
        assert len({id(session) for session in observed_sessions}) == 3
        assert db_engine.pool.checkedout() == 0
        async with app.state.session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert disposed == []
    assert disposed == [True]


async def test_cancelled_work_rolls_back_and_releases_connection(
    db_engine: AsyncEngine,
) -> None:
    app = main.create_app()
    app.state.session_factory = create_session_factory(db_engine)
    request = Request({"type": "http", "app": app})
    started = asyncio.Event()

    async def work() -> None:
        async with asynccontextmanager(get_session)(request) as session:
            session.add(
                User(identity_issuer="fixture-issuer", identity_subject="cancelled")
            )
            await session.flush()
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert db_engine.pool.checkedout() == 0
    async with app.state.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 0
        assert await session.scalar(text("SELECT 1")) == 1
