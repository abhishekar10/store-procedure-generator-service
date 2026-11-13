# Sample output

`onboard_employee.sql` is one unedited response from the service, kept so
readers can see what a generation looks like without running anything.

| | |
|---|---|
| Request | `{"messages": [{"role": "user", "content": "Onboard a new employee and record their starting salary"}]}` to `POST /v1/generate` |
| Generated | 2026-11-14, `models/gemini-flash-latest` (then `gemini-3.8-flash`), thinking level `low` |
| Retrieved tables | salary_history, employees, payroll_entries, employee_deductions, payroll_runs |
| Structural check | `sql_valid: true`, no issues |

Checked by hand against PostgreSQL 16, with the reference schema and seed
data loaded (and identity columns added, since the SQLite schema's `INTEGER
PRIMARY KEY` auto-increments and a plain Postgres integer does not):

- The script compiles: both procedures are created.
- `CALL onboard_employee(11, 'Ada', ...)` inserts the employee as `active`
  and a `hire` salary record of 5000.
- A second call with the same email fails with "Employee with email
  ada@example.com already exists" and leaves no partial rows.
- `CALL rollback_onboard_employee(11)` removes both rows again.

This is one sample, checked once. It shows the shape of the output, not a
guarantee about every generation; the service's own `sql_valid` is a
best-effort structural check, not proof a script runs.
