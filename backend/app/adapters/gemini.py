"""LLMProvider over Gemini: the one place Gemini clients are built, with
timeouts, library retries and the thinking level.

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

Generation and the classifier each send a thinking level
(GENERATION_THINKING_LEVEL, CLASSIFIER_THINKING_LEVEL, both default "low"):
the model's own default spent most of each request thinking. The model alias
floats, so a future model may reject a level -- 3.8-flash already rejects
"minimal" with a 400. Such a rejection drops that level for that model for
the life of the process and retries at once without it, so a model change
slows requests down instead of failing all of them.
"""

import logging
from collections.abc import Callable, Iterator

from google.genai import Client
from google.genai import errors as genai_errors
from google.genai.types import HttpOptions, HttpRetryOptions
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from app.adapters.base import Purpose
from app.config import get_settings
from app.errors import find_in_chain

logger = logging.getLogger(__name__)


# (model, thinking level) pairs Gemini has refused with a 400. Keyed by level
# as well as model: a model that rejects "high" may still accept "low".
_thinking_rejected: set[tuple[str, str]] = set()


def _configured_thinking_level(purpose: Purpose) -> str:
    settings = get_settings()
    return settings.classifier_thinking_level if purpose == "classify" else settings.generation_thinking_level


def thinking_level(purpose: Purpose) -> str | None:
    """The thinking level a `purpose` call sends, or None to leave it to the
    model -- either because it is set to "auto" or because the model refused it."""
    level = _configured_thinking_level(purpose)
    if level == "auto" or (get_settings().generation_model, level) in _thinking_rejected:
        return None
    return level


def disable_thinking_if_rejected(exc: BaseException, purpose: Purpose) -> bool:
    """True, once per model and level, when `exc` is Gemini refusing the
    thinking level `purpose` sent. Later builds then omit it."""
    level = thinking_level(purpose)
    api_error = find_in_chain(exc, genai_errors.ClientError)
    if level is None or api_error is None or api_error.code != 400 or "thinking" not in str(api_error).lower():
        return False
    model = get_settings().generation_model
    _thinking_rejected.add((model, level))
    logger.warning(
        "thinking_level_rejected",
        extra={"purpose": purpose, "model": model, "thinking_level": level, "upstream_message": str(api_error)[:300]},
    )
    return True


def with_thinking_fallback[T](call: Callable[[], T], purpose: Purpose) -> T:
    """Runs `call`; if Gemini refuses the thinking level, runs it once more
    without it. `call` must build its model afresh, so the retry omits it."""
    try:
        return call()
    except Exception as exc:
        if not disable_thinking_if_rejected(exc, purpose):
            raise
        return call()


def build_chat_model(purpose: Purpose) -> ChatGoogleGenerativeAI:
    settings = get_settings()
    if purpose == "classify":
        timeout, attempts, temperature = settings.classifier_timeout_s, settings.classifier_attempts, 0.0
    else:
        # A single attempt at the library level; retries are decided by
        # call_with_retry, which can tell a fast 429 from a slow timeout.
        timeout, attempts, temperature = settings.generation_timeout_s, 1, 0.2
    level = thinking_level(purpose)
    return ChatGoogleGenerativeAI(
        model=settings.generation_model,
        google_api_key=settings.require_gemini_api_key(),
        temperature=temperature,
        timeout=timeout,
        max_retries=attempts,
        **({} if level is None else {"thinking_level": level}),
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


def _text(content: str | list) -> str:
    """ChatGoogleGenerativeAI returns `.content` as a plain string for some
    models and as a list of content blocks (text/thinking/etc.) for others --
    normalize both into a single string."""
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
    )


class GeminiProvider:
    """Stateless: clients are built per call from the current settings,
    which is what lets the thinking-level fallback take effect at once."""

    def complete(self, prompt: str, purpose: Purpose) -> str:
        return with_thinking_fallback(lambda: _text(build_chat_model(purpose).invoke(prompt).content), purpose)

    def stream(self, prompt: str, purpose: Purpose) -> Iterator[str]:
        emitted = False
        try:
            for chunk in build_chat_model(purpose).stream(prompt):
                text = _text(chunk.content)
                if text:
                    emitted = True
                    yield text
        except Exception as exc:
            # A refused thinking level arrives before any text. Once it is
            # disabled the retry builds without it, and a second refusal is
            # no longer recognised as one, so this recurses at most once.
            if emitted or not disable_thinking_if_rejected(exc, purpose):
                raise
            yield from self.stream(prompt, purpose)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return build_embeddings().embed_documents(texts, task_type="retrieval_document")

    def embed_query(self, text: str) -> list[float]:
        return build_embeddings().embed_query(text, task_type="retrieval_query")

    def log_fields(self, purpose: Purpose) -> dict:
        return {"model": get_settings().generation_model, "thinking_level": thinking_level(purpose) or "auto"}
