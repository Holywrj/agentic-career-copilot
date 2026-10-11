# Agentic Career Copilot

A full-stack AI career copilot portfolio project, built incrementally.

## Documentation

- [Product](docs/product.md)
- [Architecture](docs/architecture.md)
- [Authentication API and security](docs/authentication.md)
- [Frontend authentication and browser behavior](docs/frontend-authentication.md)
- [Career Profile API and domain](docs/career-profile.md)
- [Resume upload, extraction, interpretation, and failure semantics](docs/resumes.md)
- [Resume frontend management and recovery](docs/frontend-resumes.md)

## Backend development

Requirements: Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and Docker with Compose for local PostgreSQL.

Run these commands from the repository root:

```sh
cp .env.example .env
docker compose --profile test up -d --wait db db-test
cd backend
uv sync --locked
# Generate a signing secret, then set JWT_SECRET in the ignored ../.env file.
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
uv run --env-file ../.env alembic upgrade head
uv run --env-file ../.env uvicorn career_copilot.main:app --reload
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run --env-file ../.env pytest
```

The example credentials are for local development only. Compose binds PostgreSQL to the loopback interface. The development database uses a persistent volume; the separate test database uses temporary storage. `.env` is ignored by Git.

`DATABASE_URL` is required at application startup and must use `postgresql+psycopg://`. `JWT_SECRET` is also required, has no default, and must be a strong random ASCII value of at least 32 bytes; never commit the generated secret. `JWT_SESSION_TTL_SECONDS` defaults to `1800` (30 minutes) and accepts integers from 1 to 3600. `RESUME_STORAGE_DIR` is required and identifies private binary storage; the example `./var/resumes` resolves relative to `backend/` under these commands. Startup creates the directory; uploaded files require persistent disk in deployment. Environment variables take precedence over the explicitly loaded env file. The application uses asynchronous Psycopg connections and never creates or migrates tables at startup. `/health` reports application liveness and does not query PostgreSQL.

The backend provides JSON registration/login and JWT Bearer authentication backed by PostgreSQL sessions. The [authentication contract](docs/authentication.md) describes the four endpoints, errors, and frontend integration.

Resume storage root and its `.tmp` directory require exactly POSIX mode `0700`, including existing directories. Startup and storage operations reject insecure permissions with `resume_storage_unavailable`; they do not chmod operator directories. New storage directories use `0700`, and uploaded files use `0600`.

Authenticated users can create, read, and replace their current CareerProfile through `POST`, `GET`, and `PUT /career-profile`. Profiles use user-confirmed summary, skill names, and target role names. Writes are owner-scoped and updates require an optimistic `expected_revision`; see the [Career Profile contract](docs/career-profile.md). The existing database schema supports this API without a new migration. The frontend editor preserves drafts through conflicts and uncertain write outcomes; see [frontend integration](docs/career-profile.md#frontend-integration).

Authenticated users can upload PDF resumes (5 MiB maximum), list/read their metadata, and delete unreferenced resources through `/resumes`. Binary is stored privately under generated keys, separate from PostgreSQL metadata. Explicit `POST /resumes/{resume_id}/extract` uses base pypdf to save best-effort PDF source text internally and returns metadata only. Completed attempts return 200 with `extracted`, `text_unavailable`, or `extraction_failed`; no OCR, LLM, API key, or new environment configuration is required. The `/app/resumes` management UI remains upload/list/delete only, with an unknown-status fallback for extraction states. See the [Resume contract](docs/resumes.md) for lifecycle, resource/cancellation limits, ownership, and failure semantics, and [frontend behavior](docs/frontend-resumes.md) for confirmed writes and uncertain-result recovery.

`TEST_DATABASE_URL` must name a separate database ending in `_test`. Database tests fail when it is missing or unavailable. Each test creates a random schema through Alembic, then removes only that schema. Do not point this setting at a database containing valuable data.

Explicit `POST /resumes/{resume_id}/interpret` uses persisted extracted text to obtain bounded `StructuredResumeV1` career facts from DeepSeek through LangChain. `GET /resumes/{resume_id}/interpretation` reads the validated saved result. Metadata APIs remain metadata-only, and no action changes CareerProfile automatically. Interpretation sends private extracted Resume text to the configured DeepSeek API and can incur real charges. Set `DEEPSEEK_API_KEY` only in ignored `.env` or deployment Secrets; `DEEPSEEK_BASE_URL` defaults to `https://api.deepseek.com`, and `RESUME_INTERPRETATION_MODEL` defaults to `deepseek-flash`. Settings load lazily on first interpretation use; restart after configuration changes. Missing/invalid DeepSeek settings do not block startup or non-AI features. All automated tests and CI use fakes/stubs with external SDK HTTP blocked: zero real model calls and no DeepSeek key required. No tracing or interpretation UI is enabled. See the [interpretation contract](docs/resumes.md#explicit-structured-interpretation) for schema, resource limits, caching, concurrency and cancellation.

Migration commands, run from `backend/`:

Alembic requires only database configuration, not `JWT_SECRET` or `RESUME_STORAGE_DIR`. Migration `0003` adds nullable Resume MIME/size metadata and unique storage keys; downgrade loses the new metadata while preserving Resume rows. Migration `0002` adds local credentials; it does not alter the existing User or Session tables. Downgrading to `0001` deletes local password hashes, so upgrading again does not restore login credentials.

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

Start the backend in a separate terminal using the backend instructions above (including `alembic upgrade head`). Open the URL printed by Vite, normally `http://localhost:5173`. Registration creates an account; sign in afterward to enter `/app`, the protected workspace. Its Career Profile card opens `/app/profile` to create or edit your career summary, skills, and target roles. The Resume card opens `/app/resumes` to upload PDFs, view the most recent 100 resources, and confirm deletion. Jobs and Job Analysis remain upcoming. The interface defaults to Simplified Chinese; use the browser’s built-in page translation for another language. No in-app language switch or translation service is included.

Frontend requests use relative `/api/*` paths. Vite proxies them to `http://127.0.0.1:8000` and removes `/api`, so `/api/auth/login` reaches backend `/auth/login`. This development model needs no backend CORS configuration. The proxy exists only in development: production hosting must provide the same `/api/*` routing/rewrite over HTTPS and serve `index.html` for frontend deep links, without rewriting API errors to HTML.

JWTs stay in AuthProvider memory. Refreshing, opening a new tab, or reaching the local expiry estimate requires signing in again. There is no token persistence or refresh flow. Explicit sign-out clears the browser immediately even if the server is unavailable; a warning distinguishes local sign-out from unconfirmed server revocation. See [frontend authentication](docs/frontend-authentication.md) for the architecture, failure policies, and testing boundaries.
