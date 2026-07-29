import { useEffect, useRef } from "react";
import MessageBubble from "./MessageBubble";
import StarterPrompts from "./StarterPrompts";

export default function ChatWindow({ messages, onPickStarter, onRegenerate, isStreamActive, theme, conversationTitle }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="chat-window chat-window-empty">
        <StarterPrompts onPick={onPickStarter} />
      </div>
    );
  }

  return (
    <div className="chat-window">
      <div className="messages">
        {messages.map((message, index) => (
          <MessageBubble
            key={message.id}
            message={message}
            isLast={index === messages.length - 1}
            onRegenerate={onRegenerate}
            isStreamActive={isStreamActive}
            theme={theme}
            conversationTitle={conversationTitle}
          />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
