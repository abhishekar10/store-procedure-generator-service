PRAGMA foreign_keys = ON;

CREATE TABLE departments (
    department_id       INTEGER PRIMARY KEY,
    department_name     TEXT NOT NULL,
    location             TEXT,
    manager_employee_id INTEGER,
    FOREIGN KEY (manager_employee_id) REFERENCES employees (employee_id)
);

CREATE TABLE employees (
    employee_id         INTEGER PRIMARY KEY,
    first_name           TEXT NOT NULL,
    last_name             TEXT NOT NULL,
    email                 TEXT NOT NULL UNIQUE,
    hire_date             TEXT NOT NULL,
    job_title             TEXT NOT NULL,
    department_id         INTEGER NOT NULL,
    manager_id             INTEGER,
    employment_status     TEXT NOT NULL CHECK (employment_status IN ('active', 'on_leave', 'terminated')),
    termination_date       TEXT,
    FOREIGN KEY (department_id) REFERENCES departments (department_id),
    FOREIGN KEY (manager_id) REFERENCES employees (employee_id)
);

CREATE TABLE salary_history (
    salary_id             INTEGER PRIMARY KEY,
    employee_id           INTEGER NOT NULL,
    effective_date         TEXT NOT NULL,
    gross_monthly_salary   NUMERIC NOT NULL,
    currency                TEXT NOT NULL DEFAULT 'USD',
    reason                  TEXT NOT NULL CHECK (reason IN ('hire', 'raise', 'promotion', 'adjustment')),
    FOREIGN KEY (employee_id) REFERENCES employees (employee_id)
);

CREATE TABLE timesheets (
    timesheet_id       INTEGER PRIMARY KEY,
    employee_id         INTEGER NOT NULL,
    work_date             TEXT NOT NULL,
    hours_worked           NUMERIC NOT NULL,
    overtime_hours         NUMERIC NOT NULL DEFAULT 0,
    status                   TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    approved_by             INTEGER,
    FOREIGN KEY (employee_id) REFERENCES employees (employee_id),
    FOREIGN KEY (approved_by) REFERENCES employees (employee_id)
);

CREATE TABLE leave_requests (
    leave_id         INTEGER PRIMARY KEY,
    employee_id       INTEGER NOT NULL,
    leave_type         TEXT NOT NULL CHECK (leave_type IN ('sick', 'vacation', 'unpaid')),
    start_date           TEXT NOT NULL,
    end_date               TEXT NOT NULL,
    status                   TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    FOREIGN KEY (employee_id) REFERENCES employees (employee_id)
);

CREATE TABLE deduction_types (
    deduction_type_id   INTEGER PRIMARY KEY,
    name                   TEXT NOT NULL UNIQUE,
    calculation_type       TEXT NOT NULL CHECK (calculation_type IN ('flat', 'percentage')),
    default_value           NUMERIC NOT NULL
);

CREATE TABLE employee_deductions (
    employee_deduction_id   INTEGER PRIMARY KEY,
    employee_id               INTEGER NOT NULL,
    deduction_type_id         INTEGER NOT NULL,
    value                       NUMERIC,
    active                       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    FOREIGN KEY (employee_id) REFERENCES employees (employee_id),
    FOREIGN KEY (deduction_type_id) REFERENCES deduction_types (deduction_type_id)
);

CREATE TABLE payroll_runs (
    payroll_run_id     INTEGER PRIMARY KEY,
    pay_period_start     TEXT NOT NULL,
    pay_period_end         TEXT NOT NULL,
    run_date                 TEXT NOT NULL,
    status                     TEXT NOT NULL CHECK (status IN ('draft', 'processing', 'finalized', 'rolled_back'))
);

CREATE TABLE payroll_entries (
    payroll_entry_id   INTEGER PRIMARY KEY,
    payroll_run_id       INTEGER NOT NULL,
    employee_id           INTEGER NOT NULL,
    gross_pay               NUMERIC NOT NULL,
    total_deductions         NUMERIC NOT NULL,
    net_pay                   NUMERIC NOT NULL,
    status                     TEXT NOT NULL CHECK (status IN ('calculated', 'paid', 'reversed')),
    FOREIGN KEY (payroll_run_id) REFERENCES payroll_runs (payroll_run_id),
    FOREIGN KEY (employee_id) REFERENCES employees (employee_id)
);

CREATE INDEX idx_employees_department_id ON employees (department_id);
CREATE INDEX idx_employees_manager_id ON employees (manager_id);
CREATE INDEX idx_salary_history_employee_id ON salary_history (employee_id);
CREATE INDEX idx_timesheets_employee_id ON timesheets (employee_id);
CREATE INDEX idx_leave_requests_employee_id ON leave_requests (employee_id);
CREATE INDEX idx_employee_deductions_employee_id ON employee_deductions (employee_id);
CREATE INDEX idx_payroll_entries_payroll_run_id ON payroll_entries (payroll_run_id);
CREATE INDEX idx_payroll_entries_employee_id ON payroll_entries (employee_id);
