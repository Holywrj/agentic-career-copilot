"""Private singleton behavior and CAS isolation against real PostgreSQL."""

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx2
import jwt
import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import IntegrityError

from career_copilot.auth.tokens import JWT_AUDIENCE
from career_copilot.career_profile import service
from career_copilot.career_profile.middleware import MAX_CAREER_PROFILE_BODY_BYTES
from career_copilot.career_profile.schemas import (
    CareerProfileCreate,
    CareerProfileUpdate,
)
from career_copilot.db.models import AuthSession, CareerProfile, LocalCredential, User
from career_copilot.db.session import create_session_factory

pytestmark = pytest.mark.database
PATH = "/career-profile"
PASSWORD = "Private profile test passphrase 🐍"
FIELDS = {"career_summary": None, "skills": [], "target_roles": []}
RESPONSE_FIELDS = set(FIELDS) | {"revision", "created_at", "updated_at"}


async def account(client, username="alice"):
    registered = await client.post(
        "/auth/register", json={"username": username, "password": PASSWORD}
    )
    assert registered.status_code == 201, registered.text
    logged_in = await client.post(
        "/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert logged_in.status_code == 200, logged_in.text
    return UUID(registered.json()["id"]), logged_in.json()["access_token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def replacement(revision=1, **fields):
    return {**FIELDS, **fields, "expected_revision": revision}


def assert_error(response, status, detail):
    assert response.status_code == status, response.text
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == "no-store"


@pytest_asyncio.fixture
async def profile_account(auth_client):
    return await account(auth_client)


@pytest.mark.parametrize("method", ["GET", "POST", "PUT"])
async def test_authentication_required(auth_client, method):
    payload = replacement() if method == "PUT" else {}
    for authorization in [None, "Basic invalid", "Bearer broken.jwt"]:
        headers = {"Authorization": authorization} if authorization else {}
        response = await auth_client.request(
            method,
            PATH,
            headers=headers,
            **({"json": payload} if method != "GET" else {}),
        )
        assert_error(response, 401, "authentication_required")
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("method", ["GET", "POST", "PUT"])
@pytest.mark.parametrize("state", ["expired", "revoked", "disabled"])
async def test_invalidated_authentication_never_enters_domain(
    auth_client, auth_settings, db_engine, profile_account, monkeypatch, method, state
):
    owner_id, token = profile_account
    if state == "expired":
        claims = jwt.decode(
            token, auth_settings.jwt_secret, algorithms=["HS256"], audience=JWT_AUDIENCE
        )
        claims.update(iat=claims["iat"] - 1800, exp=claims["iat"] - 1)
        token = jwt.encode(claims, auth_settings.jwt_secret, algorithm="HS256")
    else:
        async with create_session_factory(db_engine).begin() as db:
            if state == "revoked":
                await db.execute(
                    update(AuthSession)
                    .where(AuthSession.user_id == owner_id)
                    .values(revoked_at=datetime.now(UTC))
                )
            else:
                await db.execute(
                    update(User).where(User.id == owner_id).values(status="disabled")
                )

    async def forbidden(*_args):
        pytest.fail("Invalid authentication reached the domain operation")

    for name in ["get", "create", "replace"]:
        monkeypatch.setattr(service, name, forbidden)
    response = await auth_client.request(
        method,
        PATH,
        headers=bearer(token),
        **(
            {"json": replacement() if method == "PUT" else {}}
            if method != "GET"
            else {}
        ),
    )
    assert_error(response, 401, "authentication_required")
    assert response.headers["www-authenticate"] == "Bearer"


async def test_registration_leaves_optional_profile_and_put_does_not_upsert(
    auth_client, db_engine, profile_account
):
    owner_id, token = profile_account
    async with create_session_factory(db_engine).begin() as db:
        assert await db.get(CareerProfile, owner_id) is None
    assert_error(
        await auth_client.get(PATH, headers=bearer(token)),
        404,
        "career_profile_not_found",
    )
    assert_error(
        await auth_client.put(PATH, headers=bearer(token), json=replacement()),
        404,
        "career_profile_not_found",
    )
    async with create_session_factory(db_engine).begin() as db:
        assert await db.get(CareerProfile, owner_id) is None


async def test_create_defaults_read_and_duplicate(
    auth_client, db_engine, profile_account
):
    owner_id, token = profile_account
    created = await auth_client.post(PATH, headers=bearer(token), json={})
    assert created.status_code == 201, created.text
    assert created.headers["location"] == PATH
    assert created.headers["cache-control"] == "no-store"
    profile = created.json()
    assert set(profile) == RESPONSE_FIELDS
    assert {key: profile[key] for key in FIELDS} == FIELDS
    assert profile["revision"] == 1
    created_at = datetime.fromisoformat(profile["created_at"])
    updated_at = datetime.fromisoformat(profile["updated_at"])
    assert created_at.tzinfo is not None and updated_at.tzinfo is not None
    assert created_at == updated_at
    loaded = await auth_client.get(PATH, headers=bearer(token))
    assert loaded.status_code == 200 and loaded.json() == profile
    assert loaded.headers["cache-control"] == "no-store"
    assert_error(
        await auth_client.post(
            PATH, headers=bearer(token), json={"skills": ["cannot overwrite"]}
        ),
        409,
        "career_profile_already_exists",
    )
    assert (await auth_client.get(PATH, headers=bearer(token))).json() == profile
    async with create_session_factory(db_engine).begin() as db:
        assert await db.scalar(select(func.count()).select_from(CareerProfile)) == 1
        stored = await db.get(CareerProfile, owner_id)
        assert stored.skills == [] and stored.revision == 1


async def test_replacement_revision_timestamps_same_content_and_conflict(
    auth_client, profile_account
):
    _, token = profile_account
    original = (
        await auth_client.post(
            PATH,
            headers=bearer(token),
            json={"career_summary": "Original", "skills": ["Python"]},
        )
    ).json()
    payload = replacement(
        career_summary="Updated", skills=["Rust"], target_roles=["Backend Engineer"]
    )
    response = await auth_client.put(PATH, headers=bearer(token), json=payload)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    changed = response.json()
    assert changed["revision"] == 2
    assert changed["created_at"] == original["created_at"]
    assert datetime.fromisoformat(changed["updated_at"]) > datetime.fromisoformat(
        original["updated_at"]
    )
    assert {key: changed[key] for key in FIELDS} == {
        key: payload[key] for key in FIELDS
    }
    assert_error(
        await auth_client.put(PATH, headers=bearer(token), json=payload),
        409,
        "career_profile_conflict",
    )
    assert (await auth_client.get(PATH, headers=bearer(token))).json() == changed
    payload["expected_revision"] = 2
    same_content = await auth_client.put(PATH, headers=bearer(token), json=payload)
    assert same_content.status_code == 200 and same_content.json()["revision"] == 3
    cleared = await auth_client.put(
        PATH, headers=bearer(token), json=replacement(revision=3)
    )
    assert cleared.status_code == 200 and cleared.json()["revision"] == 4
    assert {key: cleared.json()[key] for key in FIELDS} == FIELDS
    assert cleared.json()["created_at"] == original["created_at"]


@pytest.mark.parametrize("method", ["POST", "PUT"])
async def test_normalization_and_first_representation(
    auth_client, profile_account, method
):
    _, token = profile_account
    fields = {
        "career_summary": " \r\nCafe\u0301\r\n背景\r方向\n ",
        "skills": [
            " Python ",
            "python",
            "Cafe\u0301",
            "CAFÉ",
            "Straße",
            "STRASSE",
            "JS",
            "JavaScript",
            "A  B",
        ],
        "target_roles": [
            " AI Engineer ",
            "ai engineer",
            "后端工程师",
            "Backend Engineer",
        ],
    }
    if method == "PUT":
        assert (
            await auth_client.post(PATH, headers=bearer(token), json={})
        ).status_code == 201
        fields["expected_revision"] = 1
    response = await auth_client.request(
        method, PATH, headers=bearer(token), json=fields
    )
    assert response.status_code == (201 if method == "POST" else 200), response.text
    profile = response.json()
    assert profile["career_summary"] == "Café\n背景\n方向"
    assert profile["skills"] == ["Python", "Café", "Straße", "JS", "JavaScript", "A  B"]
    assert profile["target_roles"] == ["AI Engineer", "后端工程师", "Backend Engineer"]
    assert (await auth_client.get(PATH, headers=bearer(token))).json() == profile


@pytest.mark.parametrize("summary", [None, "", " \n\r\n \u00a0"])
async def test_blank_summary_is_null(auth_client, profile_account, summary):
    _, token = profile_account
    response = await auth_client.post(
        PATH, headers=bearer(token), json={"career_summary": summary}
    )
    assert response.status_code == 201 and response.json()["career_summary"] is None


async def test_boundary_lengths_counts_and_international_text(
    auth_client, profile_account
):
    _, token = profile_account
    fields = {
        "career_summary": "e\u0301" * 2000,
        "skills": ["🐍" * 80] + [f"Skill {index}" for index in range(99)],
        "target_roles": ["职" * 120] + [f"Role {index}" for index in range(9)],
    }
    response = await auth_client.post(PATH, headers=bearer(token), json=fields)
    assert response.status_code == 201, response.text
    assert response.json()["career_summary"] == "é" * 2000
    assert len(response.json()["skills"]) == 100
    assert len(response.json()["target_roles"]) == 10
    # No compatibility normalization or broad format/bidi filtering.
    fields = replacement(
        skills=[
            "Ｐｙｔｈｏｎ",
            "Python",
            "👩\u200d💻",
            "می\u200cنویسم",
            "\u2067مرحبا\u2069",
        ]
    )
    response = await auth_client.put(PATH, headers=bearer(token), json=fields)
    assert response.status_code == 200 and response.json()["skills"] == fields["skills"]


INVALID_FIELDS = [
    {"career_summary": 123},
    {"career_summary": "x" * 2001},
    {"career_summary": "private\x00"},
    {"career_summary": "private\t"},
    {"career_summary": "private\ud800"},
    {"skills": None},
    {"skills": "Python"},
    {"skills": [123]},
    {"skills": [True]},
    {"skills": [{"name": "private"}]},
    {"skills": [None]},
    {"skills": [""]},
    {"skills": [" \u00a0 "]},
    {"skills": ["x" * 81]},
    {"skills": ["Python"] * 101},
    {"skills": ["Python\n"]},
    {"skills": ["Python\r"]},
    {"skills": ["Python\t"]},
    {"skills": ["Python\u2028"]},
    {"skills": ["Python\u2029"]},
    {"skills": ["Python\x7f"]},
    {"skills": ["Python\u0085"]},
    {"skills": ["Python\udfff"]},
    {"target_roles": None},
    {"target_roles": [{}]},
    {"target_roles": [""]},
    {"target_roles": ["x" * 121]},
    {"target_roles": ["Engineer"] * 11},
    {"target_roles": ["Engineer\n"]},
    {"target_roles": ["Engineer\u2028"]},
]


@pytest.mark.parametrize("invalid", INVALID_FIELDS)
@pytest.mark.parametrize("method", ["POST", "PUT"])
async def test_invalid_input_is_sanitized_and_never_writes(
    auth_client, db_engine, profile_account, method, invalid
):
    owner_id, token = profile_account
    before = None
    payload = invalid
    if method == "PUT":
        before = (await auth_client.post(PATH, headers=bearer(token), json={})).json()
        payload = {**replacement(), **invalid}
    # Escaped JSON can represent a lone surrogate; UTF-8 must not encode it directly.
    response = await auth_client.request(
        method,
        PATH,
        headers={**bearer(token), "Content-Type": "application/json"},
        content=json.dumps(payload, ensure_ascii=True).encode(),
    )
    assert_error(response, 422, "invalid_request")
    assert "private" not in response.text
    if before is not None:
        assert (await auth_client.get(PATH, headers=bearer(token))).json() == before
    else:
        async with create_session_factory(db_engine).begin() as db:
            assert await db.get(CareerProfile, owner_id) is None


@pytest.mark.parametrize(
    "field",
    [
        "user_id",
        "owner_id",
        "profile_user_id",
        "revision",
        "created_at",
        "updated_at",
        "unexpected",
    ],
)
@pytest.mark.parametrize("method", ["POST", "PUT"])
async def test_unknown_and_owner_fields_rejected(
    auth_client, profile_account, method, field
):
    owner_id, token = profile_account
    payload = replacement() if method == "PUT" else {}
    response = await auth_client.request(
        method, PATH, headers=bearer(token), json={**payload, field: str(owner_id)}
    )
    assert_error(response, 422, "invalid_request")


async def test_create_rejects_expected_revision(auth_client, profile_account):
    _, token = profile_account
    assert_error(
        await auth_client.post(
            PATH, headers=bearer(token), json={"expected_revision": 1}
        ),
        422,
        "invalid_request",
    )


@pytest.mark.parametrize(
    "field", ["expected_revision", "career_summary", "skills", "target_roles"]
)
async def test_put_requires_every_field(auth_client, profile_account, field):
    _, token = profile_account
    before = (await auth_client.post(PATH, headers=bearer(token), json={})).json()
    payload = replacement()
    del payload[field]
    assert_error(
        await auth_client.put(PATH, headers=bearer(token), json=payload),
        422,
        "invalid_request",
    )
    assert (await auth_client.get(PATH, headers=bearer(token))).json() == before


@pytest.mark.parametrize("revision", [True, False, 1.0, "1", 0, -1, None])
async def test_expected_revision_is_strict_positive_integer(
    auth_client, profile_account, revision
):
    _, token = profile_account
    before = (await auth_client.post(PATH, headers=bearer(token), json={})).json()
    assert_error(
        await auth_client.put(
            PATH, headers=bearer(token), json=replacement(revision=revision)
        ),
        422,
        "invalid_request",
    )
    assert (await auth_client.get(PATH, headers=bearer(token))).json() == before


async def test_owner_isolation_in_both_directions(auth_client, db_engine):
    a_id, a_token = await account(auth_client, "alice")
    b_id, b_token = await account(auth_client, "bob")
    b = (
        await auth_client.post(PATH, headers=bearer(b_token), json={"skills": ["Bob"]})
    ).json()
    assert_error(
        await auth_client.get(PATH, headers=bearer(a_token)),
        404,
        "career_profile_not_found",
    )
    assert_error(
        await auth_client.put(PATH, headers=bearer(a_token), json=replacement()),
        404,
        "career_profile_not_found",
    )
    a = (
        await auth_client.post(
            PATH, headers=bearer(a_token), json={"skills": ["Alice"]}
        )
    ).json()
    # Query parameters are not owner selectors, even if another User UUID is supplied.
    assert (
        await auth_client.get(
            PATH, headers=bearer(a_token), params={"user_id": str(b_id)}
        )
    ).json() == a
    assert (await auth_client.get(PATH, headers=bearer(b_token))).json() == b
    updated_a = await auth_client.put(
        PATH, headers=bearer(a_token), json=replacement(skills=["Alice updated"])
    )
    assert updated_a.status_code == 200
    assert (await auth_client.get(PATH, headers=bearer(b_token))).json() == b
    updated_b = await auth_client.put(
        PATH, headers=bearer(b_token), json=replacement(skills=["Bob updated"])
    )
    assert updated_b.status_code == 200
    assert (
        await auth_client.get(PATH, headers=bearer(a_token))
    ).json() == updated_a.json()
    async with create_session_factory(db_engine).begin() as db:
        assert (await db.get(CareerProfile, a_id)).skills == ["Alice updated"]
        assert (await db.get(CareerProfile, b_id)).skills == ["Bob updated"]
        assert await db.scalar(select(func.count()).select_from(CareerProfile)) == 2


@pytest.mark.parametrize("revision", [2, 2**31, 2**63, 10**100])
async def test_nonmatching_positive_revision_is_conflict_not_database_error(
    auth_client, profile_account, revision
):
    _, token = profile_account
    before = (await auth_client.post(PATH, headers=bearer(token), json={})).json()
    assert_error(
        await auth_client.put(
            PATH, headers=bearer(token), json=replacement(revision=revision)
        ),
        409,
        "career_profile_conflict",
    )
    assert (await auth_client.get(PATH, headers=bearer(token))).json() == before


async def test_domain_enters_and_leaves_clean_transactions(
    auth_client, db_engine, profile_account, monkeypatch
):
    owner_id, token = profile_account
    observations = []

    def checked(original):
        async def wrapped(*args):
            db = args[-1]
            assert args[0] == owner_id
            assert not db.in_transaction()
            assert db_engine.pool.checkedout() == 0
            try:
                return await original(*args)
            finally:
                assert not db.in_transaction()
                assert db_engine.pool.checkedout() == 0
                observations.append(original.__name__)

        return wrapped

    for name in ["get", "create", "replace"]:
        monkeypatch.setattr(service, name, checked(getattr(service, name)))
    assert_error(
        await auth_client.get(PATH, headers=bearer(token)),
        404,
        "career_profile_not_found",
    )
    assert (
        await auth_client.post(PATH, headers=bearer(token), json={})
    ).status_code == 201
    assert (await auth_client.get(PATH, headers=bearer(token))).status_code == 200
    assert (
        await auth_client.put(PATH, headers=bearer(token), json=replacement())
    ).status_code == 200
    assert_error(
        await auth_client.put(PATH, headers=bearer(token), json=replacement()),
        409,
        "career_profile_conflict",
    )
    assert_error(
        await auth_client.post(PATH, headers=bearer(token), json={}),
        409,
        "career_profile_already_exists",
    )
    assert observations == ["get", "create", "get", "replace", "replace", "create"]


@pytest.mark.parametrize("method", ["POST", "PUT"])
async def test_executed_write_rolls_back_on_database_failure(
    auth_client, db_engine, profile_account, method
):
    owner_id, token = profile_account
    before = None
    if method == "PUT":
        before = (await auth_client.post(PATH, headers=bearer(token), json={})).json()
    executed = []

    def fail_after_write(
        connection, cursor, statement, parameters, context, executemany
    ):
        if statement.startswith(
            ("INSERT INTO career_profiles", "UPDATE career_profiles")
        ):
            executed.append(True)
            # The real DML ran; another real DB statement now fails before commit.
            connection.exec_driver_sql("SELECT career_profile_private_failure()")

    event.listen(db_engine.sync_engine, "after_cursor_execute", fail_after_write)
    try:
        response = await auth_client.request(
            method,
            PATH,
            headers=bearer(token),
            json=replacement(skills=["private career data"])
            if method == "PUT"
            else {"skills": ["private career data"]},
        )
    finally:
        event.remove(db_engine.sync_engine, "after_cursor_execute", fail_after_write)
    assert executed == [True]
    assert_error(response, 503, "career_profile_unavailable")
    for detail in [
        "SELECT",
        "career_profile_private_failure",
        "private career data",
        token,
    ]:
        assert detail not in response.text
    assert db_engine.pool.checkedout() == 0
    if before is None:
        async with create_session_factory(db_engine).begin() as db:
            assert await db.get(CareerProfile, owner_id) is None
        assert (
            await auth_client.post(PATH, headers=bearer(token), json={})
        ).status_code == 201
    else:
        assert (await auth_client.get(PATH, headers=bearer(token))).json() == before
        assert (
            await auth_client.put(PATH, headers=bearer(token), json=replacement())
        ).status_code == 200


async def test_other_insert_constraint_failure_is_not_duplicate(
    auth_client, db_engine, profile_account
):
    _, token = profile_account
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                "ALTER TABLE career_profiles ADD CONSTRAINT private_test_constraint "
                "CHECK (revision > 1)"
            )
        )
    response = await auth_client.post(PATH, headers=bearer(token), json={})
    assert_error(response, 503, "career_profile_unavailable")
    assert "private_test_constraint" not in response.text
    async with create_session_factory(db_engine).begin() as db:
        assert await db.scalar(select(func.count()).select_from(CareerProfile)) == 0


