"""FastAPI app wiring together the RAG pipeline, reference schema, and
chat conversation storage.

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

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.db.conversation_store import (
    add_assistant_message,
    add_rejected_message,
    add_user_message,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
)
from app.db.reference_schema import list_tables
from app.db.telemetry_store import get_metrics_summary, log_event
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
    allow_methods=["GET", "POST", "DELETE"],
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
    checks["reference_db"] = settings.reference_db_path.is_file()

    collections = check_collections()
    checks["collections"] = collections
    checks["vector_store"] = all(count for count in collections.values())

    ok = bool(checks["config"] and checks["reference_db"] and checks["vector_store"])
    if not ok:
        response.status_code = 503
    return {"status": "ready" if ok else "not_ready", "checks": checks}


def _check_prompt(prompt: str) -> str:
    limit = get_settings().max_prompt_chars
    if len(prompt) > limit:
        raise ValueError(f"must be at most {limit} characters")
    return prompt


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, description="Plain-English request. At most MAX_PROMPT_CHARS (default 8000).")
    conversation_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9-]+$")

    @field_validator("prompt")
    @classmethod
    def _prompt_not_blank_or_huge(cls, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("must not be blank")
        return _check_prompt(prompt)


class StreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(default="", description="Required unless is_regenerate. At most MAX_PROMPT_CHARS.")
    conversation_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    is_regenerate: bool = False

    @field_validator("prompt")
    @classmethod
    def _prompt_not_huge(cls, prompt: str) -> str:
        return _check_prompt(prompt)

    @model_validator(mode="after")
    def _prompt_required_unless_regenerating(self) -> "StreamRequest":
        # A regenerate replays the last stored user message, so the demo UI
        # sends prompt="". Everything else needs a real prompt.
        if not self.is_regenerate and not self.prompt.strip():
            raise ValueError("prompt must not be blank")
        return self


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
    prompt: str,
    conversation_id: str | None,
    message_id: str | None,
    is_regenerate: bool,
    total_latency_ms: float,
    timings: dict[str, float] | None = None,
    tables: list[dict] | None = None,
    pattern_count: int | None = None,
    sql_valid: bool | None = None,
    sql_issues: list[str] | None = None,
    rejection_reason: str | None = None,
    error_code: str | None = None,
) -> None:
    """One place that records a finished generation request: the SQLite
    telemetry store (removed in Phase 3) and the CloudWatch EMF metric line."""
    timings = timings or {}
    log_event(
        conversation_id=conversation_id,
        message_id=message_id,
        prompt=prompt,
        is_regenerate=is_regenerate,
        accepted=outcome == "accepted",
        rejection_reason=rejection_reason,
        retrieved_tables=tables,
        pattern_count=pattern_count,
        classify_latency_ms=timings.get("classify"),
        retrieval_latency_ms=timings.get("retrieval"),
        generation_latency_ms=timings.get("generation"),
        total_latency_ms=total_latency_ms,
        sql_valid=sql_valid,
        sql_issues=sql_issues,
        error_message=error_code,
    )
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


def _prior_messages_for(conversation_id: str | None) -> list[dict]:
    if conversation_id is None:
        return []
    conversation = get_conversation(conversation_id)
    return conversation["messages"] if conversation else []


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post(
    "/generate",
    tags=["Generation"],
    summary="Generate a stored procedure from a plain-English prompt (non-streaming)",
    dependencies=[Depends(enforce_rate_limit)],
)
def generate(request: GenerateRequest):
    """Runs synchronously on FastAPI's threadpool, so a multi-minute model
    call does not block the event loop. Prefer /generate/stream behind a load
    balancer: this endpoint sends nothing until the script is done, so the
    connection sits idle for the whole generation."""
    conversation_id = request.conversation_id
    prior_messages = _prior_messages_for(conversation_id)
    start_time = time.monotonic()

    def elapsed_ms() -> float:
        return (time.monotonic() - start_time) * 1000

    if conversation_id is None:
        conversation_id = create_conversation(request.prompt)
    add_user_message(conversation_id, request.prompt)

    common = {"prompt": request.prompt, "conversation_id": conversation_id, "is_regenerate": False}
    try:
        result = generate_stored_procedure(request.prompt, prior_messages=prior_messages)
    except RequestRejected as exc:
        message_id = add_rejected_message(conversation_id, exc.reason)
        _record(
            "generate", outcome="rejected", message_id=message_id, total_latency_ms=elapsed_ms(),
            timings=exc.timings_ms, rejection_reason=exc.reason, **common,
        )
        return {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "rejected": True,
            "reason": exc.reason,
        }
    except Exception as exc:
        error = to_service_error(exc)
        _log_failure(exc, error)
        _record(
            "generate", outcome="error", message_id=None, total_latency_ms=elapsed_ms(),
            error_code=error.code, **common,
        )
        raise error from exc

    retrieved_context = serialize_context(result["retrieved_context"])
    message_id = add_assistant_message(
        conversation_id,
        full_script=result["full_script"],
        sections=result["sections"],
        retrieved_context=retrieved_context,
        sql_valid=result["sql_valid"],
        sql_issues=result["sql_issues"],
    )
    _record(
        "generate", outcome="accepted", message_id=message_id, total_latency_ms=elapsed_ms(),
        timings=result["timings_ms"], tables=retrieved_tables(result["retrieved_context"]),
        pattern_count=len(retrieved_context["pattern_chunks"]), sql_valid=result["sql_valid"],
        sql_issues=result["sql_issues"], **common,
    )

    return {
        "conversation_id": conversation_id,
        "message_id": message_id,
        "prompt": result["prompt"],
        "full_script": result["full_script"],
        "sections": result["sections"],
        "retrieved_context": retrieved_context,
        "sql_valid": result["sql_valid"],
        "sql_issues": result["sql_issues"],
    }


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


@app.post(
    "/generate/stream",
    tags=["Generation"],
    summary="Generate or revise a stored procedure, streamed as Server-Sent Events",
    dependencies=[Depends(enforce_rate_limit)],
)
def generate_stream(request: StreamRequest):
    conversation_id = request.conversation_id

    if request.is_regenerate:
        if conversation_id is None:
            raise ServiceError(400, "bad_request", "conversation_id is required to regenerate")
        conversation = get_conversation(conversation_id)
        if conversation is None:
            raise ServiceError(404, "not_found", "Conversation not found")

        messages = conversation["messages"]
        if not messages or messages[-1]["role"] != "assistant":
            raise ServiceError(400, "bad_request", "Nothing to regenerate")

        last_user_message = next((m for m in reversed(messages) if m["role"] == "user"), None)
        if last_user_message is None:
            raise ServiceError(400, "bad_request", "Nothing to regenerate")

        user_prompt = last_user_message["content"]
        prior_messages = messages[: messages.index(last_user_message)]
    else:
        user_prompt = request.prompt
        prior_messages = _prior_messages_for(conversation_id)
        if conversation_id is None:
            conversation_id = create_conversation(user_prompt)
        add_user_message(conversation_id, user_prompt)

    def event_stream() -> Iterator[str]:
        overall_start = time.monotonic()

        def elapsed_ms() -> float:
            return (time.monotonic() - overall_start) * 1000

        common = {"prompt": user_prompt, "conversation_id": conversation_id, "is_regenerate": request.is_regenerate}

        yield _sse("start", {"conversation_id": conversation_id})
        try:
            for event in stream_stored_procedure(user_prompt, prior_messages=prior_messages):
                if event["type"] == "status":
                    yield _sse("status", {"stage": event["stage"]})
                    continue

                if event["type"] == "chunk":
                    yield _sse("chunk", {"text": event["text"]})
                    continue

                if event["type"] == "rejected":
                    message_id = add_rejected_message(conversation_id, event["reason"])
                    _record(
                        "generate_stream", outcome="rejected", message_id=message_id,
                        total_latency_ms=elapsed_ms(), timings=event["timings_ms"],
                        rejection_reason=event["reason"], **common,
                    )
                    yield _sse(
                        "rejected",
                        {"conversation_id": conversation_id, "message_id": message_id, "reason": event["reason"]},
                    )
                    return

                retrieved_context = serialize_context(event["retrieved_context"])
                message_id = add_assistant_message(
                    conversation_id,
                    full_script=event["full_script"],
                    sections=event["sections"],
                    retrieved_context=retrieved_context,
                    sql_valid=event["sql_valid"],
                    sql_issues=event["sql_issues"],
                )
                _record(
                    "generate_stream", outcome="accepted", message_id=message_id,
                    total_latency_ms=elapsed_ms(), timings=event["timings_ms"],
                    tables=retrieved_tables(event["retrieved_context"]),
                    pattern_count=len(retrieved_context["pattern_chunks"]),
                    sql_valid=event["sql_valid"], sql_issues=event["sql_issues"], **common,
                )
                yield _sse(
                    "done",
                    {
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "full_script": event["full_script"],
                        "sections": event["sections"],
                        "retrieved_context": retrieved_context,
                        "sql_valid": event["sql_valid"],
                        "sql_issues": event["sql_issues"],
                    },
                )
        except Exception as exc:  # noqa: BLE001 -- surfaced to the client as a typed SSE error event
            error = to_service_error(exc)
            _log_failure(exc, error)
            _record(
                "generate_stream", outcome="error", message_id=None, total_latency_ms=elapsed_ms(),
                error_code=error.code, **common,
            )
            yield _sse("error", error_body(error, request_id_var.get())["error"])

    return StreamingResponse(
        _run_with_heartbeat(event_stream, get_settings().sse_heartbeat_s),
        media_type="text/event-stream",
        # Tell nginx-style proxies not to buffer the stream.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/conversations", tags=["Conversations"], summary="List chat conversations")
def get_conversations():
    return {"conversations": list_conversations()}


@app.get(
    "/conversations/{conversation_id}",
    tags=["Conversations"],
    summary="Get a conversation with its full message history",
)
def get_conversation_detail(conversation_id: str):
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ServiceError(404, "not_found", "Conversation not found")
    return conversation


@app.delete(
    "/conversations/{conversation_id}",
    tags=["Conversations"],
    summary="Delete a conversation and its messages",
)
def delete_conversation_route(conversation_id: str):
    if not delete_conversation(conversation_id):
        raise ServiceError(404, "not_found", "Conversation not found")
    return {"status": "deleted"}


@app.get("/schema", tags=["Reference Schema"], summary="List reference schema tables and columns")
def get_schema():
    return {"tables": list_tables()}


@app.get(
    "/metrics",
    tags=["Admin"],
    summary="Basic aggregate telemetry: acceptance rate, latency per pipeline stage, output validity",
)
def get_metrics():
    return get_metrics_summary()
