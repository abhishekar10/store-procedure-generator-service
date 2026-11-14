# Roadmap

What is planned after v1, and what each item changes. Nothing here is built.
v1 is the current service: PostgreSQL (PL/pgSQL) only, one fixed reference
schema, a stateless `/v1` API. Both planned features are additive, so they
can ship as new optional request fields under `/v1` without breaking
existing callers.

## v1.0.x -- fixes found in testing (next)

Found by a real request on the deployed demo (2026-11-15): a prompt asked
for MySQL and described a correction whose arithmetic was backwards. The
service silently produced PostgreSQL, and a script that -- run against the
seed data with one bad row -- set the deductions of all ten payroll entries
to negative values. It compiled, passed the structural check, and its own
post-validation did not notice. The manual rollback restored the data.

- **Say when a request asks for another database.** Until MySQL support
  lands, the classifier rejects requests for MySQL, SQL Server, Oracle and
  similar with a clear reason ("this service generates PostgreSQL; MySQL
  support is planned") instead of answering in PostgreSQL.
- **Post-validation checks the invariant the change restores** (here
  `gross_pay - total_deductions = net_pay`), not only that the update
  matched its own staging table.
- **Pre-validation refuses impossible results**: values that break evident
  rules (negative deductions or pay), or far more rows than the request
  describes. When the requested logic contradicts the data, the script says
  so in a comment at the top and stops, rather than quietly reinterpreting
  the request.
- **Eval cases** for both prompts, so this stays fixed.
- Optionally, compare thinking level `auto` with `low` on such prompts, to
  see whether more reasoning catches contradictions -- a quality/latency
  trade-off for the owner.

## v1.1 -- MySQL support

**API.** An optional `dialect` field on the request, `"postgresql"`
(default, today's behaviour) or `"mysql"`. Responses unchanged.

**What actually changes** -- most of it is not the prompt:

- **The section layout.** Today the automatic rollback is an `EXCEPTION`
  block at the *end* of the procedure. In MySQL the equivalent is
  `DECLARE EXIT HANDLER FOR SQLEXCEPTION`, which must be declared at the
  *top*, before any statement. So the seven sections cannot keep the same
  physical order for both dialects: either the section order becomes
  per-dialect, or ROLLBACK_AUTO is defined as "the error handling, wherever
  the dialect requires it". This decision comes first; the parser, the
  validator, the templates and the UI all follow from it.
- **A second pattern library**, `sp_templates/mysql/`, one template per
  section, written the way the PostgreSQL ones were: one worked example that
  assembles into a script that runs. Retrieval pulls patterns by dialect.
  MySQL idioms that differ: `DELIMITER` around the procedure,
  `START TRANSACTION` / `COMMIT` / `ROLLBACK` inside it, `SIGNAL SQLSTATE
  '45000'` for errors, `CREATE TEMPORARY TABLE` (no `ON COMMIT DROP`),
  `UPDATE ... JOIN` instead of `UPDATE ... FROM`, `ROW_COUNT()`.
- **A dialect-aware validator and section parser** (the `$$` and
  `CREATE PROCEDURE` rules are PostgreSQL-specific today).
- **The classifier** accepts MySQL requests.
- **Schema types.** The reference schema is SQLite-typed (dates are `TEXT`);
  the schema chunks given to the model would state types per dialect.
- **Verification.** MySQL golden prompts in the evals, and a check that
  compiles and calls each generated procedure in a throwaway MySQL
  container -- the same check done by hand for PostgreSQL so far.

## v1.2 -- your own schema, with a visual editor

Today the schema is fixed: the nine HR/payroll tables are built into the
image and embedded at build time. v1.2 lets a user describe their own
tables and generate against those.

**The design question is where an edited schema lives**, because the
service is stateless. Proposed:

- **The caller owns it, like conversation history.** The request gains an
  optional `schema` field (tables, columns, types, keys, short
  descriptions). When present, the service builds the schema chunks from it
  for that request instead of from the built-in corpus. Embedding a
  ten-table schema is about a second; results can be cached by a hash of
  the schema, so a conversation does not re-embed it every turn. No new
  database, and every task stays identical.
- The alternative -- the service storing schemas per user -- needs
  authentication, a writable store and a new `SchemaSource`/`VectorStore`
  pair. The adapters make that possible later, but it is a much bigger step
  and not needed for the editor.

**The UI.** A schema view showing tables and their relationships, where a
user can add, rename and remove tables and columns and draw foreign keys;
import from `CREATE TABLE` DDL, export back to DDL. Kept in the browser
(localStorage) next to the conversations, and sent with each request. The
built-in HR/payroll schema stays as the default and as a starting template.

**Limits to set:** maximum tables and columns per request (body size and
prompt length), and validation of the schema itself (unique names,
foreign keys that point at real columns).

## Later, not scheduled

- Authentication at the gateway, then per-user quotas.
- CloudFront in front of the ALB as a CDN, once the AWS account is verified
  for it.
