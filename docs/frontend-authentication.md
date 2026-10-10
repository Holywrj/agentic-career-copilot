# Frontend Authentication

This increment provides account creation, sign-in, a protected workspace shell, and sign-out. Career Profile, Resume, Jobs, and Job Analysis cards are explicitly upcoming features, not user data or implemented business flows. The [backend contract](authentication.md) remains authoritative for endpoint and validation semantics.

## Architecture

`main.tsx` composes StrictMode, BrowserRouter, AuthProvider, and App. App owns a shallow route table plus route titles and heading focus. Context and its hook live in `auth/AuthContext.ts`; `AuthProvider.tsx` exports only the component to preserve the React Refresh boundary. RouteGuards renders redirects with history replacement.

| Module | Responsibility |
| --- | --- |
| `api/client.ts` | Fixed `/api` requests, JSON/204 parsing, explicit Bearer headers, cancellation, optional per-request timeout, safe error classification |
| `api/auth.ts` | Typed auth helpers and small runtime checks for successful auth response shapes |
| `auth/AuthProvider.tsx` | In-memory Session, two-step login, expiration, logout, authenticated requests, generation and cancellation coordination |
| `auth/RouteGuards.tsx` | Anonymous/public and authenticated/private route access |
| `pages/` | Forms, pending states, safe errors, registration success, workspace and not-found UI |
| `components/AuthLayout.tsx`, `styles.css` | Shared product/auth surfaces, tokens, responsive layout and reduced-motion support |

The API layer imports neither React nor Context. It never owns a global token, navigates, logs credentials, or retries POSTs. Components use typed auth helpers or the Provider's `requestAuthenticated` boundary rather than scattered fetch calls. Future business helpers can accept the authenticated requester; server-state caching, uploads, and streaming are introduced only with the relevant feature.

## State and credential lifecycle

Stable state is either `anonymous` with no Session, or `authenticated` with `{ accessToken, user: { id, status: 'active' }, expiresAt }`. Login pending state and safe session notices are separate. Provider refs synchronously track the current Session and operation generation; React state renders the UI. The token is kept private to the Provider/request lifecycle rather than exposed in the Context value.

There is no storage, restoration state, JWT decoding, polling, refresh, silent reauthentication, or module-level credential singleton. Tokens are not written to localStorage, sessionStorage, IndexedDB, cookies, URL parameters, or router state. Requests set `credentials: 'omit'`. Reload, a fresh Provider, and a new tab all start anonymous and require a new sign-in. This UX limitation is intentional. Other tabs have independent Sessions; this increment does not coordinate cross-tab logout.

Memory storage avoids keeping a credential after the page lifecycle, but does not prevent XSS from stealing a live credential or invoking authenticated APIs. Stolen Bearer tokens may be replayed until expiration or server revocation. Frontend route guards are UX controls, not resource authorization. HTTPS and backend ownership checks remain required. A future persistent-login design must separately evaluate HttpOnly/Secure/SameSite cookies, refresh lifetimes, CSRF, rotation, reuse detection, and revocation.

## Login and registration

Login sends JSON credentials to `/api/auth/login`, holds the returned token temporarily, then calls `/api/auth/me` directly with that token. It does not use `requestAuthenticated` before a Session exists. Only successful responses from both steps atomically establish the Session, followed by `/app` with replacement. `/me` supplies the backend-confirmed platform User; the JWT is only a credential. Username is not assumed to be a User field.

An unsuccessful or malformed `/me` response discards the temporary token and leaves the app anonymous. Its availability or network failure is never presented as an incorrect password. The backend may already have created a Session; discarding the browser credential does not revoke it, and the existing backend TTL still applies.

Registration calls only `/api/auth/register`. Success replaces the route with `/login` and a lightweight `registered: true` success notice, without passing a token or password through navigation. It never logs in or calls `/me`.

Username and password requirements are visible guidance. Registration counts Unicode code points, considers NFC for the minimum, and avoids HTML `maxLength` based on UTF-16 units. Passwords are sent unchanged, preserving whitespace and case. The backend remains responsible for username normalization, byte bounds, control characters, and final validation. Login does not impose registration's 15-character minimum. Sanitized backend 422 responses have no field details, so the UI associates a general error with the form instead of inventing field-specific failures.

## Expiration and races

`expiresAt` is computed from the login response's `expires_in` and browser receipt time. `/me` time consumes that estimate. A timer, visible-page transition, and `pageshow` check clear a Session whose local deadline has passed. Before each authenticated request, the deadline is checked again.

This is an approximate UX deadline, not an authorization source of truth: network delay, browser clocks/timer scheduling, server revocation, and User changes can make backend validity differ. Backend JWT + PostgreSQL AuthSession checks remain authoritative, and a recognized private 401 can end the Session earlier.

Login/session changes increment a generation counter. Login checks its operation after each await. Authenticated requests snapshot the Session/generation and ignore stale success or invalidation. Ending a Session aborts pending login and private requests. Leaving the login page cancels a pending login. Logout requests are separate: their completion can update a sign-out warning only while their generation is still current, and can never clear a newly established Session.

