# Resume frontend

Authenticated users manage saved original PDFs at `/app/resumes`, opened from
the active Resume workspace card. Upload does not require Career Profile and
does not extract text, render PDF, score/improve content, or update profile facts.
The [backend contract](resumes.md) defines admission, ownership, storage and failure
semantics. There is no download endpoint or frontend download/preview action.

## Transport and validation

`api/resumes.ts` provides only list, upload and delete helpers. Each accepts
AuthProvider's `requestAuthenticated`; the feature never accesses credentials.
The Provider retains sole ownership of Bearer headers, Session generations,
private-request cancellation and recognized current `401 authentication_required`.

`api/client.ts` passes FormData directly and omits Content-Type so the browser
creates the multipart boundary. Other bodies retain JSON.stringify and
application/json; undefined bodies have no Content-Type. Accept, credentials:
omit, signal handling and request-level timeout behavior are unchanged.

Upload sends exactly one `file` part with the original filename and declared
`application/pdf` MIME, including when browser File.type is empty/inaccurate.
This declaration is not content validation. UI preflight checks case-insensitive
`.pdf`, nonempty bytes and the exact 5 MiB bound. The backend independently checks
filename, MIME, signature, binary size and total multipart size. Upload has a
60-second timeout; reads/deletes use 15 seconds. There is no percentage progress
or automatic write retry.

Helpers validate UUID, filename/status, nullable legacy MIME/size, positive safe
integer sizes, timezone-aware ISO calendar dates and list shape. Microseconds are
normalized to browser millisecond precision for display. List responses must
match the requested limit/offset, contain at most 100 items and have unique IDs.
New-upload responses must satisfy non-null PDF MIME/size and uploaded status.
Known fields are copied; extra fields are ignored rather than rendered. Malformed
success becomes invalid_response. Resume helpers require their exact 200/201/204
success statuses through an optional client expectedStatus check. Auth/Profile
do not opt into this check. DELETE expects the client's empty 204 result.

Error status/code pairs are allowlisted; Resume-specific Chinese copy never
renders raw detail, response text, SQL/OS exceptions or validation inputs. Auth
and Career Profile retain their existing JSON and validation wording. Storage
and DB 503 share user-facing availability wording but retain separate codes.

## Page state and confirmed writes

The page holds server items/read error, selected File, one tagged activity
(loading, idle, uploading, deleting an ID, refreshing), and success/error/recovery
notice. Empty state is derived from a successfully read empty list. Initial read
failure offers a manual GET retry. Buttons are disabled while an operation owns
the page; a synchronous controller ref also prevents duplicate submissions.

Each row shows filename, binary-based formatted size, browser-local upload time,
friendly status and accessible delete action. Uploaded maps to 已上传; unknown
legacy status maps to 状态暂不可识别. Null size shows — and null MIME is labeled
unrecorded. Neither IDs nor storage identities are shown.

After a validated upload success, its canonical server resource is immediately
merged by ID into the visible list, capped at 100. Selection and native input
are cleared, success is announced, then a GET refresh replaces the list. Failed
refresh preserves the confirmed resource and success, with a separate
“上传已确认，但列表刷新失败” notice. This is confirmed server data, not an optimistic
guess. DELETE 204 immediately removes the row and announces success, then refreshes
to fill the first page. Refresh failure does not undo either confirmed write.
Read failures retain previously read/confirmed data and pause new writes until
a successful manual read.

Deletion requires native window.confirm because it removes original binary.
409 resume_in_use preserves the row with a specific message; 404 triggers a
stale-list refresh rather than claiming a new successful delete.

## Uncertain outcomes and recovery

Network failure, timeout, malformed successful response and unknown infrastructure
errors after a write do not prove rejection. The page preserves selected upload
File where practical, announces uncertainty and attempts GET reconciliation.
POST's two recognized Resume 503 codes are conservatively treated the same way:
resume_unavailable can include uncertain COMMIT; the current upload flow also
closes the multipart spool after resource creation, so a storage 503 cannot be
used as a universal guarantee that creation never committed.

Recognized POST 401/413/415/422 are rejections, shown without reconciliation.
DELETE storage 503 reports a storage failure and metadata rollback, but unlink
may already have succeeded before fsync failure. Its refreshed metadata does not
establish binary health. DELETE DB 503 can have uncertain commit outcome. Both
refresh the list; no DELETE is automatically repeated.

A successful reconciliation replaces visible items and announces only current
server list state. Filename/size matches are not upload receipts. An absent item
is not a delete receipt. Recovery failure keeps old data and selection, blocks
writes, and offers GET-only retry. The page records the uncertain upload's local
file identity (name, size, lastModified). After a successful read, a selected file
with that identity, including a newly selected File object, uses retry wording
and requires confirmation that another POST can duplicate the earlier upload.
Selecting a different identity keeps the recovery notice but uses ordinary upload
wording without that confirmation. This identity is a UI heuristic, not a content
hash or server receipt. A subsequent delete also requires user confirmation.
GET cannot establish that a still-running earlier request will never complete.

## Pagination, navigation and accessibility

V1 fetches `GET /resumes?limit=100&offset=0`, with no pagination controls or total
count. The page states the 100-resource limit and warns on a full page that older
items may not be displayed. Concurrent writes can shift which items appear;
absence from this bounded list never proves resource absence.

Controller identity and aborted-signal checks guard every asynchronous state
update. StrictMode replay aborts its old read; unmount aborts the current page
operation and invalidates its identity, including follow-up GET. Late results
cannot trigger updates or recovery calls. Route key uses currentUser.id for
different-user isolation. Session correctness remains in AuthProvider.

Selected local File is not a persisted business draft: no dirty-navigation guard
or beforeunload handler is installed. Back, logout and browser navigation remain
available during writes. Abort stops client waiting; it does not promise server
rollback, particularly with backend DELETE's protected destructive section.
Returning to the page reads current state without replaying a previous write.

The page retains shared shell/header/brand/buttons, responsive white/lavender
surfaces, Simplified Chinese, route title and heading focus. File labels/help/error
association, busy/disabled state, live status/success/error announcements and
filename-specific delete names support keyboard and assistive technology use.
File/status/size/date presentation and operation behaviors have focused tests.
Run README's frontend lint, test and build validation commands; real-browser checks
should also exercise the proxy, multipart upload/delete, native confirmation,
keyboard focus and mobile layout.
