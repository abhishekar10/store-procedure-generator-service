// Unset: a local backend. Set to "" (as the AWS build does): same origin, so
// the UI calls /v1/... on whatever host served it -- the ALB routes /v1/* to
// the API and everything else to the UI, and no CORS is involved.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

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
 * "status"   -> {stage}
 * "chunk"    -> {text}
 * "done"     -> {full_script, sections, retrieved_context, sql_valid, sql_issues}
 * "rejected" -> {reason}
 * "error"    -> {code, message, request_id}
 * `messages` is the conversation so far, ending with the new request (see
 * conversationStore.toApiMessages). Lines starting with ":" are heartbeats
 * sent while the model thinks; the parser skips them because they carry no data.
 */
export async function* streamGenerate(messages) {
  const response = await fetch(`${API_BASE}/v1/generate/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
  });

  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}));
    // The service returns {error: {code, message, request_id}}.
    throw new Error(body.error?.message || `Request failed with status ${response.status}`);
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
