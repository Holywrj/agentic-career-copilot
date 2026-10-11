"""Owned resume APIs against Alembic-built PostgreSQL and a real private directory."""

import asyncio
import re
import threading
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction
from starlette.datastructures import UploadFile

from career_copilot.db.models import (
    AuthSession,
    Job,
    JobAnalysis,
    LocalCredential,
    Resume,
    User,
)
from career_copilot.db.session import create_session_factory
from career_copilot.resume import service
from career_copilot.resume.storage import StorageUnavailable, run_io
from career_copilot.resume.uploads import MAX_RESUME_BYTES, MAX_RESUME_REQUEST_BYTES

pytestmark = pytest.mark.database
PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"
FIELDS = {
    "id",
    "original_filename",
    "media_type",
    "size_bytes",
    "status",
    "created_at",
    "updated_at",
}


async def account(client, name="resume-alice"):
    data = {"username": name, "password": "Resume test passphrase only 🐍"}
    registered = await client.post("/auth/register", json=data)
    assert registered.status_code == 201
    login = await client.post("/auth/login", json=data)
    assert login.status_code == 200
    return UUID(registered.json()["id"]), {
        "Authorization": "Bearer " + login.json()["access_token"]
    }


@pytest_asyncio.fixture
async def owner(auth_client):
    return await account(auth_client)


async def upload(
    client, headers, name="resume.pdf", content=PDF, mime="application/pdf"
):
    return await client.post(
        "/resumes", headers=headers, files={"file": (name, content, mime)}
    )


def error(response, status, detail):
    assert response.status_code == status, response.text
    assert response.json() == {"detail": detail}
    assert response.headers["cache-control"] == "no-store"


async def row_for(db_engine, resume_id):
    async with create_session_factory(db_engine).begin() as db:
        return await db.get(Resume, UUID(resume_id))


async def assert_empty(db_engine, storage):
    async with create_session_factory(db_engine).begin() as db:
        assert await db.scalar(select(func.count()).select_from(Resume)) == 0
    assert list(storage.root.iterdir()) == [storage.temporary]
    assert list(storage.temporary.iterdir()) == []
    assert db_engine.pool.checkedout() == 0


@pytest.mark.parametrize(
    "name", ["resume.pdf", "resume.v2.pdf", "My.Resume.PDF", "王小明.简历.pdf"]
)
async def test_upload_metadata_binary_and_derived_owner(
    auth_client, auth_app, db_engine, owner, name
):
    user_id, headers = owner
    result = await upload(auth_client, headers, name)
    assert result.status_code == 201, result.text
    resource = result.json()
    assert set(resource) == FIELDS
    assert resource["original_filename"] == name
    assert resource["media_type"] == "application/pdf"
    assert resource["size_bytes"] == len(PDF)
    assert resource["status"] == "uploaded"
    assert result.headers["location"] == "/resumes/" + resource["id"]
    assert result.headers["cache-control"] == "no-store"
    row = await row_for(db_engine, resource["id"])
    assert row.user_id == user_id
    assert row.original_filename == name
    assert re.fullmatch("[a-f0-9]{32}", row.storage_key)
    assert (
        row.extracted_text is row.structured_content is row.supersedes_resume_id is None
    )
    storage = auth_app.state.resume_storage
    assert (storage.root / row.storage_key).read_bytes() == PDF
    assert list(storage.temporary.iterdir()) == []
    assert str(storage.root) not in result.text
    assert row.storage_key not in result.text
    assert (
        await auth_client.get(result.headers["location"], headers=headers)
    ).json() == resource


@pytest.mark.parametrize(
    "name,mime,content",
    [
        ("resume.docx", "application/pdf", PDF),
        ("resume.pdf", "application/octet-stream", PDF),
        ("resume.pdf", "text/plain", PDF),
        ("resume.pdf", None, PDF),
        ("resume.pdf", "application/pdf", b"not a PDF"),
    ],
)
async def test_unsupported_type_has_no_row_or_files(
    auth_client, auth_app, db_engine, owner, name, mime, content
):
    if mime is None:
        response = await auth_client.post(
            "/resumes",
            headers={
                **owner[1],
                "Content-Type": "multipart/form-data; boundary=boundary",
            },
            content=multipart(name, content).replace(
                b"Content-Type: application/pdf\r\n", b""
            ),
        )
    else:
        response = await upload(auth_client, owner[1], name, content, mime)
    error(
        response,
        415,
        "unsupported_resume_type",
    )
    await assert_empty(db_engine, auth_app.state.resume_storage)


def multipart(name, content=PDF, field="file", ending=True):
    body = (
        b'--boundary\r\nContent-Disposition: form-data; name="'
        + field.encode()
        + b'"; filename="'
        + name.encode("utf-8", errors="surrogatepass")
        + b'"\r\nContent-Type: application/pdf\r\n\r\n'
        + content
    )
    return body + (b"\r\n--boundary--\r\n" if ending else b"")


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        ".",
        "..",
        "../../etc/passwd",
        "..\\..\\resume.pdf",
        "x/y.pdf",
        "x:y.pdf",
        "x\x00.pdf",
        "x\x01.pdf",
        "x" * 252 + ".pdf",
    ],
)
async def test_invalid_filename(auth_client, auth_app, db_engine, owner, name):
    response = await auth_client.post(
        "/resumes",
        headers={**owner[1], "Content-Type": "multipart/form-data; boundary=boundary"},
        content=multipart(name),
    )
    error(response, 422, "invalid_request")
    await assert_empty(db_engine, auth_app.state.resume_storage)


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "extra",
        "owner",
        "multiple",
        "wrong_name",
        "no_boundary",
        "broken",
        "truncated",
        "incomplete_next_part",
        "json",
        "empty",
    ],
)
async def test_multipart_validation_is_sanitized(
    auth_client, auth_app, db_engine, owner, kind
):
    headers = owner[1]
    options = {}
    if kind == "missing":
        options = {"content": b"--boundary--\r\n"}
    elif kind in {"extra", "owner"}:
        options = {
            "files": {"file": ("resume.pdf", PDF, "application/pdf")},
            "data": {"user_id" if kind == "owner" else "extra": "private raw value"},
        }
    elif kind == "multiple":
        options = {
            "files": [
                ("file", ("a.pdf", PDF, "application/pdf")),
                ("file", ("b.pdf", PDF, "application/pdf")),
            ]
        }
    elif kind == "wrong_name":
        options = {"content": multipart("resume.pdf", field="other")}
    elif kind == "no_boundary":
        headers = {**headers, "Content-Type": "multipart/form-data"}
        options = {"content": b"private malformed body"}
    elif kind == "broken":
        options = {"content": b"not a multipart boundary"}
    elif kind == "truncated":
        options = {"content": multipart("resume.pdf", ending=False)}
    elif kind == "incomplete_next_part":
        options = {
            "content": multipart("resume.pdf", ending=False) + b"\r\n--boundary\r\n"
        }
    elif kind == "json":
        options = {"json": {"file": "private"}}
    elif kind == "empty":
        options = {"files": {"file": ("resume.pdf", b"", "application/pdf")}}
    if "content" in options and kind != "no_boundary":
        headers = {**headers, "Content-Type": "multipart/form-data; boundary=boundary"}
    error(
        await auth_client.post("/resumes", headers=headers, **options),
        422,
        "invalid_request",
    )
    await assert_empty(db_engine, auth_app.state.resume_storage)


@pytest.mark.parametrize(
    "path",
    [
        "/resumes/private-invalid-uuid",
        "/resumes?limit=private",
        "/resumes?limit=0",
        "/resumes?limit=101",
        "/resumes?offset=private",
        "/resumes?offset=-1",
        "/resumes?offset=9223372036854775808",
    ],
)
async def test_framework_validation_is_feature_local(auth_client, owner, path):
    error(await auth_client.get(path, headers=owner[1]), 422, "invalid_request")
    error(
        await auth_client.delete("/resumes/private-invalid-uuid", headers=owner[1]),
        422,
        "invalid_request",
    )


async def test_exact_file_size_boundary_and_one_byte_over(
    auth_client, auth_app, db_engine, owner
):
    headers = owner[1]
    content = PDF + b" " * (MAX_RESUME_BYTES - len(PDF))
    response = await upload(auth_client, headers, content=content)
    assert response.status_code == 201
    assert response.json()["size_bytes"] == MAX_RESUME_BYTES
    row = await row_for(db_engine, response.json()["id"])
    storage = auth_app.state.resume_storage
    assert (storage.root / row.storage_key).read_bytes() == content
    error(
        await upload(auth_client, headers, content=content + b"x"),
        413,
        "request_too_large",
    )
    assert set(storage.root.iterdir()) == {
        storage.temporary,
        storage.root / row.storage_key,
    }
    assert list(storage.temporary.iterdir()) == []


@pytest.mark.parametrize("content_length", [None, "1"])
async def test_streamed_request_limit_and_spool_cleanup(
    auth_client, auth_app, db_engine, owner, content_length, monkeypatch
):
    from starlette import formparsers

    spools = []
    original = formparsers.SpooledTemporaryFile

    def tracked(*args, **kwargs):
        spool = original(*args, **kwargs)
        spools.append(spool)
        return spool

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked)

    async def body():
        yield multipart("resume.pdf", b"", ending=False)
        for _ in range(MAX_RESUME_REQUEST_BYTES // 1024 + 1):
            yield b"x" * 1024
        pytest.fail("Ingress must stop reading oversized requests")

    headers = {**owner[1], "Content-Type": "multipart/form-data; boundary=boundary"}
    if content_length:
        headers["Content-Length"] = content_length
    error(
        await auth_client.post("/resumes", headers=headers, content=body()),
        413,
        "request_too_large",
    )
    assert spools and all(spool.closed for spool in spools)
    await assert_empty(db_engine, auth_app.state.resume_storage)


async def test_auth_before_body_and_storage(auth_client, auth_app, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Unauthenticated request entered storage")

    monkeypatch.setattr(auth_app.state.resume_storage, "save", forbidden)

    async def body():
        pytest.fail("Unauthenticated request consumed body")
        yield b""

    response = await auth_client.post(
        "/resumes", headers={"Content-Type": "multipart/form-data"}, content=body()
    )
    error(response, 401, "authentication_required")
    assert response.headers["www-authenticate"] == "Bearer"


async def test_list_get_delete_and_cross_user_isolation(
    auth_client, auth_app, db_engine, owner
):
    _, a = owner
    _, b = await account(auth_client, "resume-bob")
    a_one = (await upload(auth_client, a)).json()
    a_two = (await upload(auth_client, a, "second.pdf")).json()
    b_one = (await upload(auth_client, b)).json()
    assert (await auth_client.get("/resumes", headers=a)).json() == {
        "items": [a_two, a_one],
        "limit": 20,
        "offset": 0,
    }
    assert (await auth_client.get("/resumes?limit=1&offset=1", headers=a)).json() == {
        "items": [a_one],
        "limit": 1,
        "offset": 1,
    }
    assert (await auth_client.get("/resumes?offset=2", headers=a)).json()["items"] == []
    for headers, other in [(a, b_one), (b, a_one)]:
        for method in ["GET", "DELETE"]:
            error(
                await auth_client.request(
                    method, "/resumes/" + other["id"], headers=headers
                ),
                404,
                "resume_not_found",
            )
            error(
                await auth_client.request(
                    method, "/resumes/" + str(uuid4()), headers=headers
                ),
                404,
                "resume_not_found",
            )
    row = await row_for(db_engine, a_one["id"])
    path = auth_app.state.resume_storage.root / row.storage_key
    response = await auth_client.delete("/resumes/" + a_one["id"], headers=a)
    assert response.status_code == 204 and response.content == b""
    assert await row_for(db_engine, a_one["id"]) is None
    assert not path.exists()
    error(
        await auth_client.delete("/resumes/" + a_one["id"], headers=a),
        404,
        "resume_not_found",
    )
    assert (await auth_client.get("/resumes", headers=b)).json()["items"] == [b_one]


async def test_legacy_null_metadata_and_missing_binary_delete(
    auth_client, db_engine, owner
):
    user_id, headers = owner
    resume = Resume(
        user_id=user_id,
        original_filename="old.pdf",
        storage_key=uuid4().hex,
        parsing_status="legacy-unparsed",
    )
    async with create_session_factory(db_engine).begin() as db:
        db.add(resume)
    result = await auth_client.get(f"/resumes/{resume.id}", headers=headers)
    assert result.status_code == 200
    assert result.json()["media_type"] is result.json()["size_bytes"] is None
    assert result.json()["status"] == "legacy-unparsed"
    assert (
        await auth_client.delete(f"/resumes/{resume.id}", headers=headers)
    ).status_code == 204


@pytest.mark.parametrize("reference", ["lineage", "analysis"])
async def test_existing_fk_blocks_delete_without_touching_binary(
    auth_client, auth_app, db_engine, owner, reference
):
    user_id, headers = owner
    resource = (await upload(auth_client, headers)).json()
    row = await row_for(db_engine, resource["id"])
    async with create_session_factory(db_engine).begin() as db:
        if reference == "lineage":
            db.add(
                Resume(
                    user_id=user_id,
                    supersedes_resume_id=row.id,
                    storage_key=uuid4().hex,
                    original_filename="later.pdf",
                    parsing_status="uploaded",
                )
            )
        else:
            job = Job(user_id=user_id, description="Backend job")
            db.add(job)
            await db.flush()
            db.add(
                JobAnalysis(
                    user_id=user_id,
                    resume_id=row.id,
                    job_id=job.id,
                    profile_revision=1,
                    input_snapshot={},
                    input_schema_version=1,
                    status="fixture",
                    analysis_version="fixture",
                )
            )
    error(
        await auth_client.delete("/resumes/" + resource["id"], headers=headers),
        409,
        "resume_in_use",
    )
    assert (auth_app.state.resume_storage.root / row.storage_key).read_bytes() == PDF
    assert await row_for(db_engine, resource["id"]) is not None


async def test_storage_failure_has_safe_response_and_no_metadata(
    auth_client, auth_app, db_engine, owner, monkeypatch
):
    import os

    def fail(*args):
        raise PermissionError("/private/storage/path private OS error")

    monkeypatch.setattr(os, "write", fail)
    error(await upload(auth_client, owner[1]), 503, "resume_storage_unavailable")
    await assert_empty(db_engine, auth_app.state.resume_storage)


@pytest.mark.parametrize("commit_failure", [False, True])
async def test_confirmed_db_failure_compensates_finalized_binary(
    auth_client, auth_app, db_engine, owner, commit_failure
):
    user_id, headers = owner
    if commit_failure:
        async with create_session_factory(db_engine).begin() as db:
            await db.delete(await db.get(LocalCredential, user_id))
            await db.execute(
                text(
                    "ALTER TABLE resumes ADD CONSTRAINT private_commit_failure "
                    "FOREIGN KEY (user_id) REFERENCES local_credentials(user_id) "
                    "DEFERRABLE INITIALLY DEFERRED"
                )
            )
    observed = []

    def after_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO resumes"):
            assert len(list(auth_app.state.resume_storage.root.iterdir())) == 2
            observed.append(True)
            if not commit_failure:
                connection.exec_driver_sql("SELECT private_resume_database_failure()")

    event.listen(db_engine.sync_engine, "after_cursor_execute", after_insert)
    try:
        response = await upload(auth_client, headers)
    finally:
        event.remove(db_engine.sync_engine, "after_cursor_execute", after_insert)
    error(response, 503, "resume_unavailable")
    assert observed == [True]
    assert "location" not in response.headers
    await assert_empty(db_engine, auth_app.state.resume_storage)


async def test_compensation_failure_keeps_safe_opaque_log(
    auth_client, auth_app, db_engine, owner, monkeypatch, caplog
):
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                "ALTER TABLE resumes ADD CONSTRAINT private_insert_failure "
                "CHECK (size_bytes < 1)"
            )
        )

    async def fail(key):
        raise StorageUnavailable("private /filesystem/path")

    monkeypatch.setattr(auth_app.state.resume_storage, "delete", fail)
    error(await upload(auth_client, owner[1]), 503, "resume_storage_unavailable")
    storage = auth_app.state.resume_storage
    binary = next(path for path in storage.root.iterdir() if path != storage.temporary)
    assert binary.name in caplog.text and "operation=upload" in caplog.text
    assert (
        str(storage.root) not in caplog.text
        and "private /filesystem/path" not in caplog.text
    )
    async with create_session_factory(db_engine).begin() as db:
        assert await db.scalar(select(func.count()).select_from(Resume)) == 0


@pytest.mark.parametrize("actually_committed", [False, True])
async def test_uncertain_commit_retains_binary(
    auth_client, auth_app, db_engine, owner, monkeypatch, caplog, actually_committed
):
    from sqlalchemy.ext.asyncio import AsyncSessionTransaction

    original_exit = AsyncSessionTransaction.__aexit__

    async def uncertain_exit(transaction, kind, error_value, traceback):
        if kind is None:
            # Auth is read-only. Only the upload transaction has pending writes.
            rows = await transaction.session.scalar(
                select(func.count()).select_from(Resume)
            )
            if rows:
                if actually_committed:
                    await original_exit(transaction, kind, error_value, traceback)
                else:
                    await transaction.rollback()
                raise OperationalError(
                    "private COMMIT", {}, Exception("transport lost /private/path")
                )
        return await original_exit(transaction, kind, error_value, traceback)

    monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", uncertain_exit)
    error(await upload(auth_client, owner[1]), 503, "resume_unavailable")
    assert "resume_commit_uncertain" in caplog.text
    storage = auth_app.state.resume_storage
    assert len(list(storage.root.iterdir())) == 2
    assert list(storage.temporary.iterdir()) == []
    assert str(storage.root) not in caplog.text
    # Query without our failure seam to observe both possible DB outcomes.
    monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", original_exit)
    async with create_session_factory(db_engine).begin() as db:
        assert await db.scalar(select(func.count()).select_from(Resume)) == int(
            actually_committed
        )


async def test_delete_storage_failure_rolls_back_and_retry_succeeds(
    auth_client, auth_app, db_engine, owner, monkeypatch, caplog
):
    headers = owner[1]
    resource = (await upload(auth_client, headers)).json()
    row = await row_for(db_engine, resource["id"])
    storage = auth_app.state.resume_storage
    original = storage.delete

    async def fail(key):
        raise StorageUnavailable("private OS exception and path")

    monkeypatch.setattr(storage, "delete", fail)
    error(
        await auth_client.delete("/resumes/" + resource["id"], headers=headers),
        503,
        "resume_storage_unavailable",
    )
    assert await row_for(db_engine, resource["id"]) is not None
    assert (storage.root / row.storage_key).exists()
    assert row.storage_key in caplog.text and "operation=delete" in caplog.text
    assert "private OS" not in caplog.text
    monkeypatch.setattr(storage, "delete", original)
    assert (
        await auth_client.delete("/resumes/" + resource["id"], headers=headers)
    ).status_code == 204


@pytest.mark.parametrize("cancellations", [1, 2])
@pytest.mark.parametrize("phase", ["unlink", "commit"])
async def test_cancelled_delete_finishes_binary_and_transaction_before_session_close(
    auth_client, auth_app, db_engine, owner, monkeypatch, cancellations, phase
):
    headers = owner[1]
    resource = (await upload(auth_client, headers)).json()
    row = await row_for(db_engine, resource["id"])
    binary = auth_app.state.resume_storage.root / row.storage_key
    unlink_started, unlink_release = threading.Event(), threading.Event()
    commit_started, commit_release = asyncio.Event(), asyncio.Event()
    original_unlink = Path.unlink
    original_commit = AsyncSessionTransaction.commit
    original_close = AsyncSession.close
    closed_with_transaction = []

    def blocked_unlink(path, *args, **kwargs):
        if path == binary and phase == "unlink":
            unlink_started.set()
            assert unlink_release.wait(5)
        return original_unlink(path, *args, **kwargs)

    async def blocked_commit(transaction):
        if phase == "commit":
            assert not binary.exists()
            commit_started.set()
            await commit_release.wait()
        await original_commit(transaction)

    async def checked_close(db):
        closed_with_transaction.append(db.in_transaction())
        await original_close(db)

    monkeypatch.setattr(Path, "unlink", blocked_unlink)
    monkeypatch.setattr(AsyncSessionTransaction, "commit", blocked_commit)
    monkeypatch.setattr(AsyncSession, "close", checked_close)
    tasks_before = asyncio.all_tasks()
    task = asyncio.create_task(
        auth_client.delete("/resumes/" + resource["id"], headers=headers)
    )
    try:
        if phase == "unlink":
            assert await asyncio.wait_for(run_io(unlink_started.wait, 5), 6)
            assert binary.exists()
        else:
            await asyncio.wait_for(commit_started.wait(), 5)
        assert db_engine.pool.checkedout() == 1
        for _ in range(cancellations):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        unlink_release.set()
        commit_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    finally:
        unlink_release.set()
        commit_release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert not binary.exists()
    assert await row_for(db_engine, resource["id"]) is None
    assert closed_with_transaction and not any(closed_with_transaction)
    assert db_engine.pool.checkedout() == 0
    assert not (asyncio.all_tasks() - tasks_before)


async def test_delete_cancellation_before_destructive_section_preserves_resource(
    auth_client, auth_app, db_engine, owner, monkeypatch
):
    headers = owner[1]
    resource = (await upload(auth_client, headers)).json()
    row = await row_for(db_engine, resource["id"])
    ready = asyncio.Event()
    original = AsyncSession.execute

    async def block_after_delete(db, statement, *args, **kwargs):
        result = await original(db, statement, *args, **kwargs)
        if str(statement).startswith("DELETE FROM resumes"):
            ready.set()
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(AsyncSession, "execute", block_after_delete)
    task = asyncio.create_task(
        auth_client.delete("/resumes/" + resource["id"], headers=headers)
    )
    try:
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert (auth_app.state.resume_storage.root / row.storage_key).read_bytes() == PDF
    assert await row_for(db_engine, resource["id"]) is not None
    assert db_engine.pool.checkedout() == 0


@pytest.mark.parametrize("directory", ["root", "temporary"])
@pytest.mark.parametrize("operation", ["upload", "delete"])
async def test_insecure_storage_directory_has_safe_http_error(
    auth_client, auth_app, db_engine, owner, directory, operation
):
    headers = owner[1]
    resource = (await upload(auth_client, headers)).json()
    row = await row_for(db_engine, resource["id"])
    storage = auth_app.state.resume_storage
    insecure = storage.root if directory == "root" else storage.temporary
    insecure.chmod(0o777)
    try:
        response = (
            await upload(auth_client, headers)
            if operation == "upload"
            else await auth_client.delete("/resumes/" + resource["id"], headers=headers)
        )
        error(response, 503, "resume_storage_unavailable")
        assert (storage.root / row.storage_key).read_bytes() == PDF
        assert await row_for(db_engine, resource["id"]) is not None
    finally:
        insecure.chmod(0o700)


async def test_delete_commit_failure_keeps_row_and_missing_binary_retry(
    auth_client, auth_app, db_engine, owner
):
    headers = owner[1]
    resource = (await upload(auth_client, headers)).json()
    row = await row_for(db_engine, resource["id"])
    async with db_engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE FUNCTION private_delete_failure() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "RAISE EXCEPTION 'private commit failure'; END $$"
            )
        )
        await connection.execute(
            text(
                "CREATE CONSTRAINT TRIGGER private_delete_failure "
                "AFTER DELETE ON resumes DEFERRABLE INITIALLY DEFERRED "
                "FOR EACH ROW EXECUTE FUNCTION private_delete_failure()"
            )
        )
    error(
        await auth_client.delete("/resumes/" + resource["id"], headers=headers),
        503,
        "resume_unavailable",
    )
    assert await row_for(db_engine, resource["id"]) is not None
    assert not (auth_app.state.resume_storage.root / row.storage_key).exists()
    async with db_engine.begin() as connection:
        await connection.execute(text("DROP TRIGGER private_delete_failure ON resumes"))
    assert (
        await auth_client.delete("/resumes/" + resource["id"], headers=headers)
    ).status_code == 204


async def test_concurrent_deletes_have_one_success(auth_client, owner):
    headers = owner[1]
    resource = (await upload(auth_client, headers)).json()
    results = await asyncio.gather(
        *(
            auth_client.delete("/resumes/" + resource["id"], headers=headers)
            for _ in range(2)
        )
    )
    assert sorted(result.status_code for result in results) == [204, 404]


async def test_auth_database_failure_is_resume_unavailable(
    auth_client, db_engine, owner
):
    async with db_engine.begin() as connection:
        await connection.execute(text("DROP TABLE sessions"))
    error(await upload(auth_client, owner[1]), 503, "resume_unavailable")


async def test_storage_is_outside_upload_transaction(
    auth_client, auth_app, db_engine, owner, monkeypatch
):
    storage = auth_app.state.resume_storage
    original = storage.save

    async def checked(*args):
        assert db_engine.pool.checkedout() == 0
        result = await original(*args)
        assert db_engine.pool.checkedout() == 0
        return result

    monkeypatch.setattr(storage, "save", checked)
    assert (await upload(auth_client, owner[1])).status_code == 201
    assert db_engine.pool.checkedout() == 0


async def test_cache_scope_and_generic_programming_error(
    auth_client, owner, monkeypatch
):
    async def broken(*args):
        raise RuntimeError("private internal exception")

    monkeypatch.setattr(service, "get", broken)
    response = await auth_client.get("/resumes/" + str(uuid4()), headers=owner[1])
    assert response.status_code == 500 and "private" not in response.text
    assert (await auth_client.get("/resumes/", follow_redirects=False)).headers[
        "cache-control"
    ] == "no-store"
    assert "cache-control" not in (await auth_client.get("/health")).headers


def test_openapi_resumes_has_local_error_and_multipart_contract(auth_app):
    schema = auth_app.openapi()
    paths = schema["paths"]
    assert set(paths["/resumes"]) == {"get", "post"}
    assert set(paths["/resumes/{resume_id}"]) == {"get", "delete"}
    assert not any("download" in path for path in paths)
    assert set(paths["/resumes/{resume_id}/extract"]) == {"post"}
    action = paths["/resumes/{resume_id}/extract"]["post"]
    assert "requestBody" not in action
    assert set(action["responses"]) == {"200", "401", "404", "409", "422", "503"}
    for path in ["/resumes", "/resumes/{resume_id}", "/resumes/{resume_id}/extract"]:
        for operation in paths[path].values():
            assert operation["security"] == [{"HTTPBearer": []}]
            assert operation["responses"]["422"]["content"]["application/json"][
                "schema"
            ]["$ref"].endswith("/ResumeErrorResponse")
    request = paths["/resumes"]["post"]["requestBody"]
    file_schema = request["content"]["multipart/form-data"]["schema"]
    assert request["required"] and file_schema["required"] == ["file"]
    assert file_schema["additionalProperties"] is False
    assert file_schema["properties"]["file"]["format"] == "binary"
    assert (
        set(schema["components"]["schemas"]["ResumeResponse"]["properties"]) == FIELDS
    )


@pytest.mark.parametrize("state", ["revoked", "disabled", "unknown_status"])
async def test_invalidated_auth_never_reads_body_or_touches_resource(
    auth_client, auth_app, db_engine, owner, state, monkeypatch
):
    owner_id, headers = owner
    resource = (await upload(auth_client, headers)).json()
    row = await row_for(db_engine, resource["id"])
    async with create_session_factory(db_engine).begin() as db:
        if state == "revoked":
            await db.execute(
                update(AuthSession)
                .where(AuthSession.user_id == owner_id)
                .values(revoked_at=func.now())
            )
        else:
            await db.execute(
                update(User)
                .where(User.id == owner_id)
                .values(status="disabled" if state == "disabled" else "unknown")
            )

    async def forbidden(*args):
        pytest.fail("Invalidated auth entered storage")

    storage = auth_app.state.resume_storage
    monkeypatch.setattr(storage, "save", forbidden)
    monkeypatch.setattr(storage, "delete", forbidden)
    monkeypatch.setattr(storage, "read", forbidden)
    for method, path in [
        ("POST", "/resumes"),
        ("GET", "/resumes"),
        ("GET", "/resumes/" + resource["id"]),
        ("DELETE", "/resumes/" + resource["id"]),
        ("POST", "/resumes/" + resource["id"] + "/extract"),
    ]:
        error(
            await auth_client.request(method, path, headers=headers),
            401,
            "authentication_required",
        )
    assert (storage.root / row.storage_key).read_bytes() == PDF


async def test_cancelled_insert_rolls_back_and_compensates_finalized_binary(
    auth_app, db_engine, owner, monkeypatch
):
    ready = asyncio.Event()
    original = AsyncSession.execute

    async def wait_after_insert(db, statement, *args, **kwargs):
        result = await original(db, statement, *args, **kwargs)
        if str(statement).startswith("INSERT INTO resumes"):
            ready.set()
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(AsyncSession, "execute", wait_after_insert)
    file = UploadFile(BytesIO(PDF), filename="resume.pdf")

    async def work():
        async with create_session_factory(db_engine)() as db:
            await service.create(
                owner[0], file, "resume.pdf", db, auth_app.state.resume_storage
            )

    task = asyncio.create_task(work())
    try:
        await asyncio.wait_for(ready.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await file.close()
        if not task.done():
            task.cancel()
    await assert_empty(db_engine, auth_app.state.resume_storage)


async def test_delete_directory_sync_failure_can_retry_missing_file(
    auth_client, auth_app, db_engine, owner, monkeypatch
):
    headers = owner[1]
    resource = (await upload(auth_client, headers)).json()
    row = await row_for(db_engine, resource["id"])
    storage = auth_app.state.resume_storage
    original = storage._sync_directory

    def fail():
        raise OSError("private sync failure /private/storage")

    monkeypatch.setattr(storage, "_sync_directory", fail)
    error(
        await auth_client.delete("/resumes/" + resource["id"], headers=headers),
        503,
        "resume_storage_unavailable",
    )
    assert await row_for(db_engine, resource["id"]) is not None
    assert not (storage.root / row.storage_key).exists()
    monkeypatch.setattr(storage, "_sync_directory", original)
    assert (
        await auth_client.delete("/resumes/" + resource["id"], headers=headers)
    ).status_code == 204


@pytest.mark.parametrize("name", ["x\ud800.pdf", "x\x7f.pdf", "x\n.pdf"])
def test_filename_surrogates_and_controls_are_rejected(name):
    from fastapi import HTTPException

    from career_copilot.resume.uploads import validate_metadata

    file = UploadFile(BytesIO(PDF), filename=name)
    with pytest.raises(HTTPException) as raised:
        validate_metadata(file)
    assert raised.value.status_code == 422
    assert raised.value.detail == "invalid_request"
    file.file.close()


async def test_utf8_filename_length_boundary(auth_client, owner):
    # 83 Chinese characters + two ASCII chars + extension = 255 UTF-8 bytes.
    name = "王" * 83 + "ab.pdf"
    assert len(name.encode()) == 255
    assert (await upload(auth_client, owner[1], name)).status_code == 201
    error(await upload(auth_client, owner[1], "c" + name), 422, "invalid_request")


async def test_client_disconnect_closes_parser_spool_without_metadata(
    auth_app, db_engine, owner, monkeypatch
):
    from starlette import formparsers
    from starlette.requests import ClientDisconnect

    spools = []
    original = formparsers.SpooledTemporaryFile

    def tracked(*args, **kwargs):
        spool = original(*args, **kwargs)
        spools.append(spool)
        return spool

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracked)
    messages = iter(
        [
            {
                "type": "http.request",
                "body": multipart("resume.pdf", ending=False),
                "more_body": True,
            },
            {"type": "http.disconnect"},
        ]
    )

    async def receive():
        return next(messages)

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "POST",
        "scheme": "http",
        "path": "/resumes",
        "raw_path": b"/resumes",
        "root_path": "",
        "query_string": b"",
        "http_version": "1.1",
        "headers": [
            (b"authorization", owner[1]["Authorization"].encode()),
            (b"content-type", b"multipart/form-data; boundary=boundary"),
        ],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    with pytest.raises(ClientDisconnect):
        await auth_app(scope, receive, send)
    assert spools and all(spool.closed for spool in spools)
    assert any(message.get("body") == b"Internal Server Error" for message in sent)
    await assert_empty(db_engine, auth_app.state.resume_storage)
