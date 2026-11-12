"""Friendly, user-facing reader errors. Messages never include document content."""

from typing import Literal

ErrorCode = Literal[
    "empty_file",
    "file_too_large",
    "unsupported_file_type",
    "password_protected",
    "unreadable_file",
    "too_many_pages",
    "ai_unavailable",
    "ai_truncated",
    "ai_invalid_response",
    "ai_refused",
]

MESSAGES: dict[str, str] = {
    "empty_file": "This file is empty.",
    "file_too_large": "This file is too large. Please upload a file under {limit_mb} MB.",
    "unsupported_file_type": (
        "We can read PDFs and photos (JPEG, PNG or WebP). This file is a different type."
    ),
    "password_protected": "This PDF is password-protected. Please upload a copy without a password.",
    "unreadable_file": "We couldn't open this file. It may be damaged. Please try downloading it again.",
    "too_many_pages": "This document has more than {limit} pages. Please upload a shorter document.",
    "ai_unavailable": "We couldn't read this document automatically right now. Please try again shortly.",
    "ai_truncated": "This document is too long for us to read in one go. Please upload fewer pages.",
    "ai_invalid_response": "We couldn't make sense of this document. Please try a clearer copy.",
    "ai_refused": "We couldn't process this document. Please check it is a bank statement or payslip.",
}


class ReaderError(Exception):
    """A document could not be read. `code` is stable; `message` is safe to show users."""

    def __init__(self, code: ErrorCode, **fmt: object) -> None:
        self.code = code
        self.message = MESSAGES[code].format(**fmt)
        super().__init__(code)


class ParseFailed(Exception):
    """Internal: the deterministic parser could not read this layout. Triggers the AI path."""
