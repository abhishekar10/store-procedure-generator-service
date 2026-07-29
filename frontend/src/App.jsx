import { useEffect, useState } from "react";
import Sidebar from "./components/Sidebar";
import ChatWindow from "./components/ChatWindow";
import ChatInput from "./components/ChatInput";
import { listConversations, getConversation, deleteConversation, streamGenerate } from "./api";

function loadInitialTheme() {
  const saved = localStorage.getItem("theme");
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export default function App() {
  const [theme, setTheme] = useState(loadInitialTheme);
  const [conversations, setConversations] = useState([]);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [isStreamActive, setIsStreamActive] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);

  useEffect(() => {
    refreshConversations();
  }, []);

  const refreshConversations = async () => {
    try {
      setConversations(await listConversations());
    } catch {
      // backend not reachable yet -- sidebar just stays empty
    }
  };

  const handleNewChat = () => {
    setActiveConversationId(null);
    setMessages([]);
    setIsSidebarOpen(false);
  };

  const handleSelectConversation = async (conversationId) => {
    const conversation = await getConversation(conversationId);
    setActiveConversationId(conversation.id);
    setMessages(
      conversation.messages.map((m) =>
        m.role === "assistant" ? { ...m, status: m.kind === "rejected" ? "rejected" : "done" } : m
      )
    );
    setIsSidebarOpen(false);
  };

  const handleDeleteConversation = async (conversation) => {
    if (!window.confirm(`Delete "${conversation.title}"? This can't be undone.`)) return;

    await deleteConversation(conversation.id);
    setConversations((prev) => prev.filter((c) => c.id !== conversation.id));

    if (conversation.id === activeConversationId) {
      setActiveConversationId(null);
      setMessages([]);
    }
  };

  const runStream = async (assistantMessageId, streamArgs) => {
    setIsStreamActive(true);
    try {
      for await (const { event, data } of streamGenerate(streamArgs)) {
        if (event === "start") {
          setActiveConversationId((current) => current ?? data.conversation_id);
          refreshConversations();
        } else if (event === "status") {
          setMessages((prev) => prev.map((m) => (m.id === assistantMessageId ? { ...m, stage: data.stage } : m)));
        } else if (event === "chunk") {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId ? { ...m, status: "streaming", full_script: m.full_script + data.text } : m
            )
          );
        } else if (event === "done") {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMessageId
                ? {
                    ...m,
                    status: "done",
                    full_script: data.full_script,
                    sections: data.sections,
                    retrieved_context: data.retrieved_context,
                    sql_valid: data.sql_valid,
                    sql_issues: data.sql_issues,
                  }
                : m
            )
          );
          refreshConversations();
        } else if (event === "rejected") {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantMessageId ? { ...m, status: "rejected", content: data.reason } : m))
          );
          refreshConversations();
        } else if (event === "error") {
          setMessages((prev) =>
            prev.map((m) => (m.id === assistantMessageId ? { ...m, status: "error", errorMessage: data.message } : m))
          );
        }
      }
    } catch (err) {
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantMessageId ? { ...m, status: "error", errorMessage: err.message } : m))
      );
    } finally {
      setIsStreamActive(false);
    }
  };

  const handleSend = (promptText) => {
    const userMessage = { id: crypto.randomUUID(), role: "user", content: promptText };
    const assistantMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      full_script: "",
      status: "thinking",
      stage: "processing",
    };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    runStream(assistantMessage.id, { prompt: promptText, conversationId: activeConversationId });
  };

  const handleRegenerate = () => {
    if (!activeConversationId) return;
    const assistantMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      full_script: "",
      status: "thinking",
      stage: "processing",
    };
    setMessages((prev) => [...prev, assistantMessage]);
    runStream(assistantMessage.id, { prompt: "", conversationId: activeConversationId, isRegenerate: true });
  };

  const activeConversation = conversations.find((c) => c.id === activeConversationId);
  const conversationTitle = activeConversation?.title || messages.find((m) => m.role === "user")?.content || "stored_procedure";

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
