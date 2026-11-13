"""The one place Gemini clients are built, with timeouts and retries.

Measured against a local server that accepts connections and never answers:

- `ChatGoogleGenerativeAI(timeout=...)` is honoured, for invoke and stream.
  Its `max_retries` is really an attempt count, and those attempts retry on
  timeouts as well as 408/429/5xx. The library default is 6 attempts and no
  timeout, so before this module a hung generation could hang forever.
- `GoogleGenerativeAIEmbeddings` has no working timeout: `request_options`
  is declared but never read, and a `client_args` timeout is overridden per
  request. It hung indefinitely. Its client is replaced below with a
  google-genai `Client` configured with a timeout and retry policy, which
  was verified to time out and retry as set.
"""

import logging
import time
from collections.abc import Callable
from typing import Literal

from google.genai import Client
from google.genai.types import HttpOptions, HttpRetryOptions
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from app.config import get_settings
from app.errors import upstream_status

logger = logging.getLogger(__name__)

# Failures worth one more try: quota (429) and transient server errors.
# Deliberately not 408/504 or client-side timeouts -- for generation those
# mean the call already ran for minutes, and a retry would double that.
RETRYABLE_GENERATION_STATUS = frozenset({429, 500, 502, 503})


def build_chat_model(purpose: Literal["classify", "generate"]) -> ChatGoogleGenerativeAI:
    settings = get_settings()
    if purpose == "classify":
        timeout, attempts, temperature = settings.classifier_timeout_s, settings.classifier_attempts, 0.0
    else:
        # A single attempt at the library level; retries are decided by
        # call_with_retry, which can tell a fast 429 from a slow timeout.
        timeout, attempts, temperature = settings.generation_timeout_s, 1, 0.2
    return ChatGoogleGenerativeAI(
        model=settings.generation_model,
        google_api_key=settings.require_gemini_api_key(),
        temperature=temperature,
        timeout=timeout,
        max_retries=attempts,
    )


def build_embeddings() -> GoogleGenerativeAIEmbeddings:
    settings = get_settings()
    api_key = settings.require_gemini_api_key()
    embeddings = GoogleGenerativeAIEmbeddings(model=settings.embedding_model, google_api_key=api_key)
    embeddings.client = Client(
        api_key=api_key,
        http_options=HttpOptions(
            timeout=int(settings.embedding_timeout_s * 1000),
            retry_options=HttpRetryOptions(attempts=settings.embedding_attempts),
        ),
    )
    return embeddings


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
    for attempt in range(1, attempts + 1):
        started = clock()
        try:
            return call()
        except Exception as exc:
            if not should_retry_generation(exc, attempt=attempt, attempts=attempts, elapsed_s=clock() - started):
                raise
            time.sleep(get_settings().generation_retry_backoff_s * attempt)
    raise AssertionError("unreachable")  # pragma: no cover
