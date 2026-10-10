# Agentic Career Copilot

A full-stack AI career copilot portfolio project, built incrementally.

## Documentation

- [Product](docs/product.md)
- [Architecture](docs/architecture.md)

## Backend development

Requirements: Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Docker with Compose for local PostgreSQL.

Run these commands from the repository root:

```sh
cp .env.example .env
docker compose --profile test up -d --wait db db-test
cd backend
uv sync --locked
uv run --env-file ../.env alembic upgrade head
uv run --env-file ../.env uvicorn career_copilot.main:app --reload
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run --env-file ../.env pytest
```

The example credentials are for local development only. Compose binds PostgreSQL to the loopback interface. The development database uses a persistent volume; the separate test database uses temporary storage. `.env` is ignored by Git.

`DATABASE_URL` is required at application startup and must use `postgresql+psycopg://`. Environment variables take precedence over the explicitly loaded env file. The application uses asynchronous Psycopg connections and never creates or migrates tables at startup. `/health` reports application liveness and does not query PostgreSQL.

`TEST_DATABASE_URL` must name a separate database ending in `_test`. Database tests fail when it is missing or unavailable. Each test creates a random schema through Alembic, then removes only that schema. Do not point this setting at a database containing valuable data.

Migration commands, run from `backend/`:

```sh
uv run --env-file ../.env alembic current
uv run --env-file ../.env alembic check
uv run --env-file ../.env alembic revision --autogenerate -m "describe schema change"
# Review the generated migration before applying it.
uv run --env-file ../.env alembic upgrade head
```

For checks that do not require PostgreSQL, run `uv run pytest -m "not database"`. The full CI suite includes database tests. To stop the local database services without deleting the development volume, run `docker compose --profile test stop` from the repository root. Stopping the test service discards its temporary database contents.

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