async def test_failed_commit_never_returns_created_response(
    auth_client, db_engine, profile_account
):
    owner_id, token = profile_account
    async with create_session_factory(db_engine).begin() as db:
        # Existing JWT Sessions do not require a LocalCredential to authenticate.
        await db.delete(await db.get(LocalCredential, owner_id))
        await db.execute(
            text(
                "ALTER TABLE career_profiles ADD CONSTRAINT private_commit_failure "
                "FOREIGN KEY (user_id) REFERENCES local_credentials(user_id) "
                "DEFERRABLE INITIALLY DEFERRED"
            )
        )
    inserted = []

    def observe_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO career_profiles"):
            inserted.append(True)

    event.listen(db_engine.sync_engine, "after_cursor_execute", observe_insert)
    try:
        response = await auth_client.post(PATH, headers=bearer(token), json={})
    finally:
        event.remove(db_engine.sync_engine, "after_cursor_execute", observe_insert)
    assert inserted == [True]  # INSERT/RETURNING succeeds; only COMMIT rejects it.
    assert_error(response, 503, "career_profile_unavailable")
    assert "private_commit_failure" not in response.text
    assert "location" not in response.headers
    assert db_engine.pool.checkedout() == 0
    async with create_session_factory(db_engine).begin() as db:
        assert await db.get(CareerProfile, owner_id) is None


