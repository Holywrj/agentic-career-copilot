"""Extraction attempts on actual PDFs, private storage and Alembic-built PostgreSQL."""

import asyncio
import logging
import threading
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction
from test_resumes import FIELDS, account, error, row_for, upload

from career_copilot.db.models import CareerProfile, Resume
from career_copilot.db.session import create_session_factory
from career_copilot.resume import extraction
from career_copilot.resume.storage import run_io

pytestmark = pytest.mark.database


@pytest_asyncio.fixture
async def owner(auth_client):
    return await account(auth_client)


async def attempt(client, headers, resource):
    return await client.post(f"/resumes/{resource['id']}/extract", headers=headers)


@pytest.mark.parametrize(
    "document,status,expected",
    [
        ("digital", "extracted", "Engineer\n\nExperience café"),
        ("blank", "text_unavailable", None),
        ("malformed", "extraction_failed", None),
        ("encrypted", "extraction_failed", None),
        ("page_limit", "extraction_failed", None),
        ("decoder_limit", "extraction_failed", None),
        ("xobject_limit", "extraction_failed", None),
    ],
)
async def test_document_outcomes_are_committed_200_metadata(
    auth_client,
    auth_app,
    db_engine,
    owner,
    pdf_bytes,
    form_pdf_bytes,
    document,
    status,
    expected,
    caplog,
):
    caplog.set_level(logging.INFO, logger="career_copilot.resume.service")
    binaries = {
        "digital": lambda: pdf_bytes("Engineer", None, "Experience café"),
        "blank": lambda: pdf_bytes(None),
        "malformed": lambda: b"%PDF-1.7\nnot a complete document",
        "encrypted": lambda: pdf_bytes(encrypted=True),
        "page_limit": lambda: pdf_bytes(*(["page"] * 11)),
        "decoder_limit": lambda: pdf_bytes(
            content=b" " * (extraction.MAX_DECODED_STREAM_BYTES + 1), compressed=True
        ),
        "xobject_limit": lambda: form_pdf_bytes(1001),
    }
    binary = binaries[document]()
    resource = (
        await upload(auth_client, owner[1], "private-filename.pdf", binary)
    ).json()
    before = await row_for(db_engine, resource["id"])
    async with create_session_factory(db_engine).begin() as db:
        db.add(
            CareerProfile(
                user_id=owner[0],
                career_summary="User confirmed facts",
                skills=["Python"],
            )
        )
        await db.execute(
            update(Resume)
            .where(Resume.id == before.id)
            .values(structured_content={"legacy": "preserve"})
        )
    result = await attempt(auth_client, owner[1], resource)
    assert result.status_code == 200, result.text
    assert set(result.json()) == FIELDS
    assert result.json()["status"] == status
    assert result.headers["cache-control"] == "no-store"
    row = await row_for(db_engine, resource["id"])
    assert row.parsing_status == status and row.extracted_text == expected
    assert row.structured_content == {"legacy": "preserve"}
    assert row.storage_key == before.storage_key and row.created_at == before.created_at
    assert row.updated_at > before.updated_at
    assert (auth_app.state.resume_storage.root / row.storage_key).read_bytes() == binary
    async with create_session_factory(db_engine).begin() as db:
        profile = await db.get(CareerProfile, owner[0])
        assert (
            profile.career_summary == "User confirmed facts"
            and profile.skills == ["Python"]
            and profile.revision == 1
        )
    for response in [
        await auth_client.get(f"/resumes/{row.id}", headers=owner[1]),
        await auth_client.get("/resumes", headers=owner[1]),
    ]:
        assert (
            "extracted_text" not in response.text
            and "structured_content" not in response.text
            and row.storage_key not in response.text
        )
    assert (
        "private-filename" not in caplog.text
        and str(auth_app.state.resume_storage.root) not in caplog.text
    )
    assert binary.hex() not in caplog.text
    if status == "extraction_failed":
        assert (
            f"resume_id={row.id}" in caplog.text and "operation=extract" in caplog.text
        )
    if document == "xobject_limit":
        assert row.extracted_text is None and "reason=decoder_limit" in caplog.text
        assert "PREFIX" not in caplog.text and "FORM_SOURCE" not in caplog.text


