"""Full JSON/Bearer flows against real PostgreSQL, Argon2id and PyJWT."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import delete, event, func, select, text, update

from career_copilot.auth.tokens import JWT_AUDIENCE, JWT_ISSUER, hash_token
from career_copilot.db.models import AuthSession, LocalCredential, User
from career_copilot.db.session import create_session_factory

pytestmark = pytest.mark.database
PASSWORD = "A private long passphrase 🐍"


async def register(client, username="alice", password=PASSWORD):
    response = await client.post(
        "/auth/register", json={"username": username, "password": password}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def login(client, username="alice", password=PASSWORD):
    response = await client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def assert_authentication_required(response):
    assert response.status_code == 401
    assert response.json() == {"detail": "authentication_required"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"


async def test_register_login_me_and_exact_storage(
    auth_client, db_engine, auth_settings, caplog
):
    user = await register(auth_client, "  Alice  ")
    assert user["username"] == "alice" and user["status"] == "active"
    factory = create_session_factory(db_engine)
    async with factory() as db:
        stored_user = await db.get(User, UUID(user["id"]))
        credential = await db.get(LocalCredential, stored_user.id)
        assert stored_user.identity_issuer == "local"
        assert stored_user.identity_subject == "alice"
        assert credential.password_hash.startswith("$argon2id$")
        assert credential.created_at.tzinfo is not None
        assert await db.scalar(select(func.count()).select_from(AuthSession)) == 0
    response = await auth_client.post(
        "/auth/login", json={"username": "ALICE", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    token_data = response.json()
    assert set(token_data) == {"access_token", "token_type", "expires_in"}
    assert token_data["token_type"] == "bearer"
    assert 1790 <= token_data["expires_in"] <= 1800
    token = token_data["access_token"]
    claims = jwt.decode(
        token,
        auth_settings.jwt_secret,
        algorithms=["HS256"],
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
    )
    assert set(claims) == {"sub", "jti", "iat", "exp", "iss", "aud"}
    assert claims["sub"] == user["id"]
    assert type(claims["iat"]) is type(claims["exp"]) is int
    assert claims["exp"] - claims["iat"] == 1800
    async with factory() as db:
        session = await db.get(AuthSession, UUID(claims["jti"]))
        assert session.user_id == UUID(claims["sub"])
        assert session.token_hash == hash_token(token)
        assert session.expires_at == datetime.fromtimestamp(claims["exp"], UTC)
        assert session.created_at == datetime.fromtimestamp(claims["iat"], UTC)
        assert session.revoked_at is None
        for table in ["users", "local_credentials", "sessions"]:
            rows = (
                await db.execute(text(f"SELECT row_to_json(t) FROM {table} t"))
            ).all()
            assert token not in str(rows)
            assert PASSWORD not in str(rows)
    me = await auth_client.get("/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json() == {"id": user["id"], "status": "active"}
    assert me.headers["cache-control"] == "no-store"
    for secret in [PASSWORD, token, credential.password_hash, auth_settings.jwt_secret]:
        assert secret not in caplog.text


async def test_same_password_has_independent_salts(auth_client, db_engine):
    await register(auth_client, "alice")
    await register(auth_client, "bob")
    async with create_session_factory(db_engine)() as db:
        hashes = (await db.scalars(select(LocalCredential.password_hash))).all()
        assert len(hashes) == 2 and hashes[0] != hashes[1]


async def test_duplicate_and_concurrent_registration(auth_client, db_engine):
    responses = await asyncio.gather(
        *[
            auth_client.post(
                "/auth/register", json={"username": name, "password": PASSWORD}
            )
            for name in ["  Alice ", "ALICE", "alice"]
        ]
    )
    assert sorted(response.status_code for response in responses) == [201, 409, 409]
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        if response.status_code == 409:
            assert response.json() == {"detail": "username_unavailable"}
    async with create_session_factory(db_engine)() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(LocalCredential)) == 1


@pytest.mark.parametrize(
    "username", ["   ", "ab", "a" * 33, "-alice", "a b", "用户", "аlice", "a@b"]
)
async def test_invalid_username_is_sanitized(auth_client, username):
    response = await auth_client.post(
        "/auth/register", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert PASSWORD not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "password",
    [
        "short",
        " " * 15,
        "a" * 129,
        "🐍" * 129,
        "a" * 15 + "\n",
        "a" * 15 + "\x00",
        {"secret": PASSWORD},
    ],
)
async def test_invalid_password_is_sanitized(auth_client, password, caplog):
    response = await auth_client.post(
        "/auth/register", json={"username": "alice", "password": password}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_request"}
    assert PASSWORD not in response.text + caplog.text


async def test_password_not_trimmed_or_lowercased_and_unicode_nfc(auth_client):
    password = "  A Cafe\u0301 Passphrase  "
    await register(auth_client, password=password)
    token = await login(auth_client, password="  A Café Passphrase  ")
    assert (await auth_client.get("/auth/me", headers=bearer(token))).status_code == 200
    for wrong in [password.strip(), password.lower()]:
        response = await auth_client.post(
            "/auth/login", json={"username": "alice", "password": wrong}
        )
        assert response.status_code == 401


async def test_password_boundaries_and_no_common_password_list(auth_client):
    for index, password in enumerate(["x" * 15, "🐍" * 128, "passwordpassword"]):
        name = f"user{index}"
        await register(auth_client, name, password)
        assert await login(auth_client, name, password)


async def test_login_failures_identical_and_no_session(
    auth_client, db_engine, auth_app, monkeypatch
):
    user = await register(auth_client)
    responses = []
    observed_hashes = []
    original = auth_app.state.passwords.verify

    async def observe(password, password_hash):
        assert db_engine.pool.checkedout() == 0
        observed_hashes.append(password_hash)
        return await original(password, password_hash)

    monkeypatch.setattr(auth_app.state.passwords, "verify", observe)
    for username in ["alice", "unknown"]:
        responses.append(
            await auth_client.post(
                "/auth/login", json={"username": username, "password": "wrong"}
            )
        )
    for status in ["disabled", "unrecognized"]:
        async with create_session_factory(db_engine).begin() as db:
            await db.execute(
                update(User).where(User.id == UUID(user["id"])).values(status=status)
            )
        responses.append(
            await auth_client.post(
                "/auth/login", json={"username": "alice", "password": PASSWORD}
            )
        )
    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid_credentials"}
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["cache-control"] == "no-store"
    assert observed_hashes[0] is not None and observed_hashes[1] is None
    async with create_session_factory(db_engine)() as db:
        assert await db.scalar(select(func.count()).select_from(AuthSession)) == 0


async def test_status_rechecked_after_password_verification(
    auth_client, auth_app, db_engine, monkeypatch
):
    user = await register(auth_client)
    original = auth_app.state.passwords.verify

    async def disable_after_verify(password, password_hash):
        matches = await original(password, password_hash)
        async with create_session_factory(db_engine).begin() as db:
            await db.execute(
                update(User)
                .where(User.id == UUID(user["id"]))
                .values(status="disabled")
            )
        return matches

    monkeypatch.setattr(auth_app.state.passwords, "verify", disable_after_verify)
    response = await auth_client.post(
        "/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert response.status_code == 401


async def test_disabled_and_unknown_status_reject_existing_token(
    auth_client, db_engine
):
    user = await register(auth_client)
    token = await login(auth_client)
    for status in ["disabled", "unknown"]:
        async with create_session_factory(db_engine).begin() as db:
            await db.execute(
                update(User).where(User.id == UUID(user["id"])).values(status=status)
            )
        assert_authentication_required(
            await auth_client.get("/auth/me", headers=bearer(token))
        )
    # A disabled account can still revoke its real session.
    assert (
        await auth_client.post("/auth/logout", headers=bearer(token))
    ).status_code == 204


async def test_logout_is_idempotent_and_other_session_survives(auth_client, db_engine):
    await register(auth_client)
    first, second = await asyncio.gather(login(auth_client), login(auth_client))
    assert first != second
    for token in [first, second]:
        assert (
            await auth_client.get("/auth/me", headers=bearer(token))
        ).status_code == 200
    response = await auth_client.post("/auth/logout", headers=bearer(first))
    assert response.status_code == 204 and response.content == b""
    assert response.headers["cache-control"] == "no-store"
    async with create_session_factory(db_engine)() as db:
        revoked = await db.scalar(
            select(AuthSession.revoked_at).where(
                AuthSession.token_hash == hash_token(first)
            )
        )
        assert revoked is not None
    assert_authentication_required(
        await auth_client.get("/auth/me", headers=bearer(first))
    )
    repeated = await asyncio.gather(
        *[auth_client.post("/auth/logout", headers=bearer(first)) for _ in range(2)]
    )
    assert all(response.status_code == 204 for response in repeated)
    assert (
        await auth_client.get("/auth/me", headers=bearer(second))
    ).status_code == 200
    async with create_session_factory(db_engine)() as db:
        assert (
            await db.scalar(
                select(AuthSession.revoked_at).where(
                    AuthSession.token_hash == hash_token(first)
                )
            )
            == revoked
        )


@pytest.mark.parametrize("path", ["/auth/me", "/auth/logout"])
async def test_only_bearer_header_is_accepted(auth_client, path):
    await register(auth_client)
    token = await login(auth_client)
    method = auth_client.get if path.endswith("me") else auth_client.post
    for headers in [
        {},
        {"Authorization": "Basic nope"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer broken.jwt"},
    ]:
        assert_authentication_required(await method(path, headers=headers))
    assert_authentication_required(await method(path, params={"token": token}))
    assert_authentication_required(
        await method(path, headers={"Cookie": f"session={token}"})
    )
    assert_authentication_required(
        await auth_client.post("/auth/logout", json={"token": token})
    )
    assert_authentication_required(
        await method(
            path,
            headers=[
                ("Authorization", f"Bearer {token}"),
                ("Authorization", f"Bearer {token}"),
            ],
        )
    )


@pytest.mark.parametrize(
    "mutation",
    ["hash", "unicode_hash", "owner", "expiry", "expired", "revoked", "missing"],
)
async def test_session_mismatches_and_expiration(auth_client, db_engine, mutation):
    await register(auth_client)
    token = await login(auth_client)
    factory = create_session_factory(db_engine)
    async with factory.begin() as db:
        session = await db.scalar(
            select(AuthSession).where(AuthSession.token_hash == hash_token(token))
        )
        if mutation == "hash":
            session.token_hash = "0" * 64
        elif mutation == "unicode_hash":
            session.token_hash = "坏" * 64
        elif mutation == "owner":
            other = User(
                identity_issuer="local", identity_subject="bob", status="active"
            )
            db.add(other)
            await db.flush()
            session.user_id = other.id
        elif mutation == "expiry":
            session.expires_at += timedelta(seconds=1)
        elif mutation == "expired":
            session.created_at = datetime.now(UTC) - timedelta(hours=1)
            session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif mutation == "revoked":
            session.revoked_at = datetime.now(UTC)
        else:
            await db.delete(session)
    assert_authentication_required(
        await auth_client.get("/auth/me", headers=bearer(token))
    )
    if mutation != "revoked":
        assert_authentication_required(
            await auth_client.post("/auth/logout", headers=bearer(token))
        )


@pytest.mark.parametrize(
    "kind",
    [
        "signature",
        "algorithm",
        "issuer",
        "audience",
        "expired",
        "unknown_session",
        "reissued_same_session",
    ],
)
async def test_invalid_jwt_rejected_by_api(auth_client, auth_settings, kind):
    await register(auth_client)
    token = await login(auth_client)
    claims = jwt.decode(
        token, auth_settings.jwt_secret, algorithms=["HS256"], audience=JWT_AUDIENCE
    )
    key, algorithm = auth_settings.jwt_secret, "HS256"
    if kind == "signature":
        key = "different-test-only-signing-key" * 2
    elif kind == "algorithm":
        algorithm = "HS384"
        key += "-negative-test"
    elif kind == "issuer":
        claims["iss"] = "foreign"
    elif kind == "audience":
        claims["aud"] = "foreign"
    elif kind == "expired":
        claims.update(iat=claims["iat"] - 1800, exp=claims["iat"] - 1)
    elif kind == "unknown_session":
        claims["jti"] = str(uuid4())
    else:
        claims["extra"] = "another token with the same jti is not the issued credential"
    invalid = jwt.encode(claims, key, algorithm=algorithm)
    assert_authentication_required(
        await auth_client.get("/auth/me", headers=bearer(invalid))
    )
    assert_authentication_required(
        await auth_client.post("/auth/logout", headers=bearer(invalid))
    )


async def test_deleted_user_and_local_user_without_credential(auth_client, db_engine):
    user = await register(auth_client)
    token = await login(auth_client)
    async with create_session_factory(db_engine).begin() as db:
        await db.execute(
            delete(LocalCredential).where(LocalCredential.user_id == UUID(user["id"]))
        )
    failed = await auth_client.post(
        "/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert failed.status_code == 401
    # Existing sessions do not depend on a particular authentication method.
    assert (await auth_client.get("/auth/me", headers=bearer(token))).status_code == 200
    async with create_session_factory(db_engine).begin() as db:
        await db.execute(
            delete(AuthSession).where(AuthSession.user_id == UUID(user["id"]))
        )
        await db.execute(delete(User).where(User.id == UUID(user["id"])))
    assert_authentication_required(
        await auth_client.get("/auth/me", headers=bearer(token))
    )


async def test_register_rollback_and_only_identity_conflict_is_409(
    auth_client, db_engine
):
    def break_credential(_mapper, _connection, target):
        target.user_id = uuid4()  # Real PostgreSQL FK violation after the User INSERT.

    event.listen(LocalCredential, "before_insert", break_credential)
    try:
        response = await auth_client.post(
            "/auth/register", json={"username": "alice", "password": PASSWORD}
        )
    finally:
        event.remove(LocalCredential, "before_insert", break_credential)
    assert response.status_code == 503
    assert response.json() == {"detail": "authentication_unavailable"}
    async with create_session_factory(db_engine)() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0
        assert await db.scalar(select(func.count()).select_from(LocalCredential)) == 0
    user = await register(auth_client)

    def duplicate_credential(_mapper, _connection, target):
        target.user_id = UUID(user["id"])  # A different UNIQUE violation, not username.

    event.listen(LocalCredential, "before_insert", duplicate_credential)
    try:
        response = await auth_client.post(
            "/auth/register", json={"username": "bob", "password": PASSWORD}
        )
    finally:
        event.remove(LocalCredential, "before_insert", duplicate_credential)
    assert response.status_code == 503
    async with create_session_factory(db_engine)() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1


async def test_failed_login_commit_does_not_return_token(
    auth_client, db_engine, caplog
):
    await register(auth_client)

    def break_session(_mapper, _connection, target):
        target.user_id = uuid4()

    event.listen(AuthSession, "before_insert", break_session)
    try:
        response = await auth_client.post(
            "/auth/login", json={"username": "alice", "password": PASSWORD}
        )
    finally:
        event.remove(AuthSession, "before_insert", break_session)
    assert response.status_code == 503
    assert response.json() == {"detail": "authentication_unavailable"}
    assert PASSWORD not in response.text + caplog.text
    async with create_session_factory(db_engine)() as db:
        assert await db.scalar(select(func.count()).select_from(AuthSession)) == 0


async def test_database_unavailable_is_not_401(auth_client, db_engine):
    await register(auth_client)
    token = await login(auth_client)
    async with db_engine.begin() as connection:
        await connection.execute(text("DROP TABLE sessions"))
    response = await auth_client.get("/auth/me", headers=bearer(token))
    assert response.status_code == 503
    assert response.json() == {"detail": "authentication_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert token not in response.text


async def test_corrupt_password_hash_is_sanitized_infrastructure_failure(
    auth_client, db_engine, caplog
):
    await register(auth_client)
    corrupt_hash = "invalid-stored-credential-material"
    async with create_session_factory(db_engine).begin() as db:
        await db.execute(update(LocalCredential).values(password_hash=corrupt_hash))
    response = await auth_client.post(
        "/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "authentication_unavailable"}
    assert corrupt_hash not in response.text + caplog.text
    assert PASSWORD not in response.text + caplog.text


async def test_registration_hash_runs_without_database_transaction(
    auth_client, auth_app, db_engine, monkeypatch
):
    original = auth_app.state.passwords.hash

    async def checked_hash(password):
        assert db_engine.pool.checkedout() == 0
        return await original(password)

    monkeypatch.setattr(auth_app.state.passwords, "hash", checked_hash)
    await register(auth_client)


async def test_malformed_json_and_chunked_body_limits(auth_client):
    malformed = await auth_client.post(
        "/auth/login",
        content=b'{"password":"secret',
        headers={"Content-Type": "application/json"},
    )
    assert malformed.status_code == 422
    assert malformed.json() == {"detail": "invalid_request"}

    async def chunks():
        yield b'{"password":"'
        for _ in range(10):
            yield b"x" * 1024
        yield b'"}'

    response = await auth_client.post(
        "/auth/register", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large"}
    assert response.headers["cache-control"] == "no-store"


def test_openapi_uses_http_bearer_not_oauth_password_flow(auth_app):
    schema = auth_app.openapi()
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert schema["paths"]["/auth/me"]["get"]["security"] == [{"HTTPBearer": []}]