async def test_missing_owner_fk_is_not_duplicate(db_engine):
    async with create_session_factory(db_engine)() as db:
        with pytest.raises(IntegrityError) as error:
            await service.create(uuid4(), CareerProfileCreate(), db)
        assert error.value.orig.sqlstate == "23503"
        assert not db.in_transaction()


@pytest.mark.parametrize("table", ["sessions", "career_profiles"])
async def test_database_unavailable_in_auth_or_domain_is_sanitized(
    auth_client, db_engine, profile_account, table
):
    _, token = profile_account
    async with db_engine.begin() as connection:
        await connection.execute(text(f"DROP TABLE {table}"))
    response = await auth_client.get(PATH, headers=bearer(token))
    assert_error(response, 503, "career_profile_unavailable")
    assert token not in response.text and table not in response.text


@pytest.mark.parametrize(
    "skills",
    [[123], [{"name": "private stored data"}], [" Python "], ["Python", "python"]],
)
async def test_dirty_stored_json_is_generic_failure_without_repair(
    auth_client, auth_app, db_engine, profile_account, skills
):
    owner_id, token = profile_account
    async with create_session_factory(db_engine).begin() as db:
        db.add(CareerProfile(user_id=owner_id, skills=skills))
    response = await auth_client.get(PATH, headers=bearer(token))
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    # Framework still propagates the programming/data error to server tooling.
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=auth_app), base_url="http://test"
    ) as client:
        with pytest.raises(ValidationError):
            await client.get(PATH, headers=bearer(token))
    async with create_session_factory(db_engine).begin() as db:
        stored = await db.get(CareerProfile, owner_id)
        assert stored.skills == skills and stored.revision == 1


