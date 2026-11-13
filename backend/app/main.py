"""FastAPI app wiring together the RAG pipeline and the reference schema.

Stateless: the service stores no conversations. A caller sends the whole
conversation so far in each request and keeps the results itself, so any
task can serve any request and nothing needs a database.

Authentication is deliberately out of scope: in a real deployment this
service sits behind an ALB or API gateway, which authenticates callers
before a request reaches it. See README.md.
"""

import asyncio
import contextvars
import json
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import adapters
from app.config import Settings, get_settings
from app.errors import ServiceError, error_body, to_service_error
from app.middleware import RequestContextMiddleware
from app.observability import configure_logging, emit_generation_metrics, request_id_var
from app.rag.generator import RequestRejected, generate_stored_procedure, retrieved_tables, stream_stored_procedure
from app.rag.retriever import check_collections, serialize_context
from app.ratelimit import TokenBucketLimiter

configure_logging(get_settings().log_level)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    logger.info(
        "startup",
        extra={
            "generation_model": settings.generation_model,
            "generation_timeout_s": settings.generation_timeout_s,
            "rate_limit_per_minute": settings.rate_limit_per_minute if settings.rate_limit_enabled else None,
            "cors_allow_origins": settings.cors_allow_origins,
            "gemini_api_key_set": bool(settings.gemini_api_key),
        },
    )
    yield
    # Uvicorn stops accepting connections on SIGTERM and waits up to
    # --timeout-graceful-shutdown for in-flight requests before this runs.
    logger.info("shutdown")


app = FastAPI(
    title="RAG Stored Procedure Generator",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Starlette wraps in reverse order of registration: CORS is outermost, so
# even a 413 or 500 produced by the request middleware carries CORS headers
# and a browser can read it.
app.add_middleware(RequestContextMiddleware)
# Origins come from configuration and default to none. A browser-facing
# deployment sets CORS_ALLOW_ORIGINS explicitly; a service behind a gateway
# needs no origins at all.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allow_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type", "x-request-id"],
    expose_headers=["x-request-id", "retry-after"],
)


def _error_response(error: ServiceError, details: list[dict] | None = None) -> JSONResponse:
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after else None
    return JSONResponse(error_body(error, request_id_var.get(), details), status_code=error.status, headers=headers)


@app.exception_handler(ServiceError)
async def _service_error_handler(_request: Request, exc: ServiceError):
    return _error_response(exc)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(_request: Request, exc: RequestValidationError):
    """FastAPI's default 422 echoes the offending input back -- for a
    200,000-character prompt, all of it. Report where and why only."""
    details = [
        {"field": ".".join(str(part) for part in error["loc"] if part != "body"), "issue": error["msg"]}
        for error in exc.errors()
    ]
    return _error_response(ServiceError(422, "invalid_request", "Request validation failed."), details)


_HTTP_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed"}


@app.exception_handler(StarletteHTTPException)
async def _http_error_handler(_request: Request, exc: StarletteHTTPException):
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    return _error_response(ServiceError(exc.status_code, code, str(exc.detail)))


@app.get("/health", tags=["Ops"], summary="Liveness: the process is up and serving")
def health():
    """Deliberately dependency-free. It answers 'should this task be
    restarted', so it must not fail because an upstream is down."""
    return {"status": "ok"}


@app.get("/ready", tags=["Ops"], summary="Readiness: able to serve real traffic")
def ready(response: Response):
    """Readiness means config is present and the corpora are loaded.

    This is what the load balancer polls, so it checks only local state --
    no Gemini call, no cost, no dependency on an upstream being reachable.
    """
    settings = get_settings()
    checks: dict[str, object] = {}

    checks["config"] = bool(settings.gemini_api_key)
    checks["reference_db"] = adapters.schema_source().available()

    collections = check_collections()
    checks["collections"] = collections
    checks["vector_store"] = all(count for count in collections.values())

    ok = bool(checks["config"] and checks["reference_db"] and checks["vector_store"])
    if not ok:
        response.status_code = 503
    return {"status": "ready" if ok else "not_ready", "checks": checks}


