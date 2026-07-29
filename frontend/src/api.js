const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

export async function listConversations() {
  const response = await fetch(`${API_BASE}/conversations`);
  const data = await response.json();
  return data.conversations;
}

export async function getConversation(conversationId) {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`);
  if (!response.ok) throw new Error("Conversation not found");
  return response.json();
}

export async function deleteConversation(conversationId) {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`, { method: "DELETE" });
  if (!response.ok) throw new Error("Failed to delete conversation");
}

function parseSSEBlock(block) {
  let eventName = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  return { event: eventName, data: JSON.parse(dataLines.join("\n")) };
}

/** Streams a generation over SSE, yielding {event, data} objects:
 * "start" -> {conversation_id}
 * "chunk" -> {text}
 * "done"  -> {conversation_id, message_id, full_script, sections, retrieved_context, sql_valid, sql_issues}
 * "error" -> {message}
 */
export async function* streamGenerate({ prompt, conversationId, isRegenerate }) {
  const response = await fetch(`${API_BASE}/generate/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt,
      conversation_id: conversationId ?? null,
      is_regenerate: Boolean(isRegenerate),
    }),
  });

  if (!response.ok || !response.body) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed with status ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop();

    for (const block of blocks) {
      const parsed = parseSSEBlock(block);
      if (parsed) yield parsed;
    }
  }
}
