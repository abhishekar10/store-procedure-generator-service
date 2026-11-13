"""Typed service errors with stable codes.

Every failure a caller can see maps to one `ServiceError`, so the HTTP layer
(JSON body) and the SSE layer (`error` event) report the same code and a
message that is safe to show: no stack trace, no raw upstream JSON. The full
exception still goes to the server log.

Stable codes (callers may branch on these; the messages may change):

    invalid_request        422  body failed validation
    bad_request            400  well-formed but unusable (e.g. nothing to regenerate)
    not_found              404  unknown route or conversation
    method_not_allowed     405
    request_too_large      413  body over MAX_REQUEST_BYTES
    rate_limited           429  in-process limiter refused the request
    not_configured         503  required config (GEMINI_API_KEY) missing
    index_unavailable      503  vector store not loaded
    upstream_rate_limited  503  Gemini returned 429
    upstream_timeout       503  Gemini did not answer within the timeout
    upstream_unavailable   503  any other Gemini/transport failure
    internal_error         500  anything else
"""

from dataclasses import dataclass

import httpx
from chromadb.errors import ChromaError
from google.genai import errors as genai_errors

from app.config import ConfigError


@dataclass
class ServiceError(Exception):
    status: int
    code: str
    message: str
    retry_after: int | None = None  # seconds, sent as a Retry-After header

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class UpstreamTimeout(Exception):
    """Raised when a Gemini call outlives its deadline without the client
    library timing out on its own (e.g. a stream that keeps trickling)."""


def find_in_chain(exc: BaseException | None, types: type | tuple[type, ...]) -> BaseException | None:
    """The first exception of `types` in `exc`'s cause chain. Needed because
    langchain-google-genai re-raises Gemini 4xx errors (429 included) as
    ChatGoogleGenerativeAIError with the original as __cause__, while 5xx
    errors and transport errors come through unwrapped."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, types):
            return exc
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return None


def upstream_status(exc: BaseException) -> int | None:
    api_error = find_in_chain(exc, genai_errors.APIError)
    return api_error.code if api_error is not None else None


def to_service_error(exc: BaseException) -> ServiceError:
    if isinstance(exc, ServiceError):
        return exc
    if find_in_chain(exc, ConfigError):
        return ServiceError(503, "not_configured", "The service is not configured to call the model.")
    if find_in_chain(exc, (UpstreamTimeout, httpx.TimeoutException)):
        return ServiceError(503, "upstream_timeout", "The model did not respond in time. Try again.")
    status = upstream_status(exc)
    if status == 429:
        return ServiceError(503, "upstream_rate_limited", "The model is rate limited. Try again shortly.")
    if status is not None or find_in_chain(exc, httpx.TransportError):
        return ServiceError(503, "upstream_unavailable", "The model is unavailable. Try again shortly.")
    if find_in_chain(exc, ChromaError):
        return ServiceError(503, "index_unavailable", "The retrieval index is not available.")
    return ServiceError(500, "internal_error", "Internal error.")


def error_body(error: ServiceError, request_id: str | None, details: list[dict] | None = None) -> dict:
    body: dict = {"code": error.code, "message": error.message, "request_id": request_id}
    if details:
        body["details"] = details
    return {"error": body}
