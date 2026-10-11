"""Real tiny PDFs and source-faithful policy; no network/font/renderer dependency."""

import asyncio
import logging
import threading

import anyio
import pytest
from pypdf import PdfReader, get_configuration

from career_copilot.resume import extraction
from career_copilot.resume.extraction import (
    MAX_RAW_TEXT_BYTES,
    MAX_TEXT_BYTES,
    PolicyFailure,
    extract_pdf_text,
    normalize_page,
)
from career_copilot.resume.storage import run_io


def test_real_single_multi_page_and_unicode_pdf(pdf_bytes):
    result = extract_pdf_text(pdf_bytes("Resume café"))
    assert result.status == "extracted" and result.text == "Resume café"
    result = extract_pdf_text(pdf_bytes("Engineer", None, "Experience"))
    assert result.text == "Engineer\n\nExperience"
    assert result.reason is None
    assert "Engineer" not in repr(result)


def test_normalization_preserves_unicode_indentation_and_semantics():
    text = "\r\n \t\r\n  王小明 e\u0301 ﬀ\t \r\n\r\n \t\r\n\rWork\t history \r\n\t"
    assert normalize_page(text) == "  王小明 e\u0301 ﬀ\n\nWork\t history"
    assert normalize_page("\r\n \t\n") == ""


@pytest.mark.parametrize("text", ["text\x00", "text\ud800", "\udfff"])
def test_unpersistable_text_is_rejected(text):
    with pytest.raises(PolicyFailure) as error:
        normalize_page(text)
    assert error.value.reason == "invalid_text"


@pytest.mark.parametrize("kind", ["blank", "image", "whitespace"])
def test_no_text_is_successful_parse_not_corruption(pdf_bytes, kind):
    if kind == "image":
        binary = pdf_bytes(
            content=b"q BI /W 1 /H 1 /BPC 8 /CS /DeviceGray ID \x00 EI Q"
        )
    else:
        binary = pdf_bytes(None if kind == "blank" else "   ")
    result = extract_pdf_text(binary)
    assert result.status == "text_unavailable"
    assert result.text is result.reason is None


@pytest.mark.parametrize(
    "binary", [b"", b"%PDF-1.7\nnot a complete PDF\n%%EOF", b"not PDF"]
)
def test_malformed_real_input_is_a_safe_outcome(binary):
    result = extract_pdf_text(binary)
    assert result.status == "extraction_failed" and result.text is None
    assert result.reason == "malformed_pdf"


def test_encrypted_pdf_never_calls_application_decrypt(pdf_bytes, monkeypatch):
    binary = pdf_bytes(encrypted=True)

    def forbidden(*args):
        pytest.fail("Extraction must not call decrypt")

    monkeypatch.setattr(PdfReader, "decrypt", forbidden)
    result = extract_pdf_text(binary)
    assert result.status == "extraction_failed"
    assert result.reason == "encrypted" and result.text is None


def test_page_limit_with_real_pdf(pdf_bytes):
    assert extract_pdf_text(pdf_bytes(*(["Page"] * 10))).status == "extracted"
    result = extract_pdf_text(pdf_bytes(*(["Page"] * 11)))
    assert result.status == "extraction_failed" and result.text is None
    assert result.reason == "page_limit"


def test_compressed_stream_amplification_is_bounded(pdf_bytes):
    binary = pdf_bytes(
        content=b" " * (extraction.MAX_DECODED_STREAM_BYTES + 1), compressed=True
    )
    assert len(binary) < 10000
    result = extract_pdf_text(binary)
    assert result.status == "extraction_failed" and result.text is None
    assert result.reason == "decoder_limit"


@pytest.mark.parametrize(
    "setting,limit,reason",
    [
        ("maximum_declared_stream_length", 10, "malformed_pdf"),
        ("stream_filters_maximum_length", 0, "decoder_limit"),
        ("stream_decoding_work_maximum_length", 1, "decoder_limit"),
        ("page_tree_maximum_entries", 1, "decoder_limit"),
        ("page_tree_maximum_depth", 0, "decoder_limit"),
    ],
)
def test_configuration_limits_are_effective_and_scoped(
    pdf_bytes, monkeypatch, setting, limit, reason
):
    baseline = get_configuration()
    monkeypatch.setattr(
        extraction,
        "PARSER_CONFIGURATION",
        extraction.PARSER_CONFIGURATION.with_overwrites(**{setting: limit}),
    )
    result = extract_pdf_text(pdf_bytes("One", "Two", compressed=True))
    assert result.status == "extraction_failed" and result.reason == reason
    assert get_configuration() is baseline
    assert extraction.PARSER_CONFIGURATION.jbig2dec_binary is None


@pytest.mark.parametrize("limit", ["raw", "final"])
def test_real_utf8_text_exact_limit_and_one_byte_over(pdf_bytes, monkeypatch, limit):
    monkeypatch.setattr(
        extraction, "MAX_RAW_TEXT_BYTES" if limit == "raw" else "MAX_TEXT_BYTES", 4
    )
    assert extract_pdf_text(pdf_bytes("éé")).text == "éé"
    result = extract_pdf_text(pdf_bytes("ééx"))
    assert result.status == "extraction_failed" and result.reason == "text_limit"
    assert result.text is None


