import { useEffect, useState } from "react";
import Sidebar from "./components/Sidebar";
import ChatWindow from "./components/ChatWindow";
import ChatInput from "./components/ChatInput";
import { streamGenerate } from "./api";
import { loadConversations, newConversation, saveConversations, toApiMessages } from "./conversationStore";

function loadInitialTheme() {
  try {
    const saved = localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // storage unavailable (private window, blocked site data): use the OS preference
  }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

// Saved messages carry no UI status; MessageBubble wants one.
const withStatus = (m) => (m.role === "assistant" ? { ...m, status: m.kind === "rejected" ? "rejected" : "done" } : m);

export default function App() {
  const [theme, setTheme] = useState(loadInitialTheme);
  const [conversations, setConversations] = useState(loadConversations);
  const [activeConversationId, setActiveConversationId] = useState(null);
  // The reply being generated: {conversationId, replacesLast, message}. It
  // joins its conversation only once it is done or rejected, so a failed or
  // half-streamed reply is never saved or sent back to the service.
  const [pending, setPending] = useState(null);
  const [isStreamActive, setIsStreamActive] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("theme", theme);
    } catch {
      // not saved; the choice still applies for this session
    }
  }, [theme]);

  useEffect(() => {
    saveConversations(conversations);
  }, [conversations]);

  const activeConversation = conversations.find((c) => c.id === activeConversationId);
  const savedMessages = activeConversation?.messages ?? [];
  const pendingHere = pending && pending.conversationId === activeConversationId ? pending : null;
  // A regeneration hides the answer it replaces -- unless it failed, in which
  // case the old answer is still the latest good one.
  const hidesLast = pendingHere?.replacesLast && pendingHere.message.status !== "error";
  const messages = [
    ...(hidesLast ? savedMessages.slice(0, -1) : savedMessages).map(withStatus),
    ...(pendingHere ? [pendingHere.message] : []),
  ];

  const updateConversation = (conversationId, update) =>
    setConversations((prev) => prev.map((c) => (c.id === conversationId ? update(c) : c)));

  const updatePendingMessage = (update) =>
    setPending((p) => (p ? { ...p, message: { ...p.message, ...update(p.message) } } : p));

  const runStream = async (conversationId, history, replacesLast) => {
    setPending({
      conversationId,
      replacesLast,
      message: { id: crypto.randomUUID(), role: "assistant", status: "thinking", stage: "processing", full_script: "" },
    });
    setIsStreamActive(true);
    let settled = false;

    const fail = (errorMessage) => {
      settled = true;
      updatePendingMessage(() => ({ status: "error", errorMessage }));
    };

    try {
      for await (const { event, data } of streamGenerate(toApiMessages(history))) {
        if (event === "status") {
          updatePendingMessage(() => ({ stage: data.stage }));
        } else if (event === "chunk") {
          updatePendingMessage((m) => ({ status: "streaming", full_script: m.full_script + data.text }));
        } else if (event === "done" || event === "rejected") {
          settled = true;
          const reply =
            event === "done"
              ? {
                  id: crypto.randomUUID(),
                  role: "assistant",
                  kind: "generation",
                  full_script: data.full_script,
                  retrieved_context: data.retrieved_context,
                  sql_valid: data.sql_valid,
                  sql_issues: data.sql_issues,
                }
              : { id: crypto.randomUUID(), role: "assistant", kind: "rejected", content: data.reason };
          updateConversation(conversationId, (c) => ({
            ...c,
            messages: [...(replacesLast ? c.messages.slice(0, -1) : c.messages), reply],
          }));
          setPending(null);
        } else if (event === "error") {
          fail(data.message);
        }
      }
      if (!settled) fail("The connection closed before the script was finished.");
    } catch (err) {
      fail(err.message);
    } finally {
      setIsStreamActive(false);
    }
  };

  const handleSend = (promptText) => {
    const userMessage = { id: crypto.randomUUID(), role: "user", content: promptText };

    if (!activeConversation) {
      const conversation = { ...newConversation(promptText), messages: [userMessage] };
      setConversations((prev) => [conversation, ...prev]);
      setActiveConversationId(conversation.id);
      runStream(conversation.id, conversation.messages, false);
      return;
    }

    updateConversation(activeConversation.id, (c) => ({ ...c, messages: [...c.messages, userMessage] }));
    runStream(activeConversation.id, [...savedMessages, userMessage], false);
  };

  // Regenerating is resending the conversation without its last answer.
  const handleRegenerate = () => {
    if (!activeConversation || isStreamActive || savedMessages.at(-1)?.role !== "assistant") return;
    runStream(activeConversation.id, savedMessages.slice(0, -1), true);
  };

  const handleNewChat = () => {
    setActiveConversationId(null);
    setIsSidebarOpen(false);
  };

  const handleSelectConversation = (conversationId) => {
    setActiveConversationId(conversationId);
    setIsSidebarOpen(false);
  };

  const handleDeleteConversation = (conversation) => {
    if (!window.confirm(`Delete "${conversation.title}"? This can't be undone.`)) return;

    setConversations((prev) => prev.filter((c) => c.id !== conversation.id));
    if (conversation.id === activeConversationId) setActiveConversationId(null);
  };

  const conversationTitle = activeConversation?.title || "stored_procedure";

  return (
    <div className="app">
      {isSidebarOpen && <div className="sidebar-backdrop" onClick={() => setIsSidebarOpen(false)} />}
      <Sidebar
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelect={handleSelectConversation}
        onDelete={handleDeleteConversation}
        onNewChat={handleNewChat}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        isOpen={isSidebarOpen}
      />
      <main className="main">
        <div className="mobile-topbar">
          <button className="hamburger-btn" onClick={() => setIsSidebarOpen(true)} aria-label="Open conversation list">
            ☰
          </button>
          <span className="mobile-topbar-title">{activeConversation ? conversationTitle : "New chat"}</span>
        </div>
        <ChatWindow
          messages={messages}
          onPickStarter={handleSend}
          onRegenerate={handleRegenerate}
          isStreamActive={isStreamActive}
          theme={theme}
          conversationTitle={conversationTitle}
        />
        <ChatInput onSend={handleSend} disabled={isStreamActive} />
      </main>
    </div>
  );
}
