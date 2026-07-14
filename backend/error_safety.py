"""Sanitized error summaries safe for logs and API diagnostics."""
from collections import Counter


def safe_exception_summary(error: BaseException) -> str:
    """Return exception type and optional HTTP status, never message text."""
    summary = type(error).__name__
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        summary += f" (HTTP {status_code})"
    return summary


def validation_error_summary(error: BaseException) -> str:
    """Summarize validation error codes without field inputs or values."""
    try:
        entries = error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )
    except (AttributeError, TypeError):
        return safe_exception_summary(error)

    codes = Counter(str(entry.get("type") or "validation_error") for entry in entries)
    details = ", ".join(
        f"{code}={count}" for code, count in sorted(codes.items())
    )
    return f"{len(entries)} issue(s)" + (f" [{details}]" if details else "")
