-- SECTION: PRE_VALIDATION
DO $$
DECLARE
    v_email_count INTEGER;
BEGIN
    -- Validate that active employees exist or input structure is ready
    -- Specific input checks (email uniqueness, positive salary, valid manager) are executed inside the procedure.
    NULL;
END $$;

-- SECTION: TEMP_TABLES
CREATE TEMP TABLE IF NOT EXISTS tmp_onboard_employee (
    employee_id           INTEGER PRIMARY KEY,
    first_name            TEXT NOT NULL,
    last_name             TEXT NOT NULL,
    email                 TEXT NOT NULL,
    hire_date             TEXT NOT NULL,
    job_title             TEXT NOT NULL,
    department_id        INTEGER NOT NULL,
    manager_id            INTEGER,
    employment_status     TEXT NOT NULL,
    gross_monthly_salary  NUMERIC NOT NULL,
    currency              TEXT NOT NULL,
    reason                TEXT NOT NULL
) ON COMMIT DROP;

-- SECTION: BACKUP
-- Not applicable: Onboarding creates new records for an employee and initial salary, so no pre-existing rows exist to back up.

-- SECTION: MAIN_SCRIPT
INSERT INTO employees (
    employee_id,
    first_name,
    last_name,
    email,
    hire_date,
    job_title,
    department_id,
    manager_id,
    employment_status,
    termination_date
)
SELECT
    tmp.employee_id,
    tmp.first_name,
    tmp.last_name,
    tmp.email,
    tmp.hire_date,
    tmp.job_title,
    tmp.department_id,
    tmp.manager_id,
    tmp.employment_status,
    NULL
FROM tmp_onboard_employee tmp;

INSERT INTO salary_history (
    salary_id,
    employee_id,
    effective_date,
    gross_monthly_salary,
    currency,
    reason
)
SELECT
    COALESCE((SELECT MAX(salary_id) FROM salary_history), 0) + 1,
    tmp.employee_id,
    tmp.hire_date,
    tmp.gross_monthly_salary,
    tmp.currency,
    tmp.reason
FROM tmp_onboard_employee tmp;

-- SECTION: POST_VALIDATION
DO $$
DECLARE
    v_unattached_salaries INTEGER;
BEGIN
    SELECT COUNT(*)
    INTO v_unattached_salaries
    FROM salary_history sh
    LEFT JOIN employees e ON sh.employee_id = e.employee_id
    WHERE e.employee_id IS NULL;

    IF v_unattached_salaries > 0 THEN
        RAISE EXCEPTION 'Post-validation failed: Found % salary_history records with non-existent employee_id', v_unattached_salaries;
    END IF;
END $$;

