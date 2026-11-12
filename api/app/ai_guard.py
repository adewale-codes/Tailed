"""Cost safety net: every Claude call asks this guard first.

The API installs a Postgres-backed guard that enforces AI_DAILY_CALL_CAP across all checks.
Without one installed (unit tests, scripts), calls are allowed.
"""

from collections.abc import Callable

_guard: Callable[[], bool] = lambda: True  # noqa: E731


def set_guard(guard: Callable[[], bool]) -> None:
    global _guard
    _guard = guard


def ai_call_allowed() -> bool:
    """Reserve one AI call from today's budget. False means the cap is reached."""
    return _guard()
