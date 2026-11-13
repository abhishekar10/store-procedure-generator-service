// Labels for the real backend pipeline stages (see the "status" SSE event
// in backend/app/rag/generator.py::stream_stored_procedure).
export const STAGE_LABELS = {
  processing: "Processing your request…",
  checking_request: "Checking this is a stored procedure request…",
  retrieving_context: "Looking for relevant tables and patterns in the DB…",
  drafting_script: "Scripting the stored procedure…",
  validating_script: "Validating the generated script…",
};

// "drafting_script" is the long step (Gemini's reasoning time before the
// first token, often 30-60s) with no further backend signal until text
// starts streaming. Cycle through what's actually happening during that
// step -- the seven sections the model is producing -- so the wait reads as
// progress instead of a stuck label.
export const DRAFTING_SUBPHRASES = [
  "Scripting the stored procedure…",
  "Working out pre-validation checks…",
  "Structuring temp tables and backup steps…",
  "Writing the main script…",
  "Adding post-validation and rollback handling…",
  "Finalizing the script…",
];
