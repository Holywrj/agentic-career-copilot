"""Best-effort PDF source text with scoped policy and owned thread execution."""

import logging
import time
from dataclasses import dataclass, field
from io import BytesIO
from typing import Literal

import anyio
from pypdf import Configuration, PdfReader, apply_configuration
from pypdf.errors import DependencyError, LimitReachedError, PdfReadError

from career_copilot.resume.storage import complete
from career_copilot.resume.uploads import MAX_RESUME_BYTES

MAX_PAGES = 10
MAX_RAW_TEXT_BYTES = 512 * 1024
MAX_TEXT_BYTES = 256 * 1024
PARSER_BUDGET_SECONDS = 30
EXTRACTION_CONCURRENCY = 2
# Short resumes need modest content streams, even with font/layout overhead.
# These are decoder/work limits, not a whole-document or process-memory bound.
MAX_DECODED_STREAM_BYTES = 4 * 1024 * 1024
MAX_STREAM_WORK_BYTES = 16 * 1024 * 1024
MAX_STREAM_FILTERS = 8
MAX_PAGE_TREE_ENTRIES = 1000
MAX_PAGE_TREE_DEPTH = 20
MAX_XOBJECT_OPERATIONS = 1000
PARSER_CONFIGURATION = Configuration(
    maximum_declared_stream_length=MAX_RESUME_BYTES,
    array_based_stream_maximum_output_length=MAX_DECODED_STREAM_BYTES,
    jbig2_maximum_output_length=MAX_DECODED_STREAM_BYTES,
    lzw_maximum_output_length=MAX_DECODED_STREAM_BYTES,
    run_length_maximum_output_length=MAX_DECODED_STREAM_BYTES,
    zlib_maximum_output_length=MAX_DECODED_STREAM_BYTES,
    brotli_maximum_output_length=MAX_DECODED_STREAM_BYTES,
    image_maximum_buffer_size=MAX_DECODED_STREAM_BYTES,
    stream_filters_maximum_length=MAX_STREAM_FILTERS,
    stream_decoding_work_maximum_length=MAX_STREAM_WORK_BYTES,
    page_tree_maximum_entries=MAX_PAGE_TREE_ENTRIES,
    page_tree_maximum_depth=MAX_PAGE_TREE_DEPTH,
    xform_maximum_invocations_per_extraction=MAX_XOBJECT_OPERATIONS,
    jbig2dec_binary=None,
)
FailureReason = Literal[
    "encrypted",
    "malformed_pdf",
    "page_limit",
    "decoder_limit",
    "text_limit",
    "time_budget",
    "invalid_text",
    "parser_error",
]


@dataclass(frozen=True)
class ExtractionResult:
    status: Literal["extracted", "text_unavailable", "extraction_failed"]
    text: str | None = field(default=None, repr=False)
    reason: FailureReason | None = None


class PolicyFailure(Exception):
    def __init__(self, reason: FailureReason) -> None:
        self.reason = reason


def suppress_parser_logging() -> None:
    """Configure once at application startup, never during an extraction."""
    parser_logger = logging.getLogger("pypdf")
    parser_logger.handlers = [logging.NullHandler()]
    parser_logger.propagate = False


def normalize_page(text: str) -> str:
    if "\x00" in text or any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise PolicyFailure("invalid_text")
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.rstrip(" \t")
        if not line.strip():
            if lines and lines[-1] != "":
                lines.append("")
        else:
            lines.append(line)
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def extract_pdf_text(binary: bytes) -> ExtractionResult:
    deadline = time.monotonic() + PARSER_BUDGET_SECONDS
    xobject_operations = 0
    policy_exceeded = False

    def visit_operand(operator, _operands, _cm, _tm) -> None:
        nonlocal xobject_operations, policy_exceeded
        if operator == b"Do":
            xobject_operations += 1
            if xobject_operations > MAX_XOBJECT_OPERATIONS:
                # Nested Form extraction may catch callback exceptions, and the
                # library's Form limit silently skips content. Keep a flag instead.
                policy_exceeded = True

    def checkpoint() -> None:
        if time.monotonic() >= deadline:
            raise PolicyFailure("time_budget")

    try:
        checkpoint()
        with apply_configuration(PARSER_CONFIGURATION), BytesIO(binary) as source:
            with PdfReader(source, strict=True) as reader:
                # No password input or application decrypt call. Reject even
                # documents which the library can open using an empty password.
                if reader.is_encrypted:
                    raise PolicyFailure("encrypted")
                checkpoint()
                count = len(reader.pages)
                if count > MAX_PAGES:
                    raise PolicyFailure("page_limit")
                raw_size = 0
                pages = []
                for page in reader.pages:
                    checkpoint()
                    raw = page.extract_text(visitor_operand_before=visit_operand)
                    if policy_exceeded:
                        raise PolicyFailure("decoder_limit")
                    checkpoint()
                    # Encoding is strict: invalid Unicode cannot reach PostgreSQL.
                    raw_size += len(raw.encode("utf-8"))
                    if raw_size > MAX_RAW_TEXT_BYTES:
                        raise PolicyFailure("text_limit")
                    normalized = normalize_page(raw)
                    if normalized:
                        pages.append(normalized)
                text = "\n\n".join(pages)
                if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
                    raise PolicyFailure("text_limit")
                checkpoint()
                return (
                    ExtractionResult("extracted", text)
                    if text.strip()
                    else ExtractionResult("text_unavailable")
                )
    except PolicyFailure as error:
        return ExtractionResult("extraction_failed", reason=error.reason)
    except LimitReachedError:
        return ExtractionResult("extraction_failed", reason="decoder_limit")
    except PdfReadError:
        return ExtractionResult("extraction_failed", reason="malformed_pdf")
    except DependencyError:
        # The base package may encounter unsupported encryption at construction.
        return ExtractionResult("extraction_failed", reason="encrypted")
    except UnicodeError:
        return ExtractionResult("extraction_failed", reason="invalid_text")
    except Exception:
        # Only the third-party document-processing boundary catches arbitrary
        # parser exceptions. Never log their document-bearing messages/tracebacks.
        return ExtractionResult("extraction_failed", reason="parser_error")


async def parse_binary(binary: bytes) -> ExtractionResult:
    # AnyIO's non-abandon policy shields CancelScope cancellation, but direct
    # asyncio.Task.cancel() can still abandon its await/release the token early.
    # complete owns that await through repeated task cancellation. The service's
    # dedicated pipeline limiter remains held until the worker really finishes.
    result = await complete(
        anyio.to_thread.run_sync(extract_pdf_text, binary, abandon_on_cancel=False)
    )
    await anyio.lowlevel.checkpoint()
    return result
