-- Pattern: TEMP_TABLES
-- Stage computed results in a temp table before writing to permanent
-- tables, so validation can run against the staged data before it commits.

CREATE TEMP TABLE IF NOT EXISTS tmp_payroll_calc (
    employee_id       INTEGER PRIMARY KEY,
    gross_pay         NUMERIC NOT NULL,
    total_deductions  NUMERIC NOT NULL,
    net_pay           NUMERIC NOT NULL
) ON COMMIT DROP;

INSERT INTO tmp_payroll_calc (employee_id, gross_pay, total_deductions, net_pay)
SELECT
    e.employee_id,
    sh.gross_monthly_salary AS gross_pay,
    COALESCE(SUM(
        CASE ed.calculation_type
            WHEN 'flat' THEN COALESCE(ed.value, dt.default_value)
            WHEN 'percentage' THEN sh.gross_monthly_salary * COALESCE(ed.value, dt.default_value)
        END
    ), 0) AS total_deductions,
    sh.gross_monthly_salary - COALESCE(SUM(
        CASE ed.calculation_type
            WHEN 'flat' THEN COALESCE(ed.value, dt.default_value)
            WHEN 'percentage' THEN sh.gross_monthly_salary * COALESCE(ed.value, dt.default_value)
        END
    ), 0) AS net_pay
FROM employees e
JOIN LATERAL (
    SELECT gross_monthly_salary
    FROM salary_history
    WHERE employee_id = e.employee_id
    ORDER BY effective_date DESC
    LIMIT 1
) sh ON TRUE
LEFT JOIN employee_deductions ed ON ed.employee_id = e.employee_id AND ed.active = 1
LEFT JOIN deduction_types dt ON dt.deduction_type_id = ed.deduction_type_id
WHERE e.employment_status = 'active'
GROUP BY e.employee_id, sh.gross_monthly_salary;
