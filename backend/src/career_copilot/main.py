"""FastAPI application for the Career Copilot backend."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    application = FastAPI(title="Career Copilot API")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
