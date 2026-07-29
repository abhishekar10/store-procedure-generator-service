import { formatRelativeTime } from "../utils";
import ThemeToggle from "./ThemeToggle";

export default function Sidebar({
  conversations,
  activeConversationId,
  onSelect,
  onDelete,
  onNewChat,
  theme,
  onToggleTheme,
  isOpen,
}) {
  return (
    <aside className={`sidebar ${isOpen ? "open" : ""}`}>
      <div className="sidebar-header">
        <button className="new-chat-btn" onClick={onNewChat}>
          + New chat
        </button>
      </div>

      <nav className="conversation-list">
        {conversations.length === 0 && <p className="conversation-list-empty">No conversations yet</p>}
        {conversations.map((conversation) => (
          <div
            key={conversation.id}
            className={`conversation-item ${conversation.id === activeConversationId ? "active" : ""}`}
            onClick={() => onSelect(conversation.id)}
            onKeyDown={(e) => e.key === "Enter" && onSelect(conversation.id)}
            role="button"
            tabIndex={0}
          >
            <div className="conversation-item-main">
              <span className="conversation-title">{conversation.title}</span>
              <span className="conversation-meta">{formatRelativeTime(conversation.created_at)}</span>
            </div>
            <button
              className="conversation-delete-btn"
              title="Delete conversation"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(conversation);
              }}
            >
              🗑
            </button>
          </div>
        ))}
      </nav>

      <div className="sidebar-footer">
        <span className="sidebar-footer-label">RAG Stored Procedure Generator</span>
        <ThemeToggle theme={theme} onToggle={onToggleTheme} />
      </div>
    </aside>
  );
}
