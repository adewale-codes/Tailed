"""API errors: a stable code, an HTTP status and a plain-English message."""

from __future__ import annotations

MESSAGES: dict[str, tuple[int, str]] = {
    "consent_required": (400, "Please confirm you have the tenant's permission to check these "
                              "documents."),
    "no_files": (400, "Please add at least one bank statement or payslip."),
    "too_many_files": (400, "Please upload no more than {max_files} files per check."),
    "invalid_rent": (400, "Please enter the monthly rent as a number, for example 1100."),
    "file_too_large": (413, "Each file must be under {max_file_mb} MB."),
    "upload_too_large": (413, "This upload is too large. Please send no more than {max_files} files "
                              "of up to {max_file_mb} MB each."),
    "unsupported_file_type": (415, "We can read PDFs and photos (JPG or PNG). One of the files is a "
                                   "different type."),
    "rate_limited": (429, "You've reached today's limit of {checks_per_ip_per_day} free checks. "
                          "Please try again tomorrow."),
    "check_not_found": (404, "We couldn't find that check. Please check the link."),
    "check_expired": (410, "This report has been deleted. We delete every report and its "
                           "documents 24 hours after the check, to protect the tenant's privacy."),
    "page_not_found": (404, "We couldn't find that page."),
    "invalid_feedback": (400, "Please choose one of the options shown."),
    "invalid_email": (400, "That email address doesn't look right."),
    "invalid_request": (400, "Something in the request wasn't right. Please try again."),
    "not_found": (404, "There's nothing here."),
    "method_not_allowed": (405, "That isn't supported here."),
    "internal_error": (500, "Something went wrong on our side. Please try again."),
}


class ApiError(Exception):
    def __init__(self, code: str, **fmt: object) -> None:
        self.code = code
        self.status, template = MESSAGES[code]
        self.message = template.format(**fmt)
        super().__init__(code)
