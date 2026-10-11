# Resume upload and storage

A Resume is a saved original file resource owned by a platform User. PostgreSQL
stores metadata; private storage holds binary. Extracted text and AI
interpretation are separate future outputs. Upload does not parse, run a model,
or update CareerProfile. Upload does not require an existing CareerProfile.

## API

All operations require an explicit Bearer JWT and reuse the platform User/session
dependency. Owners come only from `current_user.id`.

| Endpoint | Request | Success |
| --- | --- | --- |
| `POST /resumes` | Multipart, exactly one file part named `file` | 201 metadata; `Location: /resumes/{id}` |
| `GET /resumes` | `limit` 1–100, default 20; `offset` 0–9223372036854775807, default 0 | 200 `{items, limit, offset}` |
| `GET /resumes/{resume_id}` | UUID | 200 metadata |
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
none is executed or publicly served. DOCX, full parsing, OCR, malware scanning,
and content extraction are deferred.

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

`ResumeStorage` exposes only `save(key, chunks, max_bytes)` and `delete(key)`.
Services know no paths. Thread-offloaded I/O completes before cancellation
propagates, preventing a writer continuing after cleanup. Tests inject real
temporary roots. Object storage may replace this boundary later; no cloud SDK,
bucket configuration, repository framework, or plugin registry is added.

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
if the first commit succeeded. Metadata reads do not probe disk. Future extraction
must explicitly handle missing binary.

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
deletion, migration preservation/constraints, and model drift. Use README's
backend validation/migration commands.

Next: frontend management → extraction from saved binary → structured/LLM
interpretation. Add an internal read method when extraction needs it. Upload
never silently changes CareerProfile or historical analysis inputs.
