"""Request-scoped plumbing, as one pure ASGI middleware:

- a request ID on every request: the caller's `X-Request-ID` if it is sane,
  otherwise a fresh one, set in a contextvar so every log line carries it,
  and echoed back as a response header;
- a request body cap, checked against Content-Length up front and against
  the bytes actually received for chunked bodies;
- one access-log line per request, with status and latency;
- a last-resort handler, so an unexpected exception becomes a JSON 500 that
  carries the request ID, never a stack trace.

Pure ASGI rather than BaseHTTPMiddleware so that streaming responses pass
straight through and the contextvar stays set while the body is produced.
"""

import json
import logging
import re
import time
import uuid

from app.config import get_settings
from app.errors import ServiceError, error_body
from app.observability import request_id_var

logger = logging.getLogger("app.request")

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_PROBE_PATHS = {"/health", "/ready"}


class _BodyTooLarge(Exception):
    pass


def _header(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header(scope, b"x-request-id")
        request_id = incoming if incoming and _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.monotonic()
        status = {"code": None}
        limit = get_settings().max_request_bytes

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-request-id", request_id.encode())]
            await send(message)

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge
            return message

        try:
            content_length = _header(scope, b"content-length")
            if content_length and content_length.isdigit() and int(content_length) > limit:
                raise _BodyTooLarge
            await self.app(scope, limited_receive, send_with_id)
        except _BodyTooLarge:
            await self._send_error(
                send_with_id, status, ServiceError(413, "request_too_large", f"Request body exceeds {limit} bytes.")
            )
        except Exception:
            logger.exception("request.unhandled_error")
            await self._send_error(send_with_id, status, ServiceError(500, "internal_error", "Internal error."))
        finally:
            # Load balancer probes arrive every few seconds per task; logging
            # each healthy one at INFO would drown the real traffic.
            probe = scope["path"] in _PROBE_PATHS and status["code"] == 200
            logger.log(
                logging.DEBUG if probe else logging.INFO,
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status["code"],
                    "latency_ms": round((time.monotonic() - started) * 1000, 1),
                },
            )
            request_id_var.reset(token)

    @staticmethod
    async def _send_error(send, status, error: ServiceError):
        if status["code"] is not None:
            # Headers already went out (e.g. mid-stream); nothing valid can
            # be sent now. The log line above records what happened.
            return
        body = json.dumps(error_body(error, request_id_var.get())).encode()
        await send(
            {
                "type": "http.response.start",
                "status": error.status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})