@pytest.mark.parametrize("no_text", [False, True])
async def test_cached_results_never_read_reparse_or_update_timestamp(
    auth_client, auth_app, db_engine, owner, pdf_bytes, monkeypatch, no_text
):
    resource = (
        await upload(
            auth_client, owner[1], content=pdf_bytes(None if no_text else "Text")
        )
    ).json()
    first = await attempt(auth_client, owner[1], resource)
    assert first.status_code == 200
    before = await row_for(db_engine, resource["id"])

    def forbidden(*args):
        pytest.fail("Cached result entered parser/storage")

    monkeypatch.setattr(auth_app.state.resume_storage, "read", forbidden)
    monkeypatch.setattr(extraction, "extract_pdf_text", forbidden)
    second = await attempt(auth_client, owner[1], resource)
    assert second.json() == first.json()
    after = await row_for(db_engine, resource["id"])
    assert (
        after.updated_at == before.updated_at
        and after.extracted_text == before.extracted_text
    )


async def test_failed_attempt_allows_one_explicit_retry_and_no_automatic_retry(
    auth_client, db_engine, owner, pdf_bytes, monkeypatch
):
    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    real_parser = extraction.extract_pdf_text
    invocations = []

    # Simulate a caught document-parser failure, not an infrastructure failure.
    from pypdf import PageObject

    original = PageObject.extract_text

    def fail_page(*args, **kwargs):
        raise RuntimeError("private parser text")

    monkeypatch.setattr(PageObject, "extract_text", fail_page)
    first = await attempt(auth_client, owner[1], resource)
    assert first.status_code == 200 and first.json()["status"] == "extraction_failed"
    assert (await row_for(db_engine, resource["id"])).extracted_text is None
    monkeypatch.setattr(PageObject, "extract_text", original)

    def counted(binary):
        invocations.append(True)
        return real_parser(binary)

    monkeypatch.setattr(extraction, "extract_pdf_text", counted)
    second = await attempt(auth_client, owner[1], resource)
    assert second.status_code == 200 and second.json()["status"] == "extracted"
    assert invocations == [True]


async def test_auth_ownership_uuid_and_no_action_body_consumption(
    auth_client, auth_app, owner, pdf_bytes, monkeypatch
):
    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    _, other = await account(auth_client, "extraction-bob")

    async def forbidden_read(*args):
        pytest.fail("Unauthorized action entered storage")

    original = auth_app.state.resume_storage.read
    monkeypatch.setattr(auth_app.state.resume_storage, "read", forbidden_read)
    error(await attempt(auth_client, {}, resource), 401, "authentication_required")
    for identifier in [resource["id"], str(uuid4())]:
        error(
            await attempt(auth_client, other, {"id": identifier}),
            404,
            "resume_not_found",
        )
    error(
        await attempt(auth_client, owner[1], {"id": "private-invalid-uuid"}),
        422,
        "invalid_request",
    )
    monkeypatch.setattr(auth_app.state.resume_storage, "read", original)

    async def body():
        pytest.fail("No-input extraction action consumed the request body")
        yield b"unexpected body"

    response = await auth_client.post(
        f"/resumes/{resource['id']}/extract", headers=owner[1], content=body()
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "status,existing_text,expected",
    [
        ("uploaded", None, 200),
        ("extraction_failed", None, 200),
        ("extracted", "Existing source text", 200),
        ("text_unavailable", None, 200),
        ("legacy-ready", "Existing source text", 409),
        ("legacy-unparsed", None, 409),
        ("uploaded", "", 409),
        ("extraction_failed", "Existing source text", 409),
        ("extracted", None, 409),
        ("extracted", "   ", 409),
        ("text_unavailable", "", 409),
    ],
)
async def test_legacy_rows_fail_closed_without_overwrite_or_metadata_backfill(
    auth_client, db_engine, owner, pdf_bytes, status, existing_text, expected
):
    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    async with create_session_factory(db_engine).begin() as db:
        await db.execute(
            update(Resume)
            .where(Resume.id == resource["id"])
            .values(
                parsing_status=status,
                extracted_text=existing_text,
                media_type=None,
                size_bytes=None,
            )
        )
    before = await row_for(db_engine, resource["id"])
    response = await attempt(auth_client, owner[1], resource)
    assert response.status_code == expected, response.text
    after = await row_for(db_engine, resource["id"])
    assert after.media_type is after.size_bytes is None
    if expected == 409:
        error(response, 409, "resume_extraction_conflict")
    if expected == 409 or status in {"extracted", "text_unavailable"}:
        assert after.parsing_status == before.parsing_status
        assert after.extracted_text == before.extracted_text
        assert after.updated_at == before.updated_at


