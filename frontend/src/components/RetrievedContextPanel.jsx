import { useState } from "react";

export default function RetrievedContextPanel({ retrievedContext }) {
  const [expanded, setExpanded] = useState(false);
  const schemaChunks = retrievedContext?.schema_chunks ?? [];
  const patternChunks = retrievedContext?.pattern_chunks ?? [];

  if (schemaChunks.length === 0 && patternChunks.length === 0) return null;

  return (
    <div className="context-panel">
      <button className="context-panel-toggle" onClick={() => setExpanded((v) => !v)}>
        {expanded ? "▾" : "▸"} Grounded in {schemaChunks.length} table{schemaChunks.length === 1 ? "" : "s"} and{" "}
        {patternChunks.length} pattern{patternChunks.length === 1 ? "" : "s"}
      </button>

      <div className="context-pills">
        {schemaChunks.map((chunk) => (
          <span key={chunk.id} className="pill pill-schema">
            {chunk.metadata.table_name}
          </span>
        ))}
        {patternChunks.map((chunk) => (
          <span key={chunk.id} className="pill pill-pattern">
            {chunk.metadata.category}
          </span>
        ))}
      </div>

      {expanded && (
        <div className="context-details">
          {schemaChunks.map((chunk) => (
            <details key={chunk.id} className="context-detail-item">
              <summary>{chunk.metadata.table_name}</summary>
              <pre>{chunk.text}</pre>
            </details>
          ))}
          {patternChunks.map((chunk) => (
            <details key={chunk.id} className="context-detail-item">
              <summary>{chunk.metadata.category}</summary>
              <pre>{chunk.text}</pre>
            </details>
          ))}
        </div>
      )}
    </div>
  );
}
