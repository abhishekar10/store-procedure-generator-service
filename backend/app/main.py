"""FastAPI app wiring together the RAG pipeline, reference schema, and
chat conversation storage."""

import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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
from app.rag.generator import RequestRejected, generate_stored_procedure, stream_stored_procedure
from app.rag.ingest import run_ingest
from app.rag.retriever import serialize_context

app = FastAPI(
    title="RAG Stored Procedure Generator",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class GenerateRequest(BaseModel):
    prompt: str
    conversation_id: str | None = None


class StreamRequest(BaseModel):
    prompt: str
    conversation_id: str | None = None
    is_regenerate: bool = False


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
)
def generate(request: GenerateRequest):
    conversation_id = request.conversation_id
    prior_messages = _prior_messages_for(conversation_id)

    if conversation_id is None:
        conversation_id = create_conversation(request.prompt)
    add_user_message(conversation_id, request.prompt)

    try:
        result = generate_stored_procedure(request.prompt, prior_messages=prior_messages)
    except RequestRejected as exc:
        message_id = add_rejected_message(conversation_id, exc.reason)
        return {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "rejected": True,
            "reason": exc.reason,
        }

    retrieved_context = serialize_context(result["retrieved_context"])

    message_id = add_assistant_message(
        conversation_id,
        full_script=result["full_script"],
        sections=result["sections"],
        retrieved_context=retrieved_context,
        sql_valid=result["sql_valid"],
        sql_issues=result["sql_issues"],
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


@app.post(
    "/generate/stream",
    tags=["Generation"],
    summary="Generate or revise a stored procedure, streamed as Server-Sent Events",
)
def generate_stream(request: StreamRequest):
    conversation_id = request.conversation_id

    if request.is_regenerate:
        if conversation_id is None:
            raise HTTPException(status_code=400, detail="conversation_id is required to regenerate")
        conversation = get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        messages = conversation["messages"]
        if not messages or messages[-1]["role"] != "assistant":
            raise HTTPException(status_code=400, detail="Nothing to regenerate")

        last_user_message = next((m for m in reversed(messages) if m["role"] == "user"), None)
        if last_user_message is None:
            raise HTTPException(status_code=400, detail="Nothing to regenerate")

        user_prompt = last_user_message["content"]
        prior_messages = messages[: messages.index(last_user_message)]
    else:
        user_prompt = request.prompt
        prior_messages = _prior_messages_for(conversation_id)
        if conversation_id is None:
            conversation_id = create_conversation(user_prompt)
        add_user_message(conversation_id, user_prompt)

    def event_stream():
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
        except Exception as exc:  # noqa: BLE001 -- surfaced to the client as an SSE error event
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.delete(
    "/conversations/{conversation_id}",
    tags=["Conversations"],
    summary="Delete a conversation and its messages",
)
def delete_conversation_route(conversation_id: str):
    if not delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}


@app.get("/schema", tags=["Reference Schema"], summary="List reference schema tables and columns")
def get_schema():
    return {"tables": list_tables()}


@app.post("/ingest", tags=["Admin"], summary="Re-embed the schema and pattern corpus into Chroma")
def ingest():
    run_ingest()
    return {"status": "ok"}