@pytest.mark.parametrize(
    "failure", ["missing", "mismatch", "permissions", "unsafe_key"]
)
async def test_storage_failure_does_not_change_lifecycle_or_enter_parser(
    auth_client, auth_app, db_engine, owner, pdf_bytes, monkeypatch, failure, caplog
):
    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    before = await row_for(db_engine, resource["id"])
    storage = auth_app.state.resume_storage
    if failure == "missing":
        (storage.root / before.storage_key).unlink()
    elif failure == "mismatch":
        async with create_session_factory(db_engine).begin() as db:
            await db.execute(
                update(Resume).where(Resume.id == before.id).values(size_bytes=1)
            )
    elif failure == "permissions":
        storage.root.chmod(0o755)
    else:
        async with create_session_factory(db_engine).begin() as db:
            await db.execute(
                update(Resume)
                .where(Resume.id == before.id)
                .values(storage_key="../../private")
            )
    before = await row_for(db_engine, resource["id"])

    def forbidden(*args):
        pytest.fail("Inconsistent storage entered parser")

    monkeypatch.setattr(extraction, "extract_pdf_text", forbidden)
    response = await attempt(auth_client, owner[1], resource)
    error(response, 503, "resume_storage_unavailable")
    after = await row_for(db_engine, resource["id"])
    assert (
        after.parsing_status == before.parsing_status and after.extracted_text is None
    )
    assert after.updated_at == before.updated_at
    assert str(storage.root) not in response.text + caplog.text
    assert before.storage_key not in response.text + caplog.text


@pytest.mark.parametrize("commit_failure", [False, True])
async def test_confirmed_update_or_commit_rejection_rolls_back_without_binary_change(
    auth_client, auth_app, db_engine, owner, pdf_bytes, commit_failure, caplog
):
    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    before = await row_for(db_engine, resource["id"])
    async with db_engine.begin() as connection:
        if commit_failure:
            await connection.execute(
                text(
                    "CREATE FUNCTION reject_extraction() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
                    "'private SQL /private/path' USING ERRCODE = '23514'; END $$"
                )
            )
            await connection.execute(
                text(
                    "CREATE CONSTRAINT TRIGGER reject_extraction_commit "
                    "AFTER UPDATE ON resumes DEFERRABLE INITIALLY DEFERRED "
                    "FOR EACH ROW EXECUTE FUNCTION reject_extraction()"
                )
            )
        else:
            await connection.execute(
                text(
                    "ALTER TABLE resumes ADD CONSTRAINT reject_extraction "
                    "CHECK (parsing_status <> 'extracted')"
                )
            )
    error(await attempt(auth_client, owner[1], resource), 503, "resume_unavailable")
    after = await row_for(db_engine, resource["id"])
    assert after.parsing_status == "uploaded" and after.extracted_text is None
    assert after.updated_at == before.updated_at
    assert (auth_app.state.resume_storage.root / after.storage_key).exists()
    assert db_engine.pool.checkedout() == 0
    assert "db_commit_uncertain" not in caplog.text


