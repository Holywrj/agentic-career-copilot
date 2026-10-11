# Career Profile

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

Run the backend validation commands from the [README](../README.md). Existing migration tests retain upgrade/downgrade/upgrade and model-drift checks. This API adds no AI, parsing, RAG, MCP, taxonomy, history tables, or dependencies.

## Frontend integration

The protected `/app/profile` route is reachable from the Workspace's Career Profile card. Resume, Jobs, and Job Analysis remain informational upcoming features. The page uses the existing Simplified Chinese UI and CSS tokens, local React state, and the existing App title/heading-focus behavior. No new dependencies or backend changes are required.

`frontend/src/api/careerProfile.ts` accepts `AuthenticatedRequest` rather than importing React or auth Context. GET/POST/PUT use `requestAuthenticated('/career-profile', ...)`, which sends requests through `/api/career-profile` and the existing Vite proxy. Helpers check successful response shape (including positive safe-integer revision, nullable summary, string arrays, and string timestamps) and reject malformed data as `invalid_response`. Profile requests have a 15-second per-request timeout; there is no automatic retry.

### Canonical data and editable draft

The page keeps the last accepted server Profile separate from a copied, editable draft. The textarea represents `null` as an empty string; an empty string is sent as `null`. Editing never mutates canonical arrays. A successful write replaces both canonical Profile and draft with the complete server response, including normalized values and server revision; the client never increments revision.

| State | Frontend behavior |
| --- | --- |
| Initial GET pending | Show loading; no editor or write action yet. |
| GET 200 | Establish canonical Profile and copied draft. |
| Recognized GET 404 | Normal onboarding; establish an empty draft without a canonical Profile. |
| Initial GET failure | Safe error and explicit GET retry; unknown 404 is not onboarding. |
| Onboarding save | POST all three business fields; an explicitly empty Profile can be created. |
| Existing save | PUT all three business fields and canonical `expected_revision`; never patch or upsert. |
| No changes to existing Profile | Disable save; comparisons retain list order and exact draft text. |
| Save pending | Disable editing and use a synchronous controller ref to prevent duplicate submission. |
| Validated success | Use returned canonical data/revision and announce confirmed save. |
| 422 / 413 | Preserve draft and show Profile-specific validation/size guidance. |
| 409 / uncertain write / PUT 404 | Preserve draft, GET latest state, and block save until an explicit user decision. |

Skill/role inputs support Enter to add and labeled buttons to remove; removal returns focus to the input. Unadded list input is preserved, counts as unsaved input, and must be added or cleared before saving, so saving cannot quietly omit it. Basic Unicode code-point/quantity guidance helps users respect summary, skill, and role limits without HTML UTF-16 `maxLength`. The summary count accounts for NFC, line endings, and outer whitespace; final validation and Unicode casefold deduplication remain backend responsibilities. The UI permits duplicate names within raw item limits and accepts server deduplication on success.

### Conflict and uncertain write recovery

A PUT `career_profile_conflict` or POST `career_profile_already_exists` does not retry the write. The draft stays visible while a separate GET loads the latest server Profile. A PUT `career_profile_not_found` also preserves edits and checks current state rather than silently converting the request to a POST.

Network errors, timeouts, unavailable services, unrecognized HTTP errors, and malformed successful write responses all have uncertain outcomes: the server may have received or committed the write. The page immediately attempts a GET for reconciliation and labels the result as current server state, **not evidence that the preceding save succeeded**. A changed or matching revision/content is not an idempotency receipt; another request might have written the same or different values. A GET 404 is only an observation at that moment: an earlier write may still complete afterward.

The recovery panel shows the reason, the untouched editable draft, and all three fields of the latest server Profile (or explicit absence). It offers two actions after a successful GET:

- **Use server state to restart editing:** replace the draft with the displayed server state, confirming before discarding changed draft or unadded inputs.
- **Keep draft and continue against server state:** adopt the displayed server revision as the new editing baseline, retaining the draft. The UI explicitly says this is not automatic merging and that a later deliberate save replaces **all three** business fields. Selecting this action does not submit anything. A missing Profile becomes an explicit create baseline only after this choice.

Neither action claims that the previous uncertain write succeeded. The user must separately choose to save/create. Further concurrent changes can still produce another conflict; POST remains create-only and PUT remains revision-checked. If reconciliation fails, edits stay available but writes remain blocked; its retry button sends only GET. No automatic merge or POST/PUT retry is implemented.

### Errors, cancellation, and limitations

`api/client.ts` recognizes only allowlisted status/code pairs, including Profile-specific 404/409/503 classifications. `careerProfileErrorMessage()` in the feature API module owns the Chinese Profile error copy; shared 422 classification does not reuse username/password guidance. Raw backend detail, Pydantic payloads, exception text, and proxy HTML never appear in the page.

Recognized private `401 authentication_required` still ends the Session in AuthProvider and redirects via ProtectedRoute. The feature does not duplicate authentication state. Availability/network failures and unrecognized 401 bodies preserve the Session. Cancellation stays silent.

Initial load, writes, and reconciliation pass AbortSignal through the authenticated requester. Controller identity plus abort checks prevent obsolete responses from updating the page after reload, StrictMode cleanup, or unmount; AuthProvider separately guards Session generations. The route keys the page by current User identity so a different account cannot retain another account's draft. Aborting a request cannot undo a server commit.

Refresh/close uses the browser's `beforeunload` prompt when edits, unadded input, a pending write, or recovery state exist. The page's return/home links and logout also confirm before leaving. Browser history back/forward and navigation initiated outside the page are not blocked; browsers may suppress unload prompts. Drafts are not persisted, and Session expiry can still discard them. Refresh/new tabs require login again under the existing memory-only auth design.

API/page tests cover lifecycle, payloads and canonical normalization, raw list bounds, keyboard edits, safe errors, duplicate submission, conflict decisions, uncertain POST/PUT outcomes (including a real request timeout), GET-only recovery retries, and stale/unmounted requests under StrictMode. App integration tests use the real AuthProvider to verify protected routing, title/focus, and 401 behavior during load, save, and reconciliation. Run `npm run lint`, `npm run test`, `npm run build` from `frontend/`, and `git diff --check` from the repository root. DOM tests do not substitute for real proxy/database, visual, or full screen-reader checks.