-- SECTION: ROLLBACK_AUTO
CREATE OR REPLACE PROCEDURE onboard_employee(
    p_first_name           TEXT,
    p_last_name            TEXT,
    p_email                TEXT,
    p_hire_date            TEXT,
    p_job_title            TEXT,
    p_department_id        INTEGER,
    p_manager_id           INTEGER,
    p_gross_monthly_salary NUMERIC,
    p_currency             TEXT,
    INOUT p_employee_id    INTEGER DEFAULT NULL
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_salary_id INTEGER;
BEGIN
    -- Pre-validation
    IF p_first_name IS NULL OR length(trim(p_first_name)) = 0 THEN
        RAISE EXCEPTION 'First name cannot be empty';
    END IF;

    IF p_last_name IS NULL OR length(trim(p_last_name)) = 0 THEN
        RAISE EXCEPTION 'Last name cannot be empty';
    END IF;

    IF p_email IS NULL OR length(trim(p_email)) = 0 THEN
        RAISE EXCEPTION 'Email cannot be empty';
    END IF;

    IF EXISTS (SELECT 1 FROM employees WHERE email = p_email) THEN
        RAISE EXCEPTION 'Employee with email % already exists', p_email;
    END IF;

    IF p_manager_id IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM employees WHERE employee_id = p_manager_id) THEN
            RAISE EXCEPTION 'Manager ID % does not exist in employees table', p_manager_id;
        END IF;
    END IF;

    IF p_gross_monthly_salary IS NULL OR p_gross_monthly_salary <= 0 THEN
        RAISE EXCEPTION 'Gross monthly salary must be positive, got %', p_gross_monthly_salary;
    END IF;

    IF p_currency IS NULL OR length(trim(p_currency)) = 0 THEN
        RAISE EXCEPTION 'Currency code cannot be empty';
    END IF;

    -- Key generation
    IF p_employee_id IS NULL THEN
        SELECT COALESCE(MAX(employee_id), 0) + 1 INTO p_employee_id FROM employees;
    END IF;

    SELECT COALESCE(MAX(salary_id), 0) + 1 INTO v_salary_id FROM salary_history;

    -- Temp table staging
    CREATE TEMP TABLE IF NOT EXISTS tmp_onboard_employee (
        employee_id          INTEGER PRIMARY KEY,
        first_name           TEXT NOT NULL,
        last_name            TEXT NOT NULL,
        email                TEXT NOT NULL,
        hire_date            TEXT NOT NULL,
        job_title            TEXT NOT NULL,
        department_id        INTEGER NOT NULL,
        manager_id           INTEGER,
        employment_status    TEXT NOT NULL,
        gross_monthly_salary NUMERIC NOT NULL,
        currency             TEXT NOT NULL,
        reason               TEXT NOT NULL
    ) ON COMMIT DROP;

    TRUNCATE TABLE tmp_onboard_employee;

    INSERT INTO tmp_onboard_employee (
        employee_id,
        first_name,
        last_name,
        email,
        hire_date,
        job_title,
        department_id,
        manager_id,
        employment_status,
        gross_monthly_salary,
        currency,
        reason
    ) VALUES (
        p_employee_id,
        p_first_name,
        p_last_name,
        p_email,
        p_hire_date,
        p_job_title,
        p_department_id,
        p_manager_id,
        'active',
        p_gross_monthly_salary,
        p_currency,
        'hire'
    );

    -- Main mutation
    INSERT INTO employees (
        employee_id,
        first_name,
        last_name,
        email,
        hire_date,
        job_title,
        department_id,
        manager_id,
        employment_status,
        termination_date
    )
    SELECT
        employee_id,
        first_name,
        last_name,
        email,
        hire_date,
        job_title,
        department_id,
        manager_id,
        employment_status,
        NULL
    FROM tmp_onboard_employee;

    INSERT INTO salary_history (
        salary_id,
        employee_id,
        effective_date,
        gross_monthly_salary,
        currency,
        reason
    )
    SELECT
        v_salary_id,
        employee_id,
        hire_date,
        gross_monthly_salary,
        currency,
        reason
    FROM tmp_onboard_employee;

    -- Post-validation
    IF NOT EXISTS (
        SELECT 1 FROM employees WHERE employee_id = p_employee_id AND employment_status = 'active'
    ) THEN
        RAISE EXCEPTION 'Post-validation failed: Employee record % was not created properly', p_employee_id;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM salary_history WHERE employee_id = p_employee_id AND reason = 'hire'
    ) THEN
        RAISE EXCEPTION 'Post-validation failed: Starting salary history record for employee % was not created', p_employee_id;
    END IF;

EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'onboard_employee failed for email %: %', p_email, SQLERRM;
        RAISE;
END;
$$;

-- SECTION: ROLLBACK_MANUAL
CREATE OR REPLACE PROCEDURE rollback_onboard_employee(p_employee_id INTEGER)
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM employees WHERE employee_id = p_employee_id
    ) THEN
        RAISE EXCEPTION 'Employee % does not exist, cannot roll back onboarding', p_employee_id;
    END IF;

    DELETE FROM salary_history WHERE employee_id = p_employee_id;
    DELETE FROM employees WHERE employee_id = p_employee_id;

    RAISE NOTICE 'Onboarding for employee % successfully rolled back', p_employee_id;
END;
$$;