# Agentic Career Copilot

A full-stack AI career copilot portfolio project, built incrementally.

## Documentation

- [Product](docs/product.md)
- [Architecture](docs/architecture.md)

## Backend development

Requirements: Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

Run these commands from the repository root:

```sh
cd backend
uv sync --locked
uv run uvicorn career_copilot.main:app --reload
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

## Frontend development

Requirements: Node.js 24 or newer and npm.

Run these commands from the repository root:

```sh
cd frontend
npm ci
npm run dev
npm run lint
npm run test
npm run build
```