async def test_programming_error_is_not_swallowed(
    auth_client, auth_app, profile_account, monkeypatch
):
    _, token = profile_account

    async def broken(*_args):
        raise RuntimeError("private programming error detail")

    monkeypatch.setattr(service, "get", broken)
    response = await auth_client.get(PATH, headers=bearer(token))
    assert response.status_code == 500 and response.text == "Internal Server Error"
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=auth_app), base_url="http://test"
    ) as client:
        with pytest.raises(RuntimeError, match="private programming error detail"):
            await client.get(PATH, headers=bearer(token))


async def test_concurrent_creation_has_one_winner(
    auth_client, db_engine, profile_account
):
    _, token = profile_account
    responses = await asyncio.gather(
        *[
            auth_client.post(PATH, headers=bearer(token), json={"skills": [name]})
            for name in ["first", "second"]
        ]
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    winner = next(
        response.json() for response in responses if response.status_code == 201
    )
    assert_error(
        next(response for response in responses if response.status_code == 409),
        409,
        "career_profile_already_exists",
    )
    assert (await auth_client.get(PATH, headers=bearer(token))).json() == winner
    async with create_session_factory(db_engine).begin() as db:
        assert await db.scalar(select(func.count()).select_from(CareerProfile)) == 1


async def test_concurrent_http_updates_have_one_winner(auth_client, profile_account):
    _, token = profile_account
    assert (
        await auth_client.post(PATH, headers=bearer(token), json={})
    ).status_code == 201
    responses = await asyncio.gather(
        *[
            auth_client.put(
                PATH, headers=bearer(token), json=replacement(skills=[name])
            )
            for name in ["Python", "Rust"]
        ]
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(
        response.json() for response in responses if response.status_code == 200
    )
    assert winner["revision"] == 2
    assert_error(
        next(response for response in responses if response.status_code == 409),
        409,
        "career_profile_conflict",
    )
    assert (await auth_client.get(PATH, headers=bearer(token))).json() == winner


async def test_stale_cas_waits_for_commit_then_cannot_overwrite(db_engine):
    factory = create_session_factory(db_engine)
    owner = User(identity_issuer="local", identity_subject="cas-owner", status="active")
    async with factory.begin() as db:
        db.add(owner)
        await db.flush()
        db.add(CareerProfile(user_id=owner.id))
    contender = None
    async with factory() as winner, factory() as loser:
        assert winner is not loser
        try:
            async with winner.begin():
                won = await winner.execute(
                    update(service.PROFILE)
                    .where(
                        service.PROFILE.c.user_id == owner.id,
                        service.PROFILE.c.revision == 1,
                    )
                    .values(
                        skills=["winner"],
                        revision=service.PROFILE.c.revision + 1,
                        updated_at=func.clock_timestamp(),
                    )
                    .returning(service.PROFILE.c.revision)
                )
                assert won.scalar_one() == 2
                xid = await winner.scalar(text("SELECT pg_current_xact_id()::text"))
                contender = asyncio.create_task(
                    service.replace(
                        owner.id,
                        CareerProfileUpdate(**replacement(skills=["loser"])),
                        loser,
                    )
                )
                # Observe a real lock wait on this transaction, not a timing assumption.
                async with asyncio.timeout(5):
                    while not await winner.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks "
                            "WHERE locktype = 'transactionid' AND NOT granted "
                            "AND transactionid::text = :xid)"
                        ),
                        {"xid": xid},
                    ):
                        if contender.done():
                            await contender
                            pytest.fail("Contender finished before the winning commit")
                        await asyncio.sleep(0.01)
                assert not contender.done()
            # Winner commits here; PostgreSQL rechecks the revision predicate.
            with pytest.raises(HTTPException) as conflict:
                await asyncio.wait_for(contender, timeout=5)
            assert conflict.value.status_code == 409
            assert conflict.value.detail == "career_profile_conflict"
            assert not winner.in_transaction() and not loser.in_transaction()
        finally:
            if contender is not None and not contender.done():
                contender.cancel()
                await asyncio.gather(contender, return_exceptions=True)
    async with factory.begin() as db:
        profile = await db.get(CareerProfile, owner.id)
        assert profile.revision == 2 and profile.skills == ["winner"]


async def test_malformed_json_and_streamed_body_bound(auth_client, profile_account):
    _, token = profile_account
    headers = {**bearer(token), "Content-Type": "application/json"}
    assert_error(
        await auth_client.post(PATH, headers=headers, content=b'{"skills":["private'),
        422,
        "invalid_request",
    )

    async def oversized():
        yield b'{"career_summary":"'
        for _ in range(MAX_CAREER_PROFILE_BODY_BYTES // 1024):
            yield b"x" * 1024
        yield b'"}'

    assert_error(
        await auth_client.post(PATH, headers=headers, content=oversized()),
        413,
        "request_too_large",
    )
    # Content-Length is deliberately false; the actual streamed bytes decide.
    assert_error(
        await auth_client.put(
            PATH, headers={**headers, "Content-Length": "1"}, content=oversized()
        ),
        413,
        "request_too_large",
    )
    # A valid body at the byte limit is accepted, rather than off by one.
    body = b"{}" + b" " * (MAX_CAREER_PROFILE_BODY_BYTES - 2)
    assert (
        await auth_client.post(PATH, headers=headers, content=body)
    ).status_code == 201


async def test_cache_policy_is_scoped_and_covers_redirects_and_method_errors(
    auth_client,
):
    redirected = await auth_client.get(f"{PATH}/", follow_redirects=False)
    assert redirected.status_code == 307
    assert redirected.headers["cache-control"] == "no-store"
    disallowed = await auth_client.delete(PATH)
    assert disallowed.status_code == 405
    assert disallowed.headers["cache-control"] == "no-store"
    health = await auth_client.get("/health")
    assert health.status_code == 200 and "cache-control" not in health.headers


def test_openapi_bearer_and_singleton_contract(auth_app):
    schema = auth_app.openapi()
    operations = schema["paths"][PATH]
    assert set(operations) == {"get", "post", "put"}
    for operation in operations.values():
        assert operation["security"] == [{"HTTPBearer": []}]
    models = schema["components"]["schemas"]
    assert set(models["CareerProfileResponse"]["properties"]) == RESPONSE_FIELDS
    assert set(models["CareerProfileUpdate"]["required"]) == set(FIELDS) | {
        "expected_revision"
    }
    assert "expected_revision" not in models["CareerProfileCreate"]["properties"]
    assert models["CareerProfileCreate"]["additionalProperties"] is False
    assert models["CareerProfileUpdate"]["additionalProperties"] is False