class Message(BaseModel):
    """One turn of the conversation so far, as the caller keeps it. Only what
    the model needs: the user's request, or what the service answered."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    kind: Literal["generation", "rejected"] | None = Field(
        default=None,
        description="Assistant messages only: a script (`generation`, the default) or a `rejected` request.",
    )
    content: str | None = Field(
        default=None,
        description="User messages: the request (at most MAX_PROMPT_CHARS). Rejected assistant messages: the reason.",
    )
    full_script: str | None = Field(default=None, description="Assistant `generation` messages: the script returned.")

    @model_validator(mode="after")
    def _shape_matches_role(self) -> "Message":
        if self.role == "user":
            if self.kind is not None or self.full_script is not None:
                raise ValueError("a user message has only content")
            if not (self.content or "").strip():
                raise ValueError("content must not be blank")
            limit = get_settings().max_prompt_chars
            if len(self.content) > limit:
                raise ValueError(f"content must be at most {limit} characters")
            return self
        self.kind = self.kind or "generation"
        if self.kind == "generation" and not (self.full_script or "").strip():
            raise ValueError("an assistant generation needs full_script")
        if self.kind == "rejected" and not (self.content or "").strip():
            raise ValueError("an assistant rejection needs content (the reason)")
        return self


_EXAMPLE_FIRST_TURN = {"messages": [{"role": "user", "content": "Process payroll for a given pay period"}]}
_EXAMPLE_FOLLOW_UP = {
    "messages": [
        {"role": "user", "content": "Process payroll for a given pay period"},
        {"role": "assistant", "kind": "generation", "full_script": "-- SECTION: PRE_VALIDATION\n..."},
        {"role": "user", "content": "Also skip employees who are on leave"},
    ]
}


class GenerateRequest(BaseModel):
    """The conversation so far, ending with the new request. To regenerate
    an answer, resend the conversation without its last assistant message."""

    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [_EXAMPLE_FIRST_TURN, _EXAMPLE_FOLLOW_UP]})

    messages: list[Message] = Field(min_length=1)

    @field_validator("messages")
    @classmethod
    def _ends_with_a_request(cls, messages: list[Message]) -> list[Message]:
        if messages[-1].role != "user":
            raise ValueError("the last message must be the user's request")
        return messages

    @property
    def prompt(self) -> str:
        return self.messages[-1].content

    @property
    def prior_messages(self) -> list[dict]:
        return [message.model_dump(exclude_none=True) for message in self.messages[:-1]]


_limiter: TokenBucketLimiter | None = None
_limiter_config: tuple | None = None
_limiter_lock = threading.Lock()


def _client_key(request: Request, settings: Settings) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if settings.trust_forwarded_for and forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def enforce_rate_limit(request: Request) -> None:
    global _limiter, _limiter_config
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    config = (settings.rate_limit_per_minute, settings.rate_limit_burst)
    with _limiter_lock:
        if _limiter is None or _limiter_config != config:
            _limiter, _limiter_config = TokenBucketLimiter(*config), config
        limiter = _limiter
    retry_after = limiter.acquire(_client_key(request, settings))
    if retry_after is not None:
        raise ServiceError(429, "rate_limited", "Too many generation requests. Slow down.", retry_after=retry_after)


def _record(
    endpoint: str,
    *,
    outcome: str,
    total_latency_ms: float,
    timings: dict[str, float] | None = None,
    tables: list[dict] | None = None,
    sql_valid: bool | None = None,
    error_code: str | None = None,
) -> None:
    """One CloudWatch EMF metric line per finished generation request."""
    timings = timings or {}
    emit_generation_metrics(
        endpoint=endpoint,
        outcome=outcome,
        total_latency_ms=total_latency_ms,
        classify_latency_ms=timings.get("classify"),
        retrieval_latency_ms=timings.get("retrieval"),
        generation_latency_ms=timings.get("generation"),
        sql_valid=sql_valid,
        retrieved_tables=tables,
        error_code=error_code,
    )


def _log_failure(exc: BaseException, error: ServiceError) -> None:
    if error.status >= 500 and error.code == "internal_error":
        logger.exception("generation.failed", extra={"error_code": error.code})
    else:
        # Upstream and config failures are expected operating conditions;
        # the error type and message are enough, a traceback is noise.
        logger.warning(
            "generation.failed", extra={"error_code": error.code, "error_type": type(exc).__name__, "error": str(exc)}
        )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


v1 = APIRouter(prefix="/v1")


def _script_result(result: dict) -> dict:
    return {
        "full_script": result["full_script"],
        "sections": result["sections"],
        "retrieved_context": serialize_context(result["retrieved_context"]),
        "sql_valid": result["sql_valid"],
        "sql_issues": result["sql_issues"],
    }


@v1.post(
    "/generate",
    tags=["Generation"],
    summary="Generate a stored procedure from the conversation so far (non-streaming)",
    dependencies=[Depends(enforce_rate_limit)],
)
def generate(request: GenerateRequest):
    """Runs synchronously on FastAPI's threadpool, so the model call does not
    block the event loop. Prefer /v1/generate/stream behind a load balancer:
    this endpoint sends nothing until the script is done."""
    start_time = time.monotonic()

    def elapsed_ms() -> float:
        return (time.monotonic() - start_time) * 1000

    try:
        result = generate_stored_procedure(request.prompt, prior_messages=request.prior_messages)
    except RequestRejected as exc:
        _record("generate", outcome="rejected", total_latency_ms=elapsed_ms(), timings=exc.timings_ms)
        return {"rejected": True, "reason": exc.reason}
    except Exception as exc:
        error = to_service_error(exc)
        _log_failure(exc, error)
        _record("generate", outcome="error", total_latency_ms=elapsed_ms(), error_code=error.code)
        raise error from exc

    _record(
        "generate",
        outcome="accepted",
        total_latency_ms=elapsed_ms(),
        timings=result["timings_ms"],
        tables=retrieved_tables(result["retrieved_context"]),
        sql_valid=result["sql_valid"],
    )
    return {"rejected": False, "prompt": result["prompt"], **_script_result(result)}


_HEARTBEAT = ": keepalive\n\n"
_END = object()


async def _run_with_heartbeat(make_events: Callable[[], Iterator[str]], interval_s: float) -> AsyncIterator[str]:
    """Runs a blocking event generator on its own thread and relays what it
    yields, sending an SSE comment line whenever `interval_s` passes with
    nothing to say.

    Generation spends a long time thinking before the first token (often
    30-60s+), and a load balancer drops a connection that is idle longer
    than its idle timeout. The heartbeat keeps the stream alive however long
    the model thinks. EventSource clients and the demo UI ignore comments.

    When the client disconnects, Starlette cancels this generator; the
    worker sees the flag at its next event and stops, instead of streaming
    the rest of the generation into nothing. It is a daemon thread, so it
    never holds up process exit.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancelled = threading.Event()

    def put(item) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:  # loop already closed: nobody is listening
            cancelled.set()

    def worker() -> None:
        try:
            for item in make_events():
                if cancelled.is_set():
                    break
                put(item)
        except BaseException as exc:  # noqa: BLE001 -- re-raised on the event loop side
            put(exc)
        finally:
            put(_END)

    context = contextvars.copy_context()  # carries the request ID into the thread
    threading.Thread(target=context.run, args=(worker,), daemon=True, name="sse-worker").start()
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval_s)
            except TimeoutError:
                yield _HEARTBEAT
                continue
            if item is _END:
                return
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        cancelled.set()


