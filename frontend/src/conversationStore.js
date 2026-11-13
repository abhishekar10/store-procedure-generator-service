// Conversation history lives in the browser: the service is stateless and
// takes the conversation so far with every request. Nothing here leaves the
// browser except through api.js.
//
// Every storage access is guarded. Private windows, blocked site data and a
// full quota all make localStorage throw; the app then keeps working with
// in-memory history that simply isn't saved.

const STORAGE_KEY = "sp-generator.conversations.v1";

export function loadConversations() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter(isConversation) : [];
  } catch {
    return [];
  }
}

/** Saves newest-first `conversations`. If the quota is exceeded, drops the
 * oldest conversations until the rest fit. Returns how many were saved. */
export function saveConversations(conversations) {
  for (let keep = conversations.length; keep >= 0; keep -= 1) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.slice(0, keep)));
      return keep;
    } catch {
      // quota exceeded (try fewer) or storage unavailable (all attempts fail)
    }
  }
  return 0;
}

export function newConversation(firstPrompt) {
  return {
    id: crypto.randomUUID(),
    title: firstPrompt.trim().slice(0, 80),
    created_at: new Date().toISOString(),
    messages: [],
  };
}

/** The request body's `messages`: only what the service accepts. */
export function toApiMessages(messages) {
  return messages.map((m) => {
    if (m.role === "user") return { role: "user", content: m.content };
    if (m.kind === "rejected") return { role: "assistant", kind: "rejected", content: m.content };
    return { role: "assistant", kind: "generation", full_script: m.full_script };
  });
}

function isConversation(value) {
  return value && typeof value.id === "string" && Array.isArray(value.messages);
}
