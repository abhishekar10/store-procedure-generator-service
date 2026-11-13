"""Structured JSON logging, request IDs, and CloudWatch EMF metrics.

Logs go to stdout as one JSON object per line. On ECS the awslogs driver
ships stdout to CloudWatch Logs, which parses each line as JSON.

Metrics are CloudWatch Embedded Metric Format (EMF) log lines rather than a
`/metrics` endpoint: a line carrying an `_aws` block is turned into metrics
by CloudWatch itself, so there is no agent, no scrape target and no extra
port. Off AWS, the same lines are ordinary structured logs.

Full prompts are never logged at INFO -- only their length and a short hash,
so repeat requests can be correlated without storing what users typed.
"""

import contextvars
import hashlib
import json
import logging
import sys
import time

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

METRICS_NAMESPACE = "SPGenerator"

# Attributes every LogRecord has; anything else on a record came from `extra=`
# and is emitted as a top-level field.
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn's ANSI-coloured duplicate of the message
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        entry.update({key: value for key, value in vars(record).items() if key not in _RESERVED})
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Routes the root logger and uvicorn's loggers through one JSON handler.
    Idempotent, so importing the app twice (tests) does not double-log."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    # The request middleware logs one line per request with the request ID;
    # uvicorn's own access log would duplicate it without one.
    logging.getLogger("uvicorn.access").disabled = True
    # Per-request client chatter from the HTTP and Chroma libraries.
    for name in ("httpx", "httpcore", "chromadb", "google_genai"):
        logging.getLogger(name).setLevel(logging.WARNING)


def prompt_fingerprint(prompt: str) -> dict:
    return {"prompt_chars": len(prompt), "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:12]}


_metrics_logger = logging.getLogger("app.metrics")

_UNITS = {
    "Requests": "Count",
    "SqlValid": "Count",
    "RetrievedTables": "Count",
    "TotalLatency": "Milliseconds",
    "ClassifyLatency": "Milliseconds",
    "RetrievalLatency": "Milliseconds",
    "GenerationLatency": "Milliseconds",
}


def emit_generation_metrics(
    *,
    endpoint: str,
    outcome: str,
    total_latency_ms: float,
    classify_latency_ms: float | None = None,
    retrieval_latency_ms: float | None = None,
    generation_latency_ms: float | None = None,
    sql_valid: bool | None = None,
    retrieved_tables: list[dict] | None = None,
    error_code: str | None = None,
) -> None:
    """One EMF line per generation request.

    Dimensions are kept to Endpoint x Outcome (accepted / rejected / error)
    so the metric count stays small and cheap. Per-request detail -- table
    names and scores, error code -- rides along as plain properties, which
    CloudWatch Logs Insights can query but which do not become metrics.
    """
    values = {
        "Requests": 1,
        "TotalLatency": total_latency_ms,
        "ClassifyLatency": classify_latency_ms,
        "RetrievalLatency": retrieval_latency_ms,
        "GenerationLatency": generation_latency_ms,
        "SqlValid": None if sql_valid is None else int(sql_valid),
        "RetrievedTables": None if retrieved_tables is None else len(retrieved_tables),
    }
    values = {name: round(value, 1) for name, value in values.items() if value is not None}

    emf = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": METRICS_NAMESPACE,
                    "Dimensions": [["Endpoint", "Outcome"]],
                    "Metrics": [{"Name": name, "Unit": _UNITS[name]} for name in values],
                }
            ],
        },
        "Endpoint": endpoint,
        "Outcome": outcome,
        **values,
    }
    if retrieved_tables is not None:
        emf["retrieved_tables"] = retrieved_tables
    if error_code is not None:
        emf["error_code"] = error_code

    _metrics_logger.info("generation.metrics", extra=emf)
