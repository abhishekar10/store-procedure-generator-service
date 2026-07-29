import { STARTER_PROMPTS } from "../starterPrompts";

export default function StarterPrompts({ onPick }) {
  return (
    <div className="starter-prompts">
      <h1 className="starter-title">What stored procedure do you need?</h1>
      <p className="starter-subtitle">
        Describe it in plain English. Grounded in the HR/payroll reference schema and a house-style pattern library.
      </p>
      <div className="starter-grid">
        {STARTER_PROMPTS.map((prompt) => (
          <button key={prompt} className="starter-card" onClick={() => onPick(prompt)}>
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}
