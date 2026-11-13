"""The generation retry policy, independent of the model provider: retry
only a quota (429) or transient server (5xx) failure, and only a fast one."""

import logging
import time
from collections.abc import Callable

from app.config import get_settings
from app.errors import upstream_status

logger = logging.getLogger(__name__)

# Failures worth one more try: quota (429) and transient server errors.
# Deliberately not 408/504 or client-side timeouts -- for generation those
# mean the call already ran for minutes, and a retry would double that.
RETRYABLE_GENERATION_STATUS = frozenset({429, 500, 502, 503})


def is_retryable_generation_error(exc: BaseException) -> bool:
    return upstream_status(exc) in RETRYABLE_GENERATION_STATUS


def should_retry_generation(exc: BaseException, *, attempt: int, attempts: int, elapsed_s: float) -> bool:
    """Retry only a failure that is retryable (429/5xx) AND fast: within the
    retry window of the attempt starting. A generation that failed after
    minutes of work is not retried -- the caller would wait twice as long
    for what is probably the same outcome."""
    retry = (
        attempt < attempts
        and elapsed_s <= get_settings().generation_retry_window_s
        and is_retryable_generation_error(exc)
    )
    if retry:
        logger.warning(
            "generation.retry",
            extra={"attempt": attempt, "upstream_status": upstream_status(exc), "elapsed_ms": round(elapsed_s * 1000)},
        )
    return retry


def call_with_retry[T](
    call: Callable[[], T],
    *,
    attempts: int,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    attempt = 1
    while True:
        started = clock()
        try:
            return call()
        except Exception as exc:
            if not should_retry_generation(exc, attempt=attempt, attempts=attempts, elapsed_s=clock() - started):
                raise
            time.sleep(get_settings().generation_retry_backoff_s * attempt)
            attempt += 1