@v1.post(
    "/generate/stream",
    tags=["Generation"],
    summary="Generate or revise a stored procedure, streamed as Server-Sent Events",
    dependencies=[Depends(enforce_rate_limit)],
)
def generate_stream(request: GenerateRequest):
    def event_stream() -> Iterator[str]:
        overall_start = time.monotonic()

        def elapsed_ms() -> float:
            return (time.monotonic() - overall_start) * 1000

        try:
            for event in stream_stored_procedure(request.prompt, prior_messages=request.prior_messages):
                if event["type"] == "status":
                    yield _sse("status", {"stage": event["stage"]})
                elif event["type"] == "chunk":
                    yield _sse("chunk", {"text": event["text"]})
                elif event["type"] == "rejected":
                    _record(
                        "generate_stream", outcome="rejected", total_latency_ms=elapsed_ms(),
                        timings=event["timings_ms"],
                    )
                    yield _sse("rejected", {"reason": event["reason"]})
                    return
                else:
                    _record(
                        "generate_stream", outcome="accepted", total_latency_ms=elapsed_ms(),
                        timings=event["timings_ms"], tables=retrieved_tables(event["retrieved_context"]),
                        sql_valid=event["sql_valid"],
                    )
                    yield _sse("done", _script_result(event))
        except Exception as exc:  # noqa: BLE001 -- surfaced to the client as a typed SSE error event
            error = to_service_error(exc)
            _log_failure(exc, error)
            _record("generate_stream", outcome="error", total_latency_ms=elapsed_ms(), error_code=error.code)
            yield _sse("error", error_body(error, request_id_var.get())["error"])

    return StreamingResponse(
        _run_with_heartbeat(event_stream, get_settings().sse_heartbeat_s),
        media_type="text/event-stream",
        # Tell nginx-style proxies not to buffer the stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@v1.get("/schema", tags=["Reference Schema"], summary="List reference schema tables and columns")
def get_schema():
    return {
        "tables": [
            {
                "table_name": table.name,
                "purpose": table.purpose,
                "columns": [
                    {
                        "name": column.name,
                        "type": column.type,
                        "primary_key": column.primary_key,
                        "not_null": column.not_null,
                        "description": column.description,
                    }
                    for column in table.columns
                ],
            }
            for table in adapters.schema_source().tables()
        ]
    }


app.include_router(v1)
