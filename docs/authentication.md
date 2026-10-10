# Authentication Backend

The current flow is local username/password → Argon2id → HS256 JWT Bearer → PostgreSQL AuthSession → `get_current_user`. PostgreSQL is authoritative for expiration and revocation; no Redis blacklist is used. OIDC is a future evolution, not part of this implementation.

## API Contract

All authentication responses have `Cache-Control: no-store`. Credentials are JSON, not OAuth password-flow form data. Successful login does not set a cookie.

| Endpoint | Request | Successful response |
| --- | --- | --- |
| `POST /auth/register` | `{"username":"alice","password":"a long private passphrase"}` | `201 {"id":"<User UUID>","status":"active","username":"alice"}` |
| `POST /auth/login` | Same JSON fields | `200 {"access_token":"<JWT>","token_type":"bearer","expires_in":1800}` |
| `GET /auth/me` | `Authorization: Bearer <JWT>` | `200 {"id":"<User UUID>","status":"active"}` |
| `POST /auth/logout` | `Authorization: Bearer <JWT>` | `204`, empty body |

`expires_in` is the remaining lifetime in seconds when the login response is built, so it can be less than the configured TTL. Registration does not log in, create a Session, or return a token. `/auth/me` does not define username as a property of all platform Users.

| Failure | HTTP status | JSON `detail` |
| --- | --- | --- |
| Invalid JSON, fields, username, or password policy | 422 | `invalid_request` |
| Credential request body larger than 8192 bytes | 413 | `request_too_large` |
| Existing normalized local username | 409 | `username_unavailable` |
| Unknown username, wrong password, disabled or unknown User status at login | 401 | `invalid_credentials` |
| Missing, malformed, forged, expired, unknown, mismatched, revoked, or disabled authentication | 401 | `authentication_required` |
| Database failure or unrecognized stored password hash | 503 | `authentication_unavailable` |

Both 401 responses include `WWW-Authenticate: Bearer`. Validation errors intentionally omit Pydantic input/context that could contain passwords. SQL parameters, password hashes, JWT decoder exceptions, and status details are not returned. Registration exposes availability as an explicit 409; login does not distinguish nonexistent users from incorrect credentials.

## Local Identity and Passwords

Registration and login share username normalization: strip surrounding whitespace, reject non-ASCII username characters, lowercase, and require 3–32 characters. The first character is alphanumeric; the rest allow ASCII alphanumeric, `_`, `.`, and `-`. Raw username input is capped at 64 characters. Renaming is not supported. Identity is `("local", normalized_username)`, protected by the existing database unique constraint. Only that constraint's uniqueness violation maps to 409.

Registration accepts passwords with 15–128 Unicode characters, rejects pure whitespace and control characters, and limits original and normalized UTF-8 input to 512 bytes before Argon2. Passwords use NFC Unicode normalization consistently at registration and login; spaces and letter case are preserved. Login applies input bounds but does not repeat registration's minimum-length policy, so incorrect short passwords receive the ordinary credential failure. No common-password list or external breach API is used.

`pwdlib[argon2]` owns Argon2id salts, encoding, and parameters. Its current recommended defaults encode 64 MiB memory, three iterations, and four lanes. Hash/verify operations use AnyIO thread offload with at most two concurrent operations per application worker. A random dummy hash is generated once at startup; unknown users still perform Argon2 verification. This reduces obvious timing differences but does not promise perfectly equal response timing or replace abuse controls.

Hashing occurs before registration's transaction. User and LocalCredential commit atomically. Login reads verification material, releases its transaction before Argon2, then locks and refreshes the User/credential before creating a Session. A concurrently changed credential or disabled User cannot pass the final login check. Neither plaintext passwords nor raw JWTs are persisted.

## JWT and Session Validation

Required claims are `sub` (User UUID string), `jti` (new AuthSession UUID string), integer `iat`, integer `exp`, `iss="career-copilot"`, and `aud="career-copilot-api"`. The JWT contains no password, hash, mutable User status, or career data. Every login uses a fresh Session UUID. `exp` exactly equals the database expiry, at integer-second precision; `iat` matches Session creation time when issued.

