# Resume upload, storage, extraction, and structured interpretation

See [Resume frontend](frontend-resumes.md) for `/app/resumes`, confirmed writes,
uncertain-result recovery and the first-100 list limitation. This document remains
the backend contract and storage reference.

A Resume is a saved original file resource owned by a platform User. PostgreSQL
stores metadata and extracted source text; private storage holds binary. Explicit
PDF text extraction and explicit DeepSeek structured interpretation are available.
Upload does not parse, run a model, or update CareerProfile. Neither upload nor
extraction requires an existing CareerProfile.

## API

All operations require an explicit Bearer JWT and reuse the platform User/session
dependency. Owners come only from `current_user.id`.

| Endpoint | Request | Success |
| --- | --- | --- |
| `POST /resumes` | Multipart, exactly one file part named `file` | 201 metadata; `Location: /resumes/{id}` |
| `GET /resumes` | `limit` 1–100, default 20; `offset` 0–9223372036854775807, default 0 | 200 `{items, limit, offset}` |
| `GET /resumes/{resume_id}` | UUID | 200 metadata |
| `POST /resumes/{resume_id}/extract` | UUID; no request body/options in OpenAPI | 200 metadata after extraction attempt or cached result |
| `POST /resumes/{resume_id}/interpret` | UUID; no request body/options in OpenAPI | 200 validated interpretation or compatible cached result |
| `GET /resumes/{resume_id}/interpretation` | UUID | 200 validated persisted interpretation |
| `DELETE /resumes/{resume_id}` | UUID | 204, no body |

List orders by `created_at DESC, id DESC`, without a total count. Concurrent writes
can shift offset pages. Queries/deletes always filter by owner. Another owner's
UUID and a missing UUID both return 404. No download/version-linking API exists.

```json
{
  "id": "00000000-0000-4000-8000-000000000001",
  "original_filename": "My.Resume.PDF",
  "media_type": "application/pdf",
  "size_bytes": 182734,
  "status": "uploaded",
  "created_at": "2026-10-11T02:00:00Z",
  "updated_at": "2026-10-11T02:00:00Z"
}
```

Storage key/root/path, owner, extraction content, and lineage are omitted.
New uploads have real non-null MIME/size. Legacy rows may have null MIME/size and
retain their existing status. Datetimes are timezone-aware; parse offsets rather
than requiring `Z`. `status` maps existing `parsing_status`; new rows use
`uploaded`, with extraction and structured content null. It records a saved
resource, not current disk health or parsing progress.

Handled responses use `Cache-Control: no-store` and errors `{"detail": "code"}`.
Validation is Resume-local and does not change Authentication/Career Profile.

| Status | Detail | Meaning |
| --- | --- | --- |
| 401 | `authentication_required` | Missing/invalid/expired/revoked auth or inactive User; Bearer challenge retained |
| 404 | `resume_not_found` | Missing or other owner's resource |
| 422 | `invalid_request` | Invalid UUID/pagination, malformed/non-multipart request, missing/empty file, extra fields, too many parts, or invalid filename |
| 415 | `unsupported_resume_type` | Extension, MIME, or signature rejected |
| 413 | `request_too_large` | Actual binary or raw request limit exceeded |
| 409 | `resume_in_use` | Known lineage/JobAnalysis foreign key blocks deletion |
| 409 | `resume_extraction_conflict` | Incompatible extraction lifecycle or changed storage identity |
| 503 | `resume_storage_unavailable` | Storage or cleanup failure |
| 503 | `resume_unavailable` | Database failure, including authentication storage |

Responses omit raw validation inputs, parser internals, filesystem paths, and
SQL/OS exceptions. Unexpected programming errors retain generic 500 handling;
deployment must keep debug responses disabled.

## Admission and bounded multipart

PDF only: case-insensitive `.pdf`, exact file-part MIME `application/pdf`, and
leading `%PDF-` signature. Missing MIME and `application/octet-stream` fail.
These are admission checks, not proof of a complete, extractable, or safe PDF.
Encrypted/scanned/internally malformed PDFs or active content may still be saved;
none is executed or publicly served. DOCX, OCR, and malware scanning are deferred.
Text extraction and interpretation are separate explicit actions.