def test_production_text_bounds_do_not_truncate(pdf_bytes):
    assert (
        extract_pdf_text(pdf_bytes("x" * MAX_TEXT_BYTES)).text == "x" * MAX_TEXT_BYTES
    )
    result = extract_pdf_text(pdf_bytes("x" * (MAX_TEXT_BYTES + 1)))
    assert result.status == "extraction_failed" and result.text is None
    # Whitespace would be tiny after normalization, but the raw budget still applies.
    result = extract_pdf_text(pdf_bytes("x" + " " * MAX_RAW_TEXT_BYTES))
    assert result.status == "extraction_failed" and result.reason == "text_limit"


@pytest.mark.parametrize(
    "setting,limit,status",
    [
        ("MAX_RAW_TEXT_BYTES", 4, "extracted"),
        ("MAX_RAW_TEXT_BYTES", 3, "extraction_failed"),
        ("MAX_TEXT_BYTES", 6, "extracted"),
        ("MAX_TEXT_BYTES", 5, "extraction_failed"),
    ],
)
def test_utf8_budget_accumulates_pages_and_counts_final_separators(
    pdf_bytes, monkeypatch, setting, limit, status
):
    monkeypatch.setattr(extraction, setting, limit)
    result = extract_pdf_text(pdf_bytes("é", None, "é"))
    assert result.status == status
    assert result.text == ("é\n\né" if status == "extracted" else None)


@pytest.mark.parametrize("times", [[0, 0, 31], [0, 0, 0, 0, 0, 31]])
def test_soft_deadline_before_page_work_and_before_return(
    pdf_bytes, monkeypatch, times
):
    binary = pdf_bytes()
    clock = iter(times)
    monkeypatch.setattr(extraction.time, "monotonic", lambda: next(clock))
    result = extract_pdf_text(binary)
    assert result.status == "extraction_failed" and result.reason == "time_budget"
    assert result.text is None


def test_raw_parser_logging_suppressed_without_warning_capture_or_failure(
    pdf_bytes, monkeypatch, caplog
):
    from pypdf import PageObject

    original = PageObject.extract_text
    extraction.suppress_parser_logging()

    def warned(page, *args, **kwargs):
        logging.getLogger("pypdf._page").warning(
            "private PDF bytes %s", "/private/path Resume text"
        )
        return original(page, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", warned)
    assert extract_pdf_text(pdf_bytes()).status == "extracted"
    assert "private" not in caplog.text and "Resume text" not in caplog.text


def test_unexpected_parser_exception_has_no_raw_details(pdf_bytes, monkeypatch, caplog):
    from pypdf import PageObject

    def broken(*args, **kwargs):
        raise RuntimeError("private PDF content /private/path")

    monkeypatch.setattr(PageObject, "extract_text", broken)
    result = extract_pdf_text(pdf_bytes())
    assert result.status == "extraction_failed" and result.reason == "parser_error"
    assert "private" not in repr(result) + caplog.text


async def test_direct_asyncio_cancel_can_abandon_anyio_nonabandon_worker():
    """Direct task cancellation bypasses AnyIO CancelScope shielding."""
    started, release, ended = threading.Event(), threading.Event(), threading.Event()
    limiter = anyio.CapacityLimiter(2)

    def worker():
        started.set()
        try:
            assert release.wait(5)
        finally:
            ended.set()

    task = asyncio.create_task(
        anyio.to_thread.run_sync(worker, abandon_on_cancel=False, limiter=limiter)
    )
    try:
        assert await asyncio.wait_for(run_io(started.wait, 5), 6)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not ended.is_set()
        assert limiter.borrowed_tokens == 0
    finally:
        release.set()
        assert await asyncio.wait_for(run_io(ended.wait, 5), 6)
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("cancellations", [1, 2])
async def test_owned_parser_waits_before_releasing_pipeline_and_propagating_cancel(
    monkeypatch, cancellations
):
    started, release = threading.Event(), threading.Event()
    limiter = anyio.CapacityLimiter(2)

    def blocked(binary):
        started.set()
        assert release.wait(5)
        return extraction.ExtractionResult("extracted", "owned result")

    monkeypatch.setattr(extraction, "extract_pdf_text", blocked)

    async def work():
        async with limiter:
            await extraction.parse_binary(b"pdf")
            pytest.fail("Cancelled parsed result must be discarded")

    before = asyncio.all_tasks()
    task = asyncio.create_task(work())
    try:
        assert await asyncio.wait_for(run_io(started.wait, 5), 6)
        for _ in range(cancellations):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and limiter.borrowed_tokens == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert limiter.borrowed_tokens == 0
    assert not (asyncio.all_tasks() - before)


async def test_anyio_scope_cancel_waits_for_worker_then_discards_result(monkeypatch):
    started, release = threading.Event(), threading.Event()
    limiter = anyio.CapacityLimiter(2)
    scopes, persisted = [], []

    def blocked(binary):
        started.set()
        assert release.wait(5)
        return extraction.ExtractionResult("extracted", "discard source")

    async def work():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            async with limiter:
                await extraction.parse_binary(b"pdf")
                persisted.append(True)

    monkeypatch.setattr(extraction, "extract_pdf_text", blocked)
    before = asyncio.all_tasks()
    task = asyncio.create_task(work())
    try:
        assert await asyncio.wait_for(run_io(started.wait, 5), 6)
        scopes[0].cancel()
        await asyncio.sleep(0)
        assert not task.done() and limiter.borrowed_tokens == 1
        release.set()
        await asyncio.wait_for(task, 5)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert scopes[0].cancelled_caught and persisted == []
    assert limiter.borrowed_tokens == 0 and not (asyncio.all_tasks() - before)
