"""Real cryptographic operations and strict token/input policy without PostgreSQL."""

import asyncio
import threading
from datetime import UTC, datetime
from uuid import uuid4

import jwt
import pytest

from career_copilot.auth.passwords import PasswordService, normalize_username
from career_copilot.auth.schemas import LoginRequest, RegisterRequest
from career_copilot.auth.tokens import (
    JWT_AUDIENCE,
    JWT_ISSUER,
    REQUIRED_CLAIMS,
    InvalidAccessToken,
    TokenClaims,
    TokenService,
)


def payload() -> dict:
    now = int(datetime.now(UTC).timestamp())
    return {
        "sub": str(uuid4()),
        "jti": str(uuid4()),
        "iat": now,
        "exp": now + 1800,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }


@pytest.mark.parametrize("claim", REQUIRED_CLAIMS)
def test_every_claim_is_required(claim, auth_settings):
    claims = payload()
    del claims[claim]
    token = jwt.encode(claims, auth_settings.jwt_secret, algorithm="HS256")
    with pytest.raises(InvalidAccessToken):
        TokenService(auth_settings).validate(token)


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("sub", "not-a-uuid"),
        ("sub", 42),
        ("jti", "not-a-uuid"),
        ("jti", 42),
        ("iat", True),
        ("iat", "123"),
        ("iat", -1),
        ("iat", 1e20),
        ("exp", True),
        ("exp", "9999999999"),
        ("exp", 9999999999.5),
        ("exp", 10**100),
        ("iss", "foreign-issuer"),
        ("aud", "foreign-api"),
        ("aud", [JWT_AUDIENCE]),
        ("nbf", "123"),
    ],
)
def test_invalid_claims_are_rejected(claim, value, auth_settings):
    claims = payload() | {claim: value}
    token = jwt.encode(claims, auth_settings.jwt_secret, algorithm="HS256")
    with pytest.raises(InvalidAccessToken):
        TokenService(auth_settings).validate(token)


@pytest.mark.parametrize("kind", ["expired", "future_iat", "iat_after_exp"])
def test_token_time_policy(kind, auth_settings):
    claims = payload()
    now = claims["iat"]
    if kind == "expired":
        claims.update(iat=now - 1800, exp=now - 1)
    elif kind == "future_iat":
        claims["iat"] = now + 31
    else:
        claims["iat"] = claims["exp"]
    token = jwt.encode(claims, auth_settings.jwt_secret, algorithm="HS256")
    with pytest.raises(InvalidAccessToken):
        TokenService(auth_settings).validate(token)


@pytest.mark.parametrize("algorithm", ["HS384", "none"])
def test_algorithm_is_not_selected_by_token(algorithm, auth_settings):
    key = "" if algorithm == "none" else auth_settings.jwt_secret + "-negative-test"
    token = jwt.encode(payload(), key, algorithm=algorithm)
    with pytest.raises(InvalidAccessToken):
        TokenService(auth_settings).validate(token)


@pytest.mark.parametrize("token", ["", "broken.jwt", "a" * 4097, "非ASCII"])
def test_malformed_or_oversize_token_is_safe(token, auth_settings):
    with pytest.raises(InvalidAccessToken) as error:
        TokenService(auth_settings).validate(token)
    assert str(error.value) == ""


def test_wrong_signature_is_rejected(auth_settings):
    token = jwt.encode(payload(), "different-test-only-signing-key" * 2, "HS256")
    with pytest.raises(InvalidAccessToken):
        TokenService(auth_settings).validate(token)


def test_issue_and_validate_round_trip(auth_settings):
    data = payload()
    claims = TokenClaims(
        user_id=uuid4(),
        session_id=uuid4(),
        issued_at=data["iat"],
        expires_at=data["exp"],
    )
    tokens = TokenService(auth_settings)
    assert tokens.validate(tokens.issue(claims)) == claims


def test_shared_normalization_preserves_password_semantics():
    password = "  My Cafe\u0301 passphrase  "
    expected = "  My Café passphrase  "
    register = RegisterRequest(username="  Ryan.Wang  ", password=password)
    login = LoginRequest(username="RYAN.WANG", password=password)
    assert register.username == login.username == "ryan.wang"
    assert register.password.get_secret_value() == expected
    assert login.password.get_secret_value() == expected
    assert password not in repr(register)
    assert expected not in repr(register)
    assert normalize_username("\u3000Alice\u3000") == "alice"


def test_non_ascii_username_cannot_become_ascii_through_lowercase():
    with pytest.raises(ValueError):
        normalize_username("\u212alice")


async def test_real_argon2_salt_and_dummy_verify():
    service = await PasswordService.create()
    password = "a long real passphrase"
    first, second = await asyncio.gather(service.hash(password), service.hash(password))
    assert first.startswith("$argon2id$")
    assert first != second
    assert password not in first
    assert await service.verify(password, first)
    assert not await service.verify("wrong", first)
    assert not await service.verify(password, None)


async def test_hash_offload_does_not_block_loop_and_bounds_concurrency(monkeypatch):
    service = await PasswordService.create()
    original = service._hasher.hash
    release = threading.Event()
    two_started = threading.Event()
    lock = threading.Lock()
    started_threads = []

    def blocking_hash(password):
        with lock:
            started_threads.append(threading.get_ident())
            if len(started_threads) == 2:
                two_started.set()
        assert release.wait(timeout=5)
        return original(password)

    monkeypatch.setattr(service._hasher, "hash", blocking_hash)
    tasks = [asyncio.create_task(service.hash("a real passphrase")) for _ in range(3)]
    try:
        assert await asyncio.wait_for(asyncio.to_thread(two_started.wait, 3), timeout=4)
        assert len(started_threads) == 2
        assert threading.get_ident() not in started_threads
    finally:
        release.set()
        await asyncio.gather(*tasks)