Filename spelling is preserved only as metadata, at most 255 UTF-8 bytes.
Empty/whitespace-only names, exact `.`/`..`, controls/surrogates, NUL, `/`, `\`,
and `:` fail. Normal dots such as `resume.v2.pdf` are allowed. Filename never
determines disk paths.

The exact binary limit is **5 MiB (5,242,880 bytes)**: enough export/image overhead
for short resumes while bounding I/O. Constants define a consistent API policy.
Raw multipart permits an additional 64 KiB: **5,308,416 bytes total**.

Authentication completes before explicit Resume-local Starlette multipart parsing
with `max_files=1, max_fields=0`. A small parser subclass requires the closing
boundary and raises inside the protected stream loop so unfinished spools close.
The single part must be named `file`; owner/status/storage fields fail. Automatic
FastAPI File parameters parse before authentication dependencies, so the schema
is instead documented explicitly in OpenAPI.

Starlette spools UploadFile, currently spilling to system temporary storage near
1 MiB. `max_part_size` bounds ordinary fields, not file bytes. ASGI middleware
counts raw request chunks before spooling. Storage reads a 5-byte signature prefix
and chunks of at most 64 KiB, counts actual bytes, and stops before writing an
over-limit chunk. Content-Length and file metadata are not authoritative.
This is bounded spooling followed by chunked saving, not socket-to-final-file
streaming. Parser spool and application temporary files close on ordinary failure.

## Private local storage

Set `RESUME_STORAGE_DIR` in the ignored environment file. Following README
commands from `backend/`, `./var/resumes` resolves to Git-ignored
`backend/var/resumes/`. Relative paths resolve once at startup. Imports create
no directories/read no environment; Alembic requires only DATABASE_URL.

Startup creates/checks root and `.tmp/`. New directories use 0700, files 0600.
Both directories must have exactly POSIX mode 0700 (owner rwx, no group/other
permissions or special bits). Existing insecure directories fail closed with
`resume_storage_unavailable`; startup and storage operations validate the mode
without changing operator permissions. Directories must be application-controlled;
no recursive chmod or protection against privileged local attackers is promised.
Keep root outside web/static paths. POSIX hard links and directory fsync are
required (macOS/Linux). Local deployment needs persistent disk; independent
ephemeral disks across instances are unsupported.

Independent UUID hex keys are the only accepted object names. DB makes
`storage_key` unique. Storage rejects unsafe keys and symlink objects/directories.
Atomic publication uses a same-filesystem hard link to the final name, followed
by temporary unlink: an existing binary cannot be overwritten. File data and
the final directory entry are fsynced before save returns.

`ResumeStorage` exposes only `save(key, chunks, max_bytes)`,
`read(key, max_bytes) -> bytes`, and `delete(key)`.
Services know no paths. Thread-offloaded I/O completes before cancellation
propagates, preventing a writer continuing after cleanup. Tests inject real
temporary roots. Object storage may replace this boundary later; no cloud SDK,
bucket configuration, repository framework, or plugin registry is added.

Read validates the opaque key and both private directories, opens with
`O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC`, then uses `fstat` to require a
regular file. Nonblocking open prevents a FIFO from hanging before validation.
It reads only that descriptor, at most `max_bytes + 1` bytes in bounded chunks,
and closes it in `finally`. Declared file size is an early rejection only; actual
bytes enforce the limit. Missing files, symlinks, directories, FIFO/devices,
permission/OS failures, and oversized objects become `StorageUnavailable` with
no path or OS message. Descriptor cleanup finishes before cancellation propagates.
Non-null database `size_bytes` must match the bytes read; mismatch returns 503
without parsing. Legacy null sizes still get the bounded read.

## Explicit PDF text extraction

`POST /resumes/{resume_id}/extract` authenticates before owner lookup and accepts
no extraction options. It does not consume or validate an arbitrary request body.
The response is the same metadata-only `ResumeResponse` as GET, with no source
text, structured content, or storage key. There is no public text endpoint.
The existing frontend has no extraction button or new status mapping; its
unknown-status fallback remains valid for resources extracted through the API.

This is best-effort deterministic source-text extraction, not complete or
lossless PDF reconstruction. Base `pypdf>=6.20.0,<7` reads with `strict=True`.
Encrypted PDFs are rejected, including documents the library can open with an
empty password: the application takes no password and never calls `decrypt`.
Digital text may be usable; scanned/image-only or blank pages may yield no text.
No OCR, model, API key, external decoder executable, or AI configuration is used.
The application does not separately traverse attachments, outlines, images, or
XMP for extraction. Parser warnings alone do not turn a usable result into failure.

| Persisted status | `extracted_text` | Explicit POST behavior |
| --- | --- | --- |
| `uploaded` | null | Read and extract |
| `extraction_failed` | null | Read and extract again; manual retry only |
| `extracted` | Nonempty, non-whitespace text | Return cached metadata without read/parse/timestamp change |
| `text_unavailable` | null | Return cached metadata without read/parse/timestamp change |
| Unknown status or inconsistent text/status | Existing value | 409; no repair, backfill, or overwrite |

A completed attempt commits `extracted`, `text_unavailable`, or
`extraction_failed` and returns **200 metadata** for all three. Encrypted,
malformed, and resource-policy failures persist `extraction_failed` with null
text; they do not produce a document-specific HTTP 422/413. No empty string or
partial prefix is saved. Infrastructure failure returns 503 and does not
intentionally change the lifecycle; a transport failure during COMMIT can have
an uncertain outcome. There is no automatic retry after any uncertain write.
Request/auth/resource errors are 422 `invalid_request`, 401
`authentication_required`, 404 `resume_not_found`, or 409
`resume_extraction_conflict`. A cross-owner UUID remains indistinguishable from
a missing UUID. Existing upload-only 413/415 errors do not apply to this action.

Normalization converts CRLF/CR to LF, trims trailing ASCII space/tab per line,
removes whitespace-only lines at page boundaries, and collapses blank runs to
one blank line. Empty pages are skipped; nonempty pages join with exactly
`"\n\n"`. Leading indentation, Unicode, combining characters, and source wording
are preserved; there is no NFC/NFKC, rewriting, inference, or section reordering.
NUL/surrogate output fails extraction. Whitespace-only final text is unavailable.

### Resource policy

Code-level V1 policy bounds short resume processing without adding env settings:

| Boundary | Limit | Purpose |
| --- | --- | --- |
| Stored binary | 5 MiB | Reuse upload policy and bound materialized input |
| Pages | 10 | Keep this endpoint focused on short resumes |
| Raw extracted UTF-8 text, summed across pages | 512 KiB | Bound text before normalization, including discarded whitespace |
| Final normalized UTF-8 text, including separators | 256 KiB | Bound persisted source text without truncation |
| Cooperative parser budget | 30 seconds | Check before reader/page work, between pages, and before returning text for persistence |
| Read/parse pipelines | 2 per application process | Bound simultaneous binary materialization and parser work |

Scoped public `apply_configuration(Configuration(...))` sets declared stream
length to 5 MiB; decoder output/array/image buffers to 4 MiB; filters per stream
to 8; decoding work to **16 MiB per stream**; page-tree entries/depth to 1000/20;
and the library-side Form invocation bound to 1000. Stream/tree limits allow
ordinary font/layout overhead while rejecting amplification or excessive traversal.
`jbig2dec_binary=None` prevents an external decoder. There is no custom
whole-document content counter, private-object traversal, or double decoding.
Policies fail the attempt rather than silently truncating text.

An extraction-local public `visitor_operand_before` counts `Do` operations
across all pages and nested Forms, with a **1000 XObject operation** limit.
Image XObjects also count as a conservative work policy. The visitor retains
an exceeded flag, checked after each `extract_text()` returns: pypdf can catch
nested callback exceptions and its Form limit can silently skip content.
Exceeding this policy fails the whole attempt as `extraction_failed` with null
text, so a known resource-limited prefix is never saved as `extracted`.
General PDF semantics can still make best-effort extraction incomplete; ordinary
parser warnings alone remain insufficient to fail an attempt.

The time budget is cooperative, not a hard timeout: one third-party parser call
can exceed it before the next checkpoint. There is **no hard CPU or RSS
isolation**, and a Python worker thread cannot be forcibly killed. No subprocess,
worker queue, persisted `extracting`, or background completion job is introduced.

### Transactions, concurrency, and cancellation

Sequence: short owner-scoped snapshot transaction → release DB connection →
acquire dedicated AnyIO pipeline limiter → bounded storage read → thread-offloaded
parse → release limiter → cancellation checkpoint → protected short final
transaction → owner/id/storage-key/lifecycle recheck under `SELECT FOR UPDATE` →
UPDATE status/text/`updated_at` → commit → 200 metadata. Read and parsing hold
neither PostgreSQL connections nor row locks. Limiter waiters hold neither
materialized binary nor DB connections. Extraction leaves original filename,
binary, `created_at`, `structured_content`, CareerProfile, lineage, and JobAnalysis
unchanged. No schema or migration change is needed.

Duplicate deterministic computation is allowed. The final transaction serializes
only recheck/update: a previously committed `extracted` or `text_unavailable`
result wins over a later failure, and returns cached current metadata. Changed
storage identity or incompatible lifecycle returns 409. Concurrent DELETE with
no final row returns 404 and discards the parsed text; there is never INSERT,
upsert, or resurrection. If storage read fails after DELETE, a short owner lookup
distinguishes missing metadata (404) from unavailable binary (503).

The parser uses AnyIO `run_sync(..., abandon_on_cancel=False)` off the event loop.
Tests in this asyncio environment demonstrate that direct `Task.cancel()` can
bypass AnyIO CancelScope shielding, end the host await, and release its thread
limiter before the worker stops. Therefore a narrow `complete()` ownership
boundary protects the parser await through repeated task cancellation, with the
dedicated two-token limiter held around the entire read/parse pipeline. The worker
must finish before cancellation propagates and the token is released; its result
is discarded and no extraction UPDATE starts. An explicit cancellation checkpoint
after parser completion also observes AnyIO scope cancellation before DB handoff.
This does not promise a prompt cancellation response for a stuck parser call.

Once the final transaction is handed to its protected operation, that operation
solely owns the request session until transaction exit. UPDATE and commit, or
rollback on failure, complete before cancellation propagates or session close,
including repeated cancellation. Cancellation there may leave a successfully
committed extraction without a response. Confirmed DB rejection rolls back;
transport failure at COMMIT can either have committed or not. Binary is unchanged
in all extraction DB failure cases, and the action does not retry automatically.

Raw pypdf logging is suppressed once at application startup with a null handler
and propagation disabled, not toggled or captured per request. Application extract
logs contain only `operation=extract`, Resume UUID, and an allowlisted reason:
`encrypted`, `malformed_pdf`, `page_limit`, `decoder_limit`, `text_limit`,
`time_budget`, `invalid_text`, `parser_error`, `storage_unavailable`, or
`db_commit_uncertain`. They omit filename, source text/bytes, storage key/path,
raw parser/SQL/OS exceptions, and document-bearing tracebacks.

## Consistency and failure boundaries

Upload: authenticate/end auth transaction → bounded form/metadata validation →
generate IDs → stream/validate temporary binary → fsync/atomic publication →
short INSERT/response-validation transaction → commit → 201. No DB transaction
is held during upload/file writing.

Save failure cleans owned temporary/final files without INSERT. INSERT failures
and server-confirmed integrity rejection at COMMIT roll back and delete finalized
binary. Collision-safe publication prevents compensation touching existing data.
Transport/cancellation errors during COMMIT are uncertain: keep binary and log
operation/opaque key, without blindly deleting or retrying. A disconnect or lost
response after commit cannot undo a resource. GET/list observes state but is not
an idempotency receipt; V1 has no POST idempotency/deduplication.

Delete: authenticate → owner-scoped SELECT FOR UPDATE in transaction → execute
uncommitted DELETE to check foreign keys → unlink/fsync → commit → 204.
Preparation is cancellable: cancellation before the destructive section rolls
back the uncommitted DELETE and leaves binary untouched. At the handoff to
storage deletion, a protected operation takes sole ownership of the session and
transaction. Unlink/fsync and commit (or rollback on failure) finish before
request cancellation propagates, including repeated task cancellation. The
request closes its session only after that operation completes; cancellation
after unlink alone cannot cause metadata rollback. Cancellation while awaiting
commit is also deferred. A cancelled request may therefore have deleted the
resource successfully without returning 204.
Known references return 409 before touching binary, with no cascade. Storage
failure rolls back metadata and returns 503, retaining key for retry. Binary may
already be absent if unlink succeeded but fsync failed. Missing binary is an
idempotent storage success; unknown Resume is API 404.

If unlink succeeds but DB commit fails, binary cannot be restored; metadata may
remain pointing to a missing file. Retry DELETE completes cleanup, or returns 404
if the first commit succeeded. Metadata reads do not probe disk. Extraction
explicitly returns 503 for missing binary on an existing eligible resource.

Upload cancellation before COMMIT rolls back and attempts shielded cleanup;
upload cancellation during COMMIT retains binary on uncertainty as described above.
Cleanup failure logs operation/key, without exception text, filenames, tokens,
or paths. **Hard crash, process termination, cleanup failure, and unresolved
uncertain commit can leave orphan binary or temporary files.** There is no
startup sweep or offline cleanup command. Dry-run/apply reconciliation belongs
to future Storage Reconciliation / Operational Hardening.

## Schema, validation, and future work

Migration `0003` adds nullable `media_type TEXT`, nullable `size_bytes BIGINT`,
`CHECK (size_bytes IS NULL OR size_bytes > 0)`, and unique `storage_key`.
Legacy metadata stays null without guessed backfill; existing status/content/
lineage are preserved. Duplicate legacy keys fail migration rather than silently
altering identities. Downgrade loses new MIME/size metadata but preserves rows.

Tests use real Alembic-built PostgreSQL and temporary filesystems for ownership,
safe errors, exact size bounds, actual binary, permissions, collisions/symlinks,
write/commit/delete failures, uncertainty, compensation, cancellation, concurrent
deletion, real tiny PDFs, normalization/UTF-8/resource limits, extraction caching,
legacy conflicts, pipeline limits, DB-free parsing, transaction cancellation,
migration preservation/constraints, and model drift. Use README's
backend validation/migration commands.

Frontend management, backend extraction, and backend interpretation are available.
User review/confirmation and interpretation UI remain future work. No Resume
action silently changes CareerProfile or historical analysis inputs.

## Explicit structured interpretation

The three independent actions are upload → extract → interpret. pypdf extracts
machine-readable PDF source text; DeepSeek interprets that persisted text into
career facts. Interpretation never reads PDF binary or automatically extracts.
Only `parsing_status="extracted"` with nonblank `extracted_text` is eligible.
The action has no client input options: like extraction, arbitrary request bodies
are not consumed, and cannot override source text, prompt, model, or provider.
GET/list metadata remains unchanged. There is no public extracted-text endpoint.

**Calling interpretation sends the persisted extracted Resume text to the
configured DeepSeek API for model processing and can incur real API charges.**
The result is an LLM interpretation, not user-confirmed career truth; review is
still required before using it as confirmed profile data.

### Configuration and provider boundary

Only these environment variables are used:

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
RESUME_INTERPRETATION_MODEL=deepseek-flash
```

