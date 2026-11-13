"""The /v1 request and response models -- the service's contract.

They drive validation and the OpenAPI spec (checked in at docs/openapi.json,
pinned by tests/test_contract.py), so the examples here are what callers see.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import get_settings

# --- request ------------------------------------------------------------------


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


# --- responses ------------------------------------------------------------------

_SCRIPT_EXAMPLE = (
    "-- SECTION: PRE_VALIDATION\nCREATE OR REPLACE PROCEDURE process_payroll(p_payroll_run_id INTEGER)\n"
    "LANGUAGE plpgsql\nAS $$\n...\n-- SECTION: ROLLBACK_MANUAL\n"
    "CREATE OR REPLACE PROCEDURE rollback_process_payroll(p_payroll_run_id INTEGER) ...\n"
)


class RetrievedChunk(BaseModel):
    id: str = Field(examples=["schema::payroll_runs"])
    text: str
    metadata: dict = Field(examples=[{"table_name": "payroll_runs"}])
    score: float | None = Field(
        description="Cosine distance for schema chunks (lower is closer); null for pattern chunks, "
        "which are looked up, not searched.",
        examples=[0.31],
    )


class RetrievedContext(BaseModel):
    schema_chunks: list[RetrievedChunk] = Field(description="Tables retrieved for this request, nearest first.")
    pattern_chunks: list[RetrievedChunk] = Field(description="One house-style pattern per section, in section order.")


class ScriptResult(BaseModel):
    full_script: str = Field(description="One deployable script with seven `-- SECTION:` markers.",
                             examples=[_SCRIPT_EXAMPLE])
    sections: dict[str, str] = Field(
        description="The script split at its markers: pre_validation, temp_tables, backup, main_script, "
        "post_validation, rollback_auto, rollback_manual.",
    )
    retrieved_context: RetrievedContext
    sql_valid: bool = Field(description="Passed the best-effort structural check. Not a guarantee it runs.")
    sql_issues: list[str] = Field(description="What the structural check found; empty when sql_valid.")


class GeneratedResponse(ScriptResult):
    rejected: Literal[False] = False
    prompt: str = Field(description="The request this script answers (the last user message).")


class RejectedResponse(BaseModel):
    rejected: Literal[True] = True
    reason: str = Field(examples=["The request is general chit-chat, not a database operation."])


class ErrorDetail(BaseModel):
    field: str = Field(examples=["messages.0"])
    issue: str = Field(examples=["Value error, content must not be blank"])


class ErrorInfo(BaseModel):
    code: str = Field(description="Stable; callers may branch on it. See README for the list.",
                      examples=["upstream_unavailable"])
    message: str = Field(description="Safe to show; may change.", examples=["The model is unavailable. Try again."])
    request_id: str | None = Field(description="Also in the X-Request-ID response header.")
    details: list[ErrorDetail] | None = Field(default=None, description="Validation errors only.")


class ErrorResponse(BaseModel):
    error: ErrorInfo


class SchemaColumn(BaseModel):
    name: str
    type: str
    primary_key: bool
    not_null: bool
    description: str


class SchemaTable(BaseModel):
    table_name: str
    purpose: str
    columns: list[SchemaColumn]


class SchemaResponse(BaseModel):
    tables: list[SchemaTable]
