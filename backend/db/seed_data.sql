-- Departments (manager_employee_id is assigned after employees are inserted,
-- to avoid a circular foreign key on first insert)
INSERT INTO departments (department_id, department_name, location, manager_employee_id) VALUES
    (1, 'Engineering', 'Bengaluru', NULL),
    (2, 'Sales', 'Mumbai', NULL),
    (3, 'Human Resources', 'Bengaluru', NULL);

-- Employees
INSERT INTO employees (employee_id, first_name, last_name, email, hire_date, job_title, department_id, manager_id, employment_status, termination_date) VALUES
    (1, 'Alice', 'Johnson', 'alice.johnson@example.com', '2019-01-15', 'CTO', 1, NULL, 'active', NULL),
    (2, 'Brian', 'Lee', 'brian.lee@example.com', '2019-06-01', 'Engineering Manager', 1, 1, 'active', NULL),
    (3, 'Carla', 'Smith', 'carla.smith@example.com', '2020-03-10', 'Software Engineer', 1, 2, 'active', NULL),
    (4, 'David', 'Kim', 'david.kim@example.com', '2021-02-22', 'Software Engineer', 1, 2, 'active', NULL),
    (5, 'Emma', 'Davis', 'emma.davis@example.com', '2018-11-05', 'VP Sales', 2, NULL, 'active', NULL),
    (6, 'Frank', 'Wright', 'frank.wright@example.com', '2019-09-17', 'Sales Manager', 2, 5, 'active', NULL),
    (7, 'Grace', 'Chen', 'grace.chen@example.com', '2022-04-01', 'Sales Representative', 2, 6, 'active', NULL),
    (8, 'Henry', 'Patel', 'henry.patel@example.com', '2018-07-30', 'HR Director', 3, NULL, 'active', NULL),
    (9, 'Isabella', 'Garcia', 'isabella.garcia@example.com', '2021-10-12', 'HR Specialist', 3, 8, 'active', NULL),
    (10, 'Jack', 'Wilson', 'jack.wilson@example.com', '2020-08-19', 'Software Engineer', 1, 2, 'terminated', '2025-12-01');

-- Now that employees exist, assign each department's manager
UPDATE departments SET manager_employee_id = 1 WHERE department_id = 1;
UPDATE departments SET manager_employee_id = 5 WHERE department_id = 2;
UPDATE departments SET manager_employee_id = 8 WHERE department_id = 3;

-- Salary history
INSERT INTO salary_history (salary_id, employee_id, effective_date, gross_monthly_salary, currency, reason) VALUES
    (1, 1, '2019-01-15', 16000, 'USD', 'hire'),
    (2, 2, '2019-06-01', 11000, 'USD', 'hire'),
    (3, 2, '2023-06-01', 12500, 'USD', 'raise'),
    (4, 3, '2020-03-10', 8500, 'USD', 'hire'),
    (5, 4, '2021-02-22', 8200, 'USD', 'hire'),
    (6, 5, '2018-11-05', 14000, 'USD', 'hire'),
    (7, 6, '2019-09-17', 9500, 'USD', 'hire'),
    (8, 7, '2022-04-01', 6000, 'USD', 'hire'),
    (9, 8, '2018-07-30', 10500, 'USD', 'hire'),
    (10, 9, '2021-10-12', 6500, 'USD', 'hire'),
    (11, 10, '2020-08-19', 8000, 'USD', 'hire');

-- Timesheets for the in-progress June pay period
INSERT INTO timesheets (timesheet_id, employee_id, work_date, hours_worked, overtime_hours, status, approved_by) VALUES
    (1, 3, '2026-06-15', 8, 0, 'approved', 2),
    (2, 3, '2026-06-16', 9, 1, 'approved', 2),
    (3, 4, '2026-06-15', 8, 0, 'approved', 2),
    (4, 4, '2026-06-16', 8, 0, 'pending', NULL),
    (5, 7, '2026-06-15', 8, 0, 'approved', 6),
    (6, 9, '2026-06-15', 8, 0, 'approved', 8);

-- Leave requests
INSERT INTO leave_requests (leave_id, employee_id, leave_type, start_date, end_date, status) VALUES
    (1, 4, 'vacation', '2026-07-10', '2026-07-14', 'approved'),
    (2, 7, 'sick', '2026-06-20', '2026-06-21', 'approved'),
    (3, 9, 'unpaid', '2026-08-01', '2026-08-03', 'pending');

-- Deduction types
INSERT INTO deduction_types (deduction_type_id, name, calculation_type, default_value) VALUES
    (1, 'federal_tax', 'percentage', 0.15),
    (2, 'health_insurance', 'flat', 200),
    (3, 'retirement_401k', 'percentage', 0.05);

-- Employee deductions (federal tax applies to everyone; health insurance and
-- 401k are opted into individually)
INSERT INTO employee_deductions (employee_deduction_id, employee_id, deduction_type_id, value, active) VALUES
    (1, 1, 1, NULL, 1),
    (2, 1, 2, NULL, 1),
    (3, 2, 1, NULL, 1),
    (4, 2, 2, NULL, 1),
    (5, 2, 3, 0.08, 1),
    (6, 3, 1, NULL, 1),
    (7, 3, 2, NULL, 1),
    (8, 4, 1, NULL, 1),
    (9, 5, 1, NULL, 1),
    (10, 5, 2, NULL, 1),
    (11, 6, 1, NULL, 1),
    (12, 7, 1, NULL, 1),
    (13, 8, 1, NULL, 1),
    (14, 9, 1, NULL, 1),
    (15, 10, 1, NULL, 0);

-- Payroll runs: one finalized, one still processing
INSERT INTO payroll_runs (payroll_run_id, pay_period_start, pay_period_end, run_date, status) VALUES
    (1, '2026-05-01', '2026-05-31', '2026-06-02', 'finalized'),
    (2, '2026-06-01', '2026-06-30', '2026-07-02', 'processing');

-- Payroll entries for the finalized May run
INSERT INTO payroll_entries (payroll_entry_id, payroll_run_id, employee_id, gross_pay, total_deductions, net_pay, status) VALUES
    (1, 1, 1, 16000, 3200, 12800, 'paid'),
    (2, 1, 2, 12500, 2625, 9875, 'paid'),
    (3, 1, 3, 8500, 1700, 6800, 'paid'),
    (4, 1, 4, 8200, 1230, 6970, 'paid'),
    (5, 1, 5, 14000, 2800, 11200, 'paid'),
    (6, 1, 6, 9500, 1425, 8075, 'paid'),
    (7, 1, 7, 6000, 900, 5100, 'paid'),
    (8, 1, 8, 10500, 1575, 8925, 'paid'),
    (9, 1, 9, 6500, 975, 5525, 'paid');
