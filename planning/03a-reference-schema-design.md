# Reference Schema Design — HR / Payroll (`db/reference.db`)

A concrete starting point for the metadata-only SQLite reference DB. Column
types are written SQLite-style; feel free to adjust names/types as you author
the actual seed script — nothing here needs to match exactly.

## Tables

### `departments`
| column | type | notes |
|---|---|---|
| department_id | INTEGER PK | |
| department_name | TEXT | |
| location | TEXT | |
| manager_employee_id | INTEGER, FK → employees.employee_id, nullable | department head |

### `employees`
| column | type | notes |
|---|---|---|
| employee_id | INTEGER PK | |
| first_name / last_name | TEXT | |
| email | TEXT, unique | |
| hire_date | DATE | |
| job_title | TEXT | |
| department_id | INTEGER, FK → departments | |
| manager_id | INTEGER, FK → employees, nullable | self-referential reporting line |
| employment_status | TEXT | `active` / `on_leave` / `terminated` |
| termination_date | DATE, nullable | |

### `salary_history`
| column | type | notes |
|---|---|---|
| salary_id | INTEGER PK | |
| employee_id | INTEGER, FK → employees | |
| effective_date | DATE | |
| gross_monthly_salary | NUMERIC | |
| currency | TEXT | |
| reason | TEXT | `hire` / `raise` / `promotion` / `adjustment` |

### `timesheets`
| column | type | notes |
|---|---|---|
| timesheet_id | INTEGER PK | |
| employee_id | INTEGER, FK → employees | |
| work_date | DATE | |
| hours_worked | NUMERIC | |
| overtime_hours | NUMERIC | |
| status | TEXT | `pending` / `approved` / `rejected` |
| approved_by | INTEGER, FK → employees, nullable | |

### `leave_requests`
| column | type | notes |
|---|---|---|
| leave_id | INTEGER PK | |
| employee_id | INTEGER, FK → employees | |
| leave_type | TEXT | `sick` / `vacation` / `unpaid` |
| start_date / end_date | DATE | |
| status | TEXT | `pending` / `approved` / `rejected` |

### `deduction_types`
| column | type | notes |
|---|---|---|
| deduction_type_id | INTEGER PK | |
| name | TEXT | e.g. `federal_tax`, `health_insurance`, `retirement_401k` |
| calculation_type | TEXT | `flat` / `percentage` |
| default_value | NUMERIC | |

### `employee_deductions`
| column | type | notes |
|---|---|---|
| employee_deduction_id | INTEGER PK | |
| employee_id | INTEGER, FK → employees | |
| deduction_type_id | INTEGER, FK → deduction_types | |
| value | NUMERIC, nullable | overrides `deduction_types.default_value` if set |
| active | BOOLEAN | |

### `payroll_runs`
| column | type | notes |
|---|---|---|
| payroll_run_id | INTEGER PK | |
| pay_period_start / pay_period_end | DATE | |
| run_date | DATE | |
| status | TEXT | `draft` / `processing` / `finalized` / `rolled_back` |

### `payroll_entries`
| column | type | notes |
|---|---|---|
| payroll_entry_id | INTEGER PK | |
| payroll_run_id | INTEGER, FK → payroll_runs | |
| employee_id | INTEGER, FK → employees | |
| gross_pay | NUMERIC | |
| total_deductions | NUMERIC | |
| net_pay | NUMERIC | |
| status | TEXT | `calculated` / `paid` / `reversed` |

## Why this shape

Nine tables is enough that a given prompt should only pull a *subset* into
retrieved context — which is what actually exercises schema retrieval,
rather than every generation just seeing the whole DB. The FK chain from
`payroll_runs` down through `payroll_entries` / `employees` /
`salary_history` / `employee_deductions` / `timesheets` gives multi-table
procedures real justification for pre-validation, backup, and rollback,
rather than those sections feeling bolted on to a single-table update.

## Candidate prompts for testing retrieval quality

Worth keeping a short list like this once the pipeline is up, to sanity-check
that retrieval pulls the right table subset per prompt rather than
overfetching everything every time:

- "Process payroll for a given pay period" — expected to touch
  `payroll_runs`, `payroll_entries`, `employees`, `salary_history`,
  `timesheets`, `employee_deductions`, `deduction_types`.
- "Onboard a new employee and record their starting salary" — expected to
  touch `employees`, `departments`, `salary_history`.
- "Approve a timesheet and recalculate overtime pay" — expected to touch
  `timesheets`, `employees`, `salary_history`.
- "Terminate an employee and reverse their pending payroll entry" — expected
  to touch `employees`, `payroll_entries`, `payroll_runs`.

If a narrow prompt like the onboarding one starts pulling in payroll tables
too, that's a signal the schema chunk descriptions need sharper per-table
"purpose" text rather than a retrieval top-k tweak.

## Sample data

A handful of rows per table (5–10 employees across 2–3 departments, a couple
of payroll runs, a mix of approved/pending timesheets) is probably enough —
the DB is never executed against, so its job is just to give the LLM
realistic value shapes, not to stress-test anything.
