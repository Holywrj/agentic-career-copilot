"""FastAPI application for the Career Copilot backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from career_copilot.auth.middleware import AuthResponseMiddleware
from career_copilot.auth.passwords import PasswordService
from career_copilot.auth.router import router as auth_router
from career_copilot.auth.tokens import TokenService
from career_copilot.career_profile.middleware import CareerProfileResponseMiddleware
from career_copilot.career_profile.router import router as career_profile_router
from career_copilot.db.session import create_engine, create_session_factory
from career_copilot.resume.middleware import ResumeResponseMiddleware
from career_copilot.resume.router import router as resume_router
from career_copilot.resume.storage import LocalResumeStorage, ResumeStorage, run_io
from career_copilot.settings import AuthSettings, ResumeSettings, Settings


def create_app(
    settings: Settings | None = None,
    auth_settings: AuthSettings | None = None,
    resume_settings: ResumeSettings | None = None,
    resume_storage: ResumeStorage | None = None,
) -> FastAPI:
    """Create the FastAPI application."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configured = settings if settings is not None else Settings.from_env()
        configured_auth = (
            auth_settings if auth_settings is not None else AuthSettings.from_env()
        )
        engine = create_engine(configured)
        application.state.engine = engine
        application.state.session_factory = create_session_factory(engine)
        try:
            application.state.resume_storage = (
                resume_storage
                if resume_storage is not None
                else await run_io(
                    LocalResumeStorage,
                    (
                        resume_settings
                        if resume_settings is not None
                        else ResumeSettings.from_env()
                    ).storage_dir,
                )
            )
            application.state.auth_settings = configured_auth
            application.state.tokens = TokenService(configured_auth)
            application.state.passwords = await PasswordService.create()
            yield
        finally:
            await engine.dispose()

    application = FastAPI(title="Career Copilot API", lifespan=lifespan)
    application.add_middleware(AuthResponseMiddleware)
    application.add_middleware(CareerProfileResponseMiddleware)
    application.add_middleware(ResumeResponseMiddleware)
    application.include_router(auth_router)
    application.include_router(career_profile_router)
    application.include_router(resume_router)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
