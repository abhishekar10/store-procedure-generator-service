import { useRef, useState } from "react";

export default function ChatInput({ onSend, disabled }) {
  const [value, setValue] = useState("");
  const textareaRef = useRef(null);

  const handleInput = (event) => {
    setValue(event.target.value);
    const textarea = textareaRef.current;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  };

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setValue("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  const handleKeyDown = (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="chat-input-bar">
      <div className="chat-input-box">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? "Waiting for the current response..." : "Describe the stored procedure you need..."}
          disabled={disabled}
          rows={1}
        />
        <button className="send-btn" onClick={submit} disabled={disabled || !value.trim()}>
          Send
        </button>
      </div>
      <p className="chat-input-hint">Enter to send · Shift+Enter for a new line</p>
    </div>
  );
}