Keep real keys only in ignored `.env` or deployment Secrets. The existing dataclass
configuration style is retained. The interpreter loads settings on first use and
keeps that configuration for its lifetime; restart after changing it. Missing key
or invalid configuration fails uncached interpretation safely with 503. Application
startup, authentication, CareerProfile, upload/list/delete, extraction, and GET
interpretation do not require DeepSeek configuration. A compatible POST cache
needs the configured model identifier but does not require an API key.

`ResumeInterpreter` accepts text and asynchronously returns `StructuredResumeV1`.
`DeepSeekResumeInterpreter` implements it using `langchain-openai` ChatOpenAI and
`langchain-core` messages, pointing explicitly to DeepSeek's OpenAI-compatible API.
There is no OpenAI model or `OPENAI_API_KEY` configuration. The lock pins
`langchain-core==1.6.9` and `langchain-openai==1.7.0`.

The implementation uses `with_structured_output(StructuredResumeV1,
method="function_calling", strict=False, include_raw=False)`. Provider strict
mode requires DeepSeek's beta endpoint and has schema limitations, so standard
function output plus application validation is used. `parallel_tool_calls` is
disabled as an unsupported OpenAI-specific parameter. Thinking is explicitly
disabled via `extra_body={"thinking": {"type": "disabled"}}`; the Chat Completions
API is selected explicitly. No business tools are bound or executed. The SDK
request format and Pydantic parsing are exercised through an offline stub under
the locked versions. References: [DeepSeek tool calls](https://api-docs.deepseek.com/guides/tool_calls/),
[thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/), and
[ChatOpenAI](https://docs.langchain.com/oss/python/integrations/chat/openai).

Code constants set temperature 0, 8192 maximum output tokens, a 60-second SDK
request timeout, and zero automatic SDK retries. Clients close after each call,
including on cancellation; there is no detached LLM task or background completion.

### Schema and resource policy

All fields are required; absent facts use null or empty arrays. Every nested model
is strict and forbids extra keys. Strings retain source wording without trimming,
rewriting, date normalization, or silent truncation. Empty/whitespace strings,
NUL and Unicode surrogates are rejected.

| Model | Complete fields |
| --- | --- |
| `StructuredResumeV1` | `summary: str\|null`, `skills: str[]`, `experience: ResumeExperience[]`, `education: ResumeEducation[]`, `projects: ResumeProject[]`, `certifications: ResumeCertification[]` |
| `ResumeExperience` | `job_title`, `company`, `location`, `start_date_text`, `end_date_text` (each `str\|null`); `highlights: str[]` |
| `ResumeEducation` | `institution`, `degree`, `field_of_study`, `date_text` (each `str\|null`); `highlights: str[]` |
| `ResumeProject` | `name`, `description` (each `str\|null`); `technologies: str[]`, `highlights: str[]` |
| `ResumeCertification` | `name`, `issuer`, `date_text` (each `str\|null`) |

| Boundary | Limit |
| --- | --- |
| Persisted source sent to model | 32 KiB UTF-8; enough for ordinary short resumes, deliberately below the extraction limit |
| Names/titles/companies/locations/institutions/degrees/fields/issuers | 200 characters each |
| Dates, skills, technologies | 100 characters each |
| Summary/project description | 2000 characters each |
| Highlight | 1000 characters each; at most 10 highlights per entry |
| Skills / experience / education / projects / certifications | 100 / 20 / 10 / 20 / 20 entries |
| Technologies per project | 30 entries |
| Compact UTF-8 JSON, including persisted envelope | 64 KiB |
| Model concurrency | 2 per application process |

Pydantic output is revalidated in the service, including already constructed model
instances. The final envelope is size-checked too. Any malformed/oversized result
fails the whole interpretation; no truncated prefix or partial success is stored.
This bounds accepted data, not hard process memory for a misbehaving remote server.

### Prompt and privacy

A fixed system message defines resume text as untrusted data and forbids obeying
embedded instructions, inventing facts, inferring skills from job titles, filling
missing facts, or improving achievements. The source is a separate user message;
it is never interpolated into the system prompt. Contact numbers, home addresses,
identity documents and unrelated personal details are excluded from the schema
and prompt. The model has no database access or executable business tools.

Prompt instructions reduce injection/hallucination risk; structural validation
does not prove factual correctness or guarantee semantic obedience. There is no
evidence verifier or automatic CareerProfile mutation in V1.

Tracing is explicitly disabled in a scoped `tracing_context(enabled=False,
parent=False)` even when LangSmith environment switches are present. Callbacks
are empty; global LangChain debug/verbose mode causes a safe failure rather than
printing private inputs. SDK loggers are suppressed once at startup, following
the existing parser logging pattern. LangSmith/OpenAI/httpx/tiktoken and other
packages may exist as LangChain transitive dependencies; their presence does not
enable tracing, OpenAI model calls, or tokenization infrastructure.

Application interpretation logs contain only `operation=interpret`, Resume UUID,
and `model_unavailable`, `invalid_output`, or `db_commit_uncertain`. No raw
exception, source text, structured JSON, prompt, response, key/token, PDF bytes,
or storage identity/path is logged or included in errors.

### Persistence, caching, concurrency, and cancellation

The existing nullable JSONB `structured_content` stores this validated envelope:

```json
{
  "schema_version": 1,
  "model_identifier": "deepseek-flash",
  "source_sha256": "<64 lowercase hex characters>",
  "content": {
    "summary": null,
    "skills": [],
    "experience": [],
    "education": [],
    "projects": [],
    "certifications": []
  }
}
```

Version and model are application-owned audit metadata. The source digest prevents
reusing a cached interpretation after a source change. No new columns, tables,
backfill, or Alembic migration are needed; database head remains `0003`.
Legacy/malformed/incompatible structured content fails with 409 and is preserved.
There is no force reinterpret or automatic schema/model migration.

The API returns `{resume_id, schema_version, model_identifier, content}` through
`ResumeInterpretationResponse`; the envelope is validated before serialization
and the internal digest is omitted. GET can read valid saved results without
provider configuration, including after a configured model change. POST reuses
only current-version, matching-model, matching-source, valid stored results and
does not call the model or change `updated_at` on a cache hit.

Sequence: authentication ends its transaction → short owner-scoped snapshot
transaction → release connection → wait for two-token interpretation capacity →
async DeepSeek call → application validation → cancellation checkpoint → protected
short transaction → owner-scoped `SELECT FOR UPDATE` → verify storage key, exact
source text and lifecycle → accept a compatible already-persisted winner or write
the envelope and `updated_at=clock_timestamp()` → commit.

Capacity/model waits hold no transaction, PostgreSQL connection or row lock.
Duplicate calls can occur when requests race before a result is accepted. Final
transactions serialize acceptance and return the first compatible winner instead
of overwriting it. Changed source/storage/lifecycle returns 409; a deleted row
returns 404 without resurrection. No persisted `interpreting` status is added;
successful interpretation retains `parsing_status="extracted"`.

Cancellation during capacity/model waits propagates and discards results. An
explicit checkpoint after validation observes cancellation before persistence.
After final transaction handoff, existing `complete()` ownership defers even
repeated cancellation through commit/rollback and prevents premature session
close. Cancellation at that point can leave a committed result without a response.
A lost COMMIT response has an uncertain outcome; no automatic retry is performed.
Cancelling local HTTP work cannot guarantee the remote provider stops generation
or charging. The limiter is process-local, not a deployment-wide spend cap.

| Status | Interpretation-specific detail |
| --- | --- |
| 404 | `resume_not_found` (missing/other owner), `resume_interpretation_not_found` (eligible Resume has no saved result) |
| 409 | `resume_interpretation_not_ready` (not extracted/blank), `resume_interpretation_conflict` (changed source/lifecycle or invalid/incompatible existing data) |
| 413 | `resume_interpretation_input_too_large` |
| 503 | `resume_interpretation_unavailable` (config/provider/structured validation); existing `resume_unavailable` for database failures |

### Offline validation and cost policy

Every automated test and CI run must make **zero real DeepSeek calls**, require
no DeepSeek key, and use fakes or local SDK transport stubs. An autouse fixture
blocks external SDK HTTP transports and requests-based tracing transport for the
whole suite. Ten focused new tests cover readiness/input size, success/cache/API
privacy/profile preservation/DB-free model work, authorization, output limits,
safe provider/config failures, locked SDK payload/prompt/tracing behavior,
bounded concurrency/first accepted winner, changed source/deletion, cancellation
before persistence, and final commit ownership. Existing migration tests verify
the unchanged schema. Use the normal README validation commands.

No real smoke test is necessary for this change. Any later manual paid test must
state the expected call count before execution and use at most three calls;
prefer one ordinary Resume. Do not run repeated paid validation loops.
