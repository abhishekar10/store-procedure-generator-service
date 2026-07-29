import { useEffect, useState } from "react";
import CodeBlock from "./CodeBlock";
import ValidityBadge from "./ValidityBadge";
import RetrievedContextPanel from "./RetrievedContextPanel";
import { downloadScript } from "../utils";
import { STAGE_LABELS, DRAFTING_SUBPHRASES } from "../thinkingStages";

function ThinkingIndicator({ stage }) {
  const [subIndex, setSubIndex] = useState(0);

  useEffect(() => {
    if (stage !== "drafting_script") {
      setSubIndex(0);
      return undefined;
    }
    const interval = setInterval(() => {
      setSubIndex((i) => Math.min(i + 1, DRAFTING_SUBPHRASES.length - 1));
    }, 7000);
    return () => clearInterval(interval);
  }, [stage]);

  const label = stage === "drafting_script" ? DRAFTING_SUBPHRASES[subIndex] : STAGE_LABELS[stage] || STAGE_LABELS.processing;

  return (
    <div className="thinking-indicator">
      <span className="dot" />
      <span className="dot" />
      <span className="dot" />
      <span className="thinking-label">{label}</span>
    </div>
  );
}

export default function MessageBubble({ message, isLast, onRegenerate, isStreamActive, theme, conversationTitle }) {
  if (message.role === "user") {
    return (
      <div className="message message-user">
        <div className="bubble">{message.content}</div>
      </div>
    );
  }

  if (message.status === "error") {
    return (
      <div className="message message-assistant">
        <div className="error-box">Something went wrong generating this response: {message.errorMessage}</div>
      </div>
    );
  }

  if (message.status === "rejected") {
    return (
      <div className="message message-assistant">
        <div className="info-box">
          <strong>This doesn't look like a stored procedure request.</strong>
          <p>{message.content}</p>
        </div>
      </div>
    );
  }

  if (message.status === "thinking") {
    return (
      <div className="message message-assistant">
        <ThinkingIndicator stage={message.stage} />
      </div>
    );
  }

  return (
    <div className="message message-assistant">
      <CodeBlock code={message.full_script} theme={theme} />

      {message.status === "done" && (
        <>
          <div className="message-meta-row">
            <ValidityBadge valid={message.sql_valid} issues={message.sql_issues} />
            <button className="text-btn" onClick={() => downloadScript(conversationTitle, message.full_script)}>
              Download .sql
            </button>
            {isLast && !isStreamActive && (
              <button className="text-btn" onClick={onRegenerate}>
                ↻ Regenerate
              </button>
            )}
          </div>
          <RetrievedContextPanel retrievedContext={message.retrieved_context} />
        </>
      )}
    </div>
  );
}
