# Career Profile Backend

CareerProfile is the current user's confirmed career background, skills, and direction. It is product data, separate from authentication identity, temporary resume extraction, LLM suggestions, and JobAnalysis results.

Each User has zero or one CareerProfile. Registration does not create one; a missing Profile is a normal onboarding state. An explicitly created empty Profile is a different state. Future parsers and agents may propose changes, but must obtain user confirmation and use the same revision-checked write operation instead of silently replacing confirmed data.

## V1 fields and normalization

| Field | Meaning and rules |
| --- | --- |
| `career_summary` | Nullable short self-description of background, experience, and career direction; at most 2,000 Unicode code points after normalization. |
| `skills` | String array; at most 100 raw items; each normalized name is 1–80 Unicode code points. |
| `target_roles` | String array; at most 10 raw items; each normalized name is 1–120 Unicode code points. |

Inputs use strict strings and arrays, NFC Unicode normalization, and trimming of outer whitespace. A blank summary becomes `null`. CRLF and CR in summaries become LF, and normal line breaks are allowed. Control characters (Unicode category `Cc`) and surrogate characters (`Cs`) are rejected before trimming, except LF in a summary. Names must be nonblank and single-line; Unicode line/paragraph separators are also rejected in names. There is no broad format-character or bidi policy, NFKC conversion, alias merging, or internal whitespace collapsing.

Arrays are deduplicated using normalized names' Unicode `casefold()` values. The first spelling/case and input order are preserved. The raw count limit is checked before deduplication: 101 copies of `Python` are invalid. `null` is invalid for arrays; use `[]` to clear them. `[" Python ", "python", "FastAPI"]` becomes `["Python", "FastAPI"]`; `JS` and `JavaScript` remain separate.

The backend is the validation source of truth. V1 does not define levels, years, evidence, skill taxonomy, salary, location, seniority, or remote preferences. Unknown request fields are rejected.

## API contract

All three operations require `Authorization: Bearer <JWT>` and use the existing [authentication dependency](authentication.md). The URL always identifies the current authenticated user's Profile; clients cannot choose an owner.

| Endpoint | Request | Success |
| --- | --- | --- |
| `GET /career-profile` | No request body | `200` Profile |
| `POST /career-profile` | Optional `career_summary`, `skills`, `target_roles`, defaulting to `null`, `[]`, `[]` | `201` Profile, `Location: /career-profile` |
| `PUT /career-profile` | Required `expected_revision` and all three business fields | `200` replacement Profile |

POST creates only; `{}` is valid. A second POST is a conflict and cannot change the existing Profile. PUT updates only and is not an upsert. It replaces all three business fields: omitted fields are invalid, not an instruction to retain their old values. Clear them explicitly with `null` and `[]`.

Example PUT:

```json
{
  "expected_revision": 3,
  "career_summary": "Backend developer moving into AI application engineering.",
  "skills": ["Python", "FastAPI"],
  "target_roles": ["AI Engineer"]
}
```

GET, POST, and PUT return the same strict response shape:

```json
{
  "revision": 4,
  "career_summary": "Backend developer moving into AI application engineering.",
  "skills": ["Python", "FastAPI"],
  "target_roles": ["AI Engineer"],
  "created_at": "2026-10-10T02:00:00Z",
  "updated_at": "2026-10-10T02:15:00Z"
}
```

Timestamps are timezone-aware datetimes serialized by FastAPI/Pydantic using the standard datetime JSON representation; clients should parse offsets instead of relying on a literal `Z` suffix. No custom timestamp string formatter is used. Responses omit `user_id`, authentication identity, Session, and `expected_revision`. POST does not accept `revision` or `expected_revision`; clients cannot set timestamps.

| Failure | Status | JSON `detail` |
| --- | --- | --- |
| Missing, invalid, expired, revoked, or disabled authentication | 401 | `authentication_required` |
| Invalid JSON, fields, types, normalization rules, or incomplete PUT | 422 | `invalid_request` |
| Current user has no Profile | 404 | `career_profile_not_found` |
| POST when a Profile already exists | 409 | `career_profile_already_exists` |
| PUT with a nonmatching revision | 409 | `career_profile_conflict` |
| Body larger than 128 KiB of actual streamed bytes | 413 | `request_too_large` |
| SQLAlchemy/database failure, including authentication-store failure | 503 | `career_profile_unavailable` |

401 retains `WWW-Authenticate: Bearer`. Validation errors omit Pydantic's private raw input and context. Database errors never return SQL, parameters, or constraint names. Unrecognized programming errors and invalid stored response data propagate normally to the framework's generic 500 handling; they are not caught as availability errors or turned into validation errors. Production runs without debug responses.