@pytest.mark.parametrize("committed", [False, True])
async def test_uncertain_commit_has_safe_503_without_retry_or_binary_compensation(
    auth_client, auth_app, db_engine, owner, pdf_bytes, monkeypatch, caplog, committed
):
    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    original_execute, original_exit = (
        AsyncSession.execute,
        AsyncSessionTransaction.__aexit__,
    )

    async def marked(db, statement, *args, **kwargs):
        result = await original_execute(db, statement, *args, **kwargs)
        if str(statement).startswith("UPDATE resumes"):
            db.info["extraction_write"] = True
        return result

    async def uncertain(transaction, kind, value, traceback):
        if kind is None and transaction.session.info.pop("extraction_write", False):
            if committed:
                await original_exit(transaction, kind, value, traceback)
            else:
                await transaction.rollback()
            raise OperationalError(
                "private SQL COMMIT", {}, Exception("private document /private/path")
            )
        return await original_exit(transaction, kind, value, traceback)

    monkeypatch.setattr(AsyncSession, "execute", marked)
    monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", uncertain)
    error(await attempt(auth_client, owner[1], resource), 503, "resume_unavailable")
    monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", original_exit)
    row = await row_for(db_engine, resource["id"])
    assert row.parsing_status == ("extracted" if committed else "uploaded")
    assert (row.extracted_text is not None) == committed
    assert (auth_app.state.resume_storage.root / row.storage_key).exists()
    assert (
        "reason=db_commit_uncertain" in caplog.text
        and "private document" not in caplog.text
    )
    assert db_engine.pool.checkedout() == 0


@pytest.mark.parametrize("winner", ["extracted", "text_unavailable"])
async def test_duplicate_parsing_never_overwrites_success_with_late_failure(
    auth_client, auth_app, db_engine, owner, pdf_bytes, monkeypatch, winner
):
    resource = (
        await upload(
            auth_client,
            owner[1],
            content=pdf_bytes(None if winner == "text_unavailable" else "Source"),
        )
    ).json()
    started = [threading.Event(), threading.Event()]
    releases = [threading.Event(), threading.Event()]
    count, lock = 0, threading.Lock()
    real = extraction.extract_pdf_text

    def concurrent(binary):
        nonlocal count
        with lock:
            index = count
            count += 1
        started[index].set()
        assert releases[index].wait(5)
        return real(binary if index == 0 else b"malformed")

    monkeypatch.setattr(extraction, "extract_pdf_text", concurrent)
    before_tasks = asyncio.all_tasks()
    tasks = []
    try:
        tasks.append(asyncio.create_task(attempt(auth_client, owner[1], resource)))
        assert await asyncio.wait_for(run_io(started[0].wait, 5), 6)
        tasks.append(asyncio.create_task(attempt(auth_client, owner[1], resource)))
        assert await asyncio.wait_for(run_io(started[1].wait, 5), 6)
        assert db_engine.pool.checkedout() == 0
        assert auth_app.state.resume_extraction_limiter.borrowed_tokens == 2
        releases[0].set()
        first = await tasks[0]
        assert first.status_code == 200 and first.json()["status"] == winner
        releases[1].set()
        second = await tasks[1]
        assert second.json() == first.json()
    finally:
        for release in releases:
            release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert count == 2 and not (asyncio.all_tasks() - before_tasks)
    assert db_engine.pool.checkedout() == 0
    assert auth_app.state.resume_extraction_limiter.borrowed_tokens == 0


@pytest.mark.parametrize("race", ["delete", "replace_key", "legacy_text"])
async def test_final_recheck_discards_result_after_concurrent_resource_change(
    auth_client, auth_app, db_engine, owner, pdf_bytes, monkeypatch, race
):
    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    before = await row_for(db_engine, resource["id"])
    started, release = threading.Event(), threading.Event()
    real = extraction.extract_pdf_text

    def blocked(binary):
        started.set()
        assert release.wait(5)
        return real(binary)

    monkeypatch.setattr(extraction, "extract_pdf_text", blocked)
    task = asyncio.create_task(attempt(auth_client, owner[1], resource))
    try:
        assert await asyncio.wait_for(run_io(started.wait, 5), 6)
        assert db_engine.pool.checkedout() == 0
        # Authenticated DB requests remain runnable while parser is blocked.
        assert (await auth_client.get("/resumes", headers=owner[1])).status_code == 200
        if race == "delete":
            assert (
                await auth_client.delete(f"/resumes/{before.id}", headers=owner[1])
            ).status_code == 204
        else:
            values = (
                {"storage_key": uuid4().hex}
                if race == "replace_key"
                else {
                    "extracted_text": "Preserve legacy text",
                    "parsing_status": "legacy-ready",
                }
            )
            async with create_session_factory(db_engine).begin() as db:
                await db.execute(
                    update(Resume).where(Resume.id == before.id).values(**values)
                )
        release.set()
        error(
            await task,
            404 if race == "delete" else 409,
            "resume_not_found" if race == "delete" else "resume_extraction_conflict",
        )
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    row = await row_for(db_engine, resource["id"])
    if race == "delete":
        assert (
            row is None
            and not (auth_app.state.resume_storage.root / before.storage_key).exists()
        )
    elif race == "legacy_text":
        assert row.extracted_text == "Preserve legacy text"
    else:
        assert row.extracted_text is None
    assert db_engine.pool.checkedout() == 0