Cancellation and generation checks serve complementary roles: abort limits unnecessary work; identity checks guard against late results. Neither can undo an operation that already passed backend authentication.

## Errors and sign-out

The client recognizes allowlisted status/code pairs from the backend. Unknown details, validation payloads, exception text, and HTML proxy responses are never displayed verbatim. Invalid success responses, network failures, cancellation, and timeout are distinct. Cancellation is silent in forms. Auth helpers choose a 15-second timeout per request; the generic client has no default timeout, so future long-running transports are not constrained by this choice.

| Failure | Behavior |
| --- | --- |
| Login `401 invalid_credentials` | Safe form error; no authenticated Session established |
| Current private `401 authentication_required` | Provider clears Session; protected route replaces with `/login` |
| Old Session private 401 | Cannot clear a newer Session |
| Private unknown/mismatched 401 body | Safe unexpected error, without treating an unrecognized code as Session invalidation; backend still enforces access |
| Login/register 503 or network failure | Anonymous, safe retryable message |
| Temporary login `/me` 503/network/invalid response | Discard temporary token, remain anonymous |
| Authenticated private 503/network | Preserve Session and return the error to the feature UI |
| 409 / 422 / 413 | Username availability, general validation, or oversized-input message |

Explicit sign-out snapshots the old token and immediately clears the browser Session, cancels private/login requests, and redirects via the guard to `/login`. It then attempts `POST /api/auth/logout` with the captured Bearer token. A 204 or recognized `401 authentication_required` completes local sign-out without a revocation warning. A 503, network failure, timeout, malformed response, or unexpected error shows: “已在当前浏览器退出，但暂时无法确认服务器上的登录会话是否已撤销。” Local state remains anonymous in all cases.

The initial success notice claims only browser sign-out. Failure does not imply server revocation; an already copied token may remain usable until server expiration/revocation. The old credential is retained only for the bounded in-flight request, never persisted for retries. A late old logout cannot overwrite a new Session or its notices.

## Routes and development integration

| Route | Anonymous | Authenticated |
| --- | --- | --- |
| `/` | Replace with `/login` | Replace with `/app` |
| `/login`, `/register` | Form | Replace with `/app` |
| `/app` | Replace with `/login` | Workspace shell |
| Unknown | Not found, link to sign-in | Not found, link to workspace |

No arbitrary return URL or nested workspace route framework is introduced. React Router runs in Declarative mode.

Run PostgreSQL/migrations/backend as described in the [README](../README.md), then run `npm run dev` in `frontend/`. The browser uses the Vite origin, normally `http://localhost:5173`. Requests to `/api/auth/*` are proxied to `http://127.0.0.1:8000/auth/*` by removing `/api`. The backend's `/auth` contract is unchanged and this development flow needs no new backend CORS middleware.

Vite's proxy is development-only. Production hosting must serve the SPA, provide equivalent `/api/*` proxy/rewrite behavior over HTTPS, and preserve Authorization and API status/body responses. Page deep links should return `index.html`; API failures must not be routed to the SPA fallback. A build alone does not supply backend routing.

## UI and validation

The original soft-glass layout uses plain CSS tokens, white/high-opacity form surfaces, subtle lavender/blue radial gradients, restrained typography, and dark primary actions. There are no external imagery assets, React Bits copies, WebGL, or continuous motion. Desktop uses product storytelling beside the form; tablet/mobile stack a short introduction and form while hiding preview cards. Upcoming workspace cards are informational, without fake analytics or dead navigation controls.

The interface defaults to Simplified Chinese, including labels, status messages, errors, and route titles. `index.html` declares `lang="zh-CN"` and `translate="yes"`; users can use their browser’s built-in page translation. Only the brand wordmark opts out of translation. There is no translation SDK, language selector, or additional dependency. The font stack includes common Chinese system fonts. Backend validation and API field names are unchanged.

Forms have explicit labels, appropriate autocomplete, native keyboard submission, loading/disabled states, synchronous duplicate-submit protection, associated announced errors, visible keyboard focus, page titles and route heading focus. Paste and password managers are supported. Nonessential transitions are disabled with `prefers-reduced-motion`; the auth surface has an opaque fallback without backdrop-filter support.

Run `npm run lint`, `npm run test`, `npm run build`, and repository `git diff --check`. Vitest/Testing Library tests exercise API boundaries, auth responses, route access, memory-only behavior, expiry, failures and Session races under StrictMode. user-event covers form and keyboard interactions. Test-only fixtures and controls are not imported by the application.

Real-browser checks should additionally cover register → login → `/me` → workspace → logout through the actual proxy, refresh/new-tab anonymity, keyboard focus, desktop/tablet/mobile layouts, and readable errors. DOM tests alone do not verify rendering, full screen-reader behavior, reverse-proxy deployment, or backend integration.