The path-scoped middleware applies `Cache-Control: no-store` to normal success and handled validation, authentication, domain, and database-error responses, including redirects at the trailing-slash form. It counts received body chunks and does not trust Content-Length. Framework-generated unhandled 500 responses occur outside this middleware and contain only the framework's generic error body.

## Ownership and transactions

Authorization follows:

```text
Bearer JWT → get_current_user → current_user.id → owner-scoped SQL
```

Every Profile SELECT, UPDATE, and existence check explicitly filters by this owner. INSERT sets `user_id` from the authenticated User. `user_id`, `owner_id`, and `profile_user_id` are forbidden body fields, and query parameters do not select an owner. There is no `/career-profiles/{user_id}` route. The service does not verify JWTs, inspect Sessions, or duplicate active-user logic.

Authentication finishes its short read transaction before returning the User. Profile operations then start their own short `async with db.begin()` transaction on the request's cached AsyncSession. GET materializes and validates its response inside the transaction and ends it before returning. POST and PUT validate their returned data inside the transaction and return only after commit. Errors roll back; request cleanup is a fallback, not the normal transaction boundary. There is no implicit autocommit, request-wide transaction, or network/LLM work inside these transactions.

## Revision and optimistic concurrency

Creation starts at revision 1. Each successful PUT increments revision by one, even when normalized business content is identical. GET, validation failures, conflicts, and rolled-back writes do not increment it. Revision is a concurrency version, not a history ID, schema version, timestamp, or audit log.

`expected_revision` is a strict positive integer: booleans, floats, numeric strings, zero, and negative numbers are invalid. Clients submit the revision they loaded, not a chosen new revision. This increment uses the request body, with no ETag/If-Match protocol.

Writes use a single compare-and-swap statement with bound parameters:

```sql
UPDATE career_profiles
SET career_summary = :career_summary,
    skills = :skills,
    target_roles = :target_roles,
    revision = revision + 1,
    updated_at = clock_timestamp()
WHERE user_id = :current_user_id
  AND revision = :expected_revision
RETURNING revision, career_summary, skills, target_roles, created_at, updated_at;
```

At PostgreSQL's READ COMMITTED isolation level, a competing writer waits for the row, then re-evaluates the revision condition after the winner commits. Two writes using the same revision cannot both succeed. The service does not read a revision, compare it in Python, then perform an unconditional UPDATE. `updated_at` explicitly uses `clock_timestamp()`; `created_at` stays unchanged. Timestamps do not govern concurrency.

If UPDATE returns no row, an owner-scoped existence SELECT in that same short transaction distinguishes a missing Profile (404) from a revision conflict (409). POST uses `INSERT ... ON CONFLICT (user_id) DO NOTHING RETURNING ...`; only this primary-key conflict becomes `career_profile_already_exists`. Other database failures remain safe 503 errors.

After a conflict, keep the user's unsaved edits and fetch the latest Profile. Do not automatically replace `expected_revision` with the latest value and overwrite somebody else's work. A retry of an already committed PUT with its old expected revision conflicts without another write. After an uncertain network outcome, use GET to inspect current state; revision is not an idempotency receipt. There is no automatic retry, merging, profile deletion, or history recovery in this API.

## Persistence and testing

This increment needs no migration. The existing `user_id` primary key/foreign key ensures zero or one Profile per User. Existing JSONB CHECKs ensure array shape, and revision must be positive. ORM array annotations are narrowed to `list[str]`, but that is application typing, not a new database constraint.

Raw SQL writers can still put non-string or noncanonical values into those arrays. The API validates stored responses strictly and fails safely instead of silently coercing, normalizing, or repairing them. A future schema change should address independent writers or structured skills only when real requirements justify it.

There is no PostgreSQL RLS. Current isolation relies on verified authentication, owner-scoped SQL, foreign keys, and real cross-user integration tests. RLS remains a separate hardening change.

Tests use the existing dedicated PostgreSQL database and disposable Alembic-built schemas, never SQLite or `create_all()`. They cover lifecycle, auth rejection, normalization, boundaries, strict response data, owner isolation, transaction cleanup, real rollback, safe errors, streaming bounds, OpenAPI, and competing creation/update requests. A deterministic two-session test holds an uncommitted winning CAS, observes the stale writer's real PostgreSQL lock wait, then commits and verifies conflict and final revision 2.

Run the backend validation commands from the [README](../README.md). Existing migration tests retain upgrade/downgrade/upgrade and model-drift checks. The frontend transport can later use `requestAuthenticated()`; its CareerProfile types, error-code mapping, and UI belong to the next PR. This increment adds no AI, parsing, RAG, MCP, taxonomy, history tables, or dependencies.