@pytest.mark.parametrize("cancellations", [1, 2])
async def test_parser_cancellation_discards_result_with_no_db_write_or_leak(
    auth_client, auth_app, db_engine, owner, pdf_bytes, monkeypatch, cancellations
):
    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    before = await row_for(db_engine, resource["id"])
    started, release = threading.Event(), threading.Event()
    real = extraction.extract_pdf_text

    def blocked(binary):
        started.set()
        assert release.wait(5)
        return real(binary)

    monkeypatch.setattr(extraction, "extract_pdf_text", blocked)
    before_tasks = asyncio.all_tasks()
    task = asyncio.create_task(attempt(auth_client, owner[1], resource))
    try:
        assert await asyncio.wait_for(run_io(started.wait, 5), 6)
        for _ in range(cancellations):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert db_engine.pool.checkedout() == 0
            assert auth_app.state.resume_extraction_limiter.borrowed_tokens == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    after = await row_for(db_engine, resource["id"])
    assert after.parsing_status == "uploaded" and after.extracted_text is None
    assert after.updated_at == before.updated_at
    assert db_engine.pool.checkedout() == 0
    assert auth_app.state.resume_extraction_limiter.borrowed_tokens == 0
    assert not (asyncio.all_tasks() - before_tasks)


@pytest.mark.parametrize("phase", ["update", "commit"])
@pytest.mark.parametrize("cancellations", [1, 2])
async def test_final_transaction_finishes_before_cancelled_request_session_closes(
    auth_client,
    auth_app,
    db_engine,
    owner,
    pdf_bytes,
    monkeypatch,
    phase,
    cancellations,
):
    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    ready, release = asyncio.Event(), asyncio.Event()
    original_execute, original_exit, original_close = (
        AsyncSession.execute,
        AsyncSessionTransaction.__aexit__,
        AsyncSession.close,
    )
    closed_active = []

    async def blocked_update(db, statement, *args, **kwargs):
        result = await original_execute(db, statement, *args, **kwargs)
        if str(statement).startswith("UPDATE resumes"):
            db.info["extraction_write"] = True
            if phase == "update":
                ready.set()
                await release.wait()
        return result

    async def blocked_exit(transaction, kind, value, traceback):
        if (
            kind is None
            and transaction.session.info.pop("extraction_write", False)
            and phase == "commit"
        ):
            ready.set()
            await release.wait()
        return await original_exit(transaction, kind, value, traceback)

    async def checked_close(db):
        closed_active.append(db.in_transaction())
        await original_close(db)

    monkeypatch.setattr(AsyncSession, "execute", blocked_update)
    monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", blocked_exit)
    monkeypatch.setattr(AsyncSession, "close", checked_close)
    before_tasks = asyncio.all_tasks()
    task = asyncio.create_task(attempt(auth_client, owner[1], resource))
    try:
        await asyncio.wait_for(ready.wait(), 5)
        assert db_engine.pool.checkedout() == 1
        for _ in range(cancellations):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and closed_active == []
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    row = await row_for(db_engine, resource["id"])
    assert row.parsing_status == "extracted" and row.extracted_text == "Resume Engineer"
    assert closed_active and not any(closed_active)
    assert (auth_app.state.resume_storage.root / row.storage_key).exists()
    assert db_engine.pool.checkedout() == 0
    assert not (asyncio.all_tasks() - before_tasks)


async def test_storage_delete_race_before_open_returns_not_found(
    auth_client, auth_app, db_engine, owner, pdf_bytes, monkeypatch
):
    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    original = auth_app.state.resume_storage.read

    async def delete_before_read(key, limit):
        assert (
            await auth_client.delete(f"/resumes/{resource['id']}", headers=owner[1])
        ).status_code == 204
        return await original(key, limit)

    monkeypatch.setattr(auth_app.state.resume_storage, "read", delete_before_read)
    error(await attempt(auth_client, owner[1], resource), 404, "resume_not_found")
    assert await row_for(db_engine, resource["id"]) is None