`JWT_SECRET` is required at application startup and has no default. Generate at least 32 random bytes, for example `secrets.token_urlsafe(32)`, and configure it through the ignored local `.env` or a deployment secret store. Configuration validates printable ASCII length and hides the secret from `repr`; it cannot infer randomness, so do not substitute a memorable string. `JWT_SESSION_TTL_SECONDS` defaults to 1800 and supports 1–3600. Database migrations require only `DATABASE_URL`.

The reusable `get_current_user` dependency performs:

1. Accept exactly one explicit Bearer Authorization header; reject cookie/query/body authentication.
2. Validate using only `algorithms=["HS256"]`, signature, expiration, fixed issuer/audience, and all required claims. Validate UUIDs, integer NumericDates, representable timestamps, and `iat < exp`. Allow at most 30 seconds of future `iat` clock skew; expiration has no grace period. Tokens are capped at 4096 ASCII bytes.
3. Fetch Session by `jti`, joined to its User; require `session.user_id == sub`.
4. Compute SHA-256 over the exact raw JWT and compare with the stored hash in constant time. A differently encoded or reissued JWT cannot reuse the same Session merely because its claims match.
5. Require exact JWT/DB expiry equality, a future DB expiry, no revocation, and an active User; unknown status fails closed.
6. Return the platform User for subsequent resource ownership checks. Authentication alone does not authorize access to another User's resources.

JWT ≠ User and JWT ≠ AuthSession. JWT signature validation is necessary but insufficient. Hash-only storage prevents a read of the Session table from directly yielding reusable Bearer credentials; this does not protect against signing-key compromise or an attacker with database write access. All application workers must use the same signing key. Replacing the key invalidates existing JWTs; no key ring or automatic rotation is implemented.

Logout validates the same JWT/Session relationship and expiry, locks the matching rows, conditionally sets `revoked_at`, and commits before returning 204. Already-revoked matching sessions still return 204 while the JWT is unexpired; disabled Users may also revoke their own matching sessions. Missing, forged, expired, unknown, and mismatched tokens still return 401. Other sessions are unaffected. Requests whose authentication check runs after revocation commit fail; logout cannot undo an operation that already passed authentication.

## Browser and Operational Boundaries

Frontend code sends `Authorization: Bearer <access_token>` for private APIs and logout, handles 401 by ending its authenticated state, and treats 503 as an availability error. There is no refresh token; after expiration the user logs in again. The frontend must clear its token after successful logout. This backend increment supplies no frontend UI, token persistence, CORS policy, or automatic reauthentication.

Do not default to JWT storage in `localStorage`. An in-memory token is compatible with this API and is lost on reload; frontend work must explicitly design its browser flow. Bearer headers are not automatically attached by the browser, so the current header-only authentication does not need cookie CSRF protection. XSS can still steal accessible tokens or invoke APIs; frontend XSS protection and HTTPS remain necessary. A future HttpOnly cookie flow requires a new CSRF and cookie-attribute design.

Deploy with HTTPS and ensure application, proxy, tracing, and monitoring configuration never logs request credentials, Authorization headers, response tokens, password hashes, or signing secrets. The implementation does not introduce credential logging. SQLAlchemy uses hidden parameters. Thread concurrency and body bounds limit per-request work but do not provide rate limiting or bounded ingress queues; public deployment needs registration/login abuse limits at ingress before exposure. This increment does not add reset, verification, MFA, refresh, or session-cleanup workflows.

## Schema and Testing

Migration `0002_add_local_credentials` creates only `local_credentials(user_id UUID PRIMARY KEY REFERENCES users(id), password_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())`. `0001`, User, and AuthSession columns are unchanged. A downgrade deletes local credentials but preserves Users and Sessions; re-upgrading cannot recover those hashes.

Tests run against disposable Alembic-created schemas in the dedicated PostgreSQL test database, using real Argon2id and PyJWT. They cover API flows, normalization and password boundaries, unique/concurrent registration, transaction rollback, hash-only storage, independent concurrent logins, active/disabled/unknown statuses, JWT validation, Session mismatches, revocation, repeat/concurrent logout, safe responses, import/configuration behavior, thread offload, schema constraints, incremental upgrades/downgrades, and Alembic model drift checks. No real identity-provider or breach-service network calls are required.

The current User stores one issuer/subject pair. Linking local and external identities to one User will require a future `UserIdentity` migration and explicit linking policy; no provider has been chosen and no identity-linking table is created now.
