# syntax=docker/dockerfile:1.7
#
# Multi-stage build for the stored-procedure generator service.
#
# The corpus is small (9 schema chunks, 6 pattern templates) and static, so the
# reference database and the Chroma index are built INTO the image rather than
# mounted at runtime. That means the running container needs no writable state
# and no startup work, and /ready is true the moment the process is up.
#
# Building the index calls the Gemini embeddings API, so the key is needed at
# build time. It is passed as a BuildKit secret, never as an ARG or ENV -- an
# ARG would be visible in `docker history` forever.
#
#   DOCKER_BUILDKIT=1 docker build \
#       --secret id=gemini_key,env=GEMINI_API_KEY \
#       -t sp-generator:dev backend/

ARG PYTHON_VERSION=3.12.13

# --------------------------------------------------------------------------
# Stage 1: dependencies
# --------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS deps

COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    # Some wheels here are large (onnxruntime, chromadb-rust-bindings); the
    # default 30s read timeout is not enough on a slow connection.
    UV_HTTP_TIMEOUT=300

WORKDIR /build

# Only the manifest, so this layer caches until dependencies actually change.
COPY pyproject.toml ./
RUN uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python -r pyproject.toml

# --------------------------------------------------------------------------
# Stage 2: build the corpus (reference DB + vector index)
# --------------------------------------------------------------------------
FROM deps AS corpus

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/build

COPY app ./app
COPY db ./db

# No API key needed: pure sqlite3 over schema.sql + seed_data.sql.
RUN python db/build_db.py

# Embeds both collections into a clean store. The secret is mounted only for
# this command and leaves no trace in the layer.
RUN --mount=type=secret,id=gemini_key \
    GEMINI_API_KEY="$(cat /run/secrets/gemini_key)" \
    VECTOR_STORE_DIR=/build/data/vector_store \
    python -m app.rag.ingest

# --------------------------------------------------------------------------
# Stage 3: runtime
# --------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

# Non-root. A fixed uid/gid keeps file ownership predictable.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/srv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /srv

COPY --from=deps    --chown=root:root /opt/venv            /opt/venv
COPY --from=corpus  --chown=root:root /build/app           /srv/app
COPY --from=corpus  --chown=root:root /build/db            /srv/db
# The corpus is read-only at runtime, but app.db is still written here until
# conversation storage is removed, so this one directory belongs to the app user.
COPY --from=corpus  --chown=app:app   /build/data          /srv/data

USER app
EXPOSE 8000

# Local convenience; ECS uses the ALB target-group check against /ready.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"]

# Single worker: generation is a 44-232s upstream call, so throughput comes
# from task count, not from workers inside one container. Generation runs on
# threads, so one worker serves many concurrent requests.
#
# On SIGTERM uvicorn stops accepting and gives in-flight requests up to
# GRACEFUL_SHUTDOWN_S to finish. ECS SIGKILLs at the task's stopTimeout
# (default 30s, max 120s), so stopTimeout must sit above this value. A
# generation longer than that is still cut off by a deploy: the cap is
# ECS's, not ours. --no-access-log: the app logs one JSON line per request.
ENV GRACEFUL_SHUTDOWN_S=110
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --no-access-log --timeout-graceful-shutdown ${GRACEFUL_SHUTDOWN_S}"]