@pytest.mark.parametrize("policy", ["raw", "final", "deadline", "invalid", "exception"])
async def test_policy_and_parser_failures_commit_safe_200_without_partial_text(
    auth_client, db_engine, owner, pdf_bytes, monkeypatch, caplog, policy
):
    from pypdf import PageObject

    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    before = await row_for(db_engine, resource["id"])
    caplog.set_level(logging.INFO, logger="career_copilot.resume.service")
    settings = {
        "raw": ("MAX_RAW_TEXT_BYTES", 1, "text_limit"),
        "final": ("MAX_TEXT_BYTES", 1, "text_limit"),
        "deadline": ("PARSER_BUDGET_SECONDS", 0, "time_budget"),
    }
    if policy in settings:
        setting, value, reason = settings[policy]
        monkeypatch.setattr(extraction, setting, value)
    else:
        reason = "invalid_text" if policy == "invalid" else "parser_error"

        def broken(*args, **kwargs):
            if policy == "invalid":
                return "private source\x00"
            raise RuntimeError("private source /private/path")

        monkeypatch.setattr(PageObject, "extract_text", broken)
    response = await attempt(auth_client, owner[1], resource)
    assert response.status_code == 200
    assert set(response.json()) == FIELDS
    assert response.json()["status"] == "extraction_failed"
    after = await row_for(db_engine, resource["id"])
    assert after.extracted_text is None and after.parsing_status == "extraction_failed"
    assert after.updated_at > before.updated_at
    assert f"reason={reason}" in caplog.text
    assert "private source" not in response.text + caplog.text
    assert "/private/path" not in response.text + caplog.text


async def test_pipeline_limit_queues_before_read_and_releases_cancelled_waiter(
    auth_client, auth_app, db_engine, owner, pdf_bytes, monkeypatch
):
    resource = (await upload(auth_client, owner[1], content=pdf_bytes())).json()
    first_started, started, release = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )
    lock, count = threading.Lock(), 0
    read_calls = []
    original_read = auth_app.state.resume_storage.read
    original_parse = extraction.extract_pdf_text
    limiter = auth_app.state.resume_extraction_limiter

    async def checked_read(key, limit):
        assert db_engine.pool.checkedout() == 0
        read_calls.append(key)
        return await original_read(key, limit)

    def blocked(binary):
        nonlocal count
        with lock:
            count += 1
            if count == 1:
                first_started.set()
            if count == 2:
                started.set()
        assert release.wait(5)
        return original_parse(binary)

    async def queued():
        while limiter.statistics().tasks_waiting != 1:
            await asyncio.sleep(0)

    monkeypatch.setattr(auth_app.state.resume_storage, "read", checked_read)
    monkeypatch.setattr(extraction, "extract_pdf_text", blocked)
    before_tasks = asyncio.all_tasks()
    tasks = []
    try:
        tasks.append(asyncio.create_task(attempt(auth_client, owner[1], resource)))
        assert await asyncio.wait_for(run_io(first_started.wait, 5), 6)
        tasks.append(asyncio.create_task(attempt(auth_client, owner[1], resource)))
        assert await asyncio.wait_for(run_io(started.wait, 5), 6)
        tasks.append(asyncio.create_task(attempt(auth_client, owner[1], resource)))
        await asyncio.wait_for(queued(), 5)
        assert len(read_calls) == 2 and limiter.borrowed_tokens == 2
        assert db_engine.pool.checkedout() == 0
        tasks[2].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[2]
        assert limiter.statistics().tasks_waiting == 0
        assert limiter.borrowed_tokens == 2 and len(read_calls) == 2
        release.set()
        responses = await asyncio.gather(*tasks[:2])
        assert all(response.status_code == 200 for response in responses)
        assert responses[0].json() == responses[1].json()
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert limiter.borrowed_tokens == 0 and db_engine.pool.checkedout() == 0
    assert not (asyncio.all_tasks() - before_tasks)
