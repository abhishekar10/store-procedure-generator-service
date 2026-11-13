-- SECTION: PRE_VALIDATION
CREATE OR REPLACE PROCEDURE onboard_employee(
    p_employee_id          INTEGER,
    p_first_name           TEXT,
    p_last_name            TEXT,
    p_email                TEXT,
    p_hire_date            TEXT,
    p_job_title            TEXT,
    p_department_id        INTEGER,
    p_manager_id           INTEGER,
    p_salary_id            INTEGER,
    p_gross_monthly_salary NUMERIC,
    p_currency             TEXT
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_inserted_emp_count    INTEGER;
    v_inserted_salary_count INTEGER;
BEGIN
    IF p_employee_id IS NULL THEN
        RAISE EXCEPTION 'Employee ID is required';
    END IF;

    IF p_first_name IS NULL OR btrim(p_first_name) = '' THEN
        RAISE EXCEPTION 'First name is required';
    END IF;

    IF p_last_name IS NULL OR btrim(p_last_name) = '' THEN
        RAISE EXCEPTION 'Last name is required';
    END IF;

    IF p_email IS NULL OR btrim(p_email) = '' THEN
        RAISE EXCEPTION 'Email is required';
    END IF;

    IF p_hire_date IS NULL OR btrim(p_hire_date) = '' THEN
        RAISE EXCEPTION 'Hire date is required';
    END IF;

    IF p_job_title IS NULL OR btrim(p_job_title) = '' THEN
        RAISE EXCEPTION 'Job title is required';
    END IF;

    IF p_department_id IS NULL THEN
        RAISE EXCEPTION 'Department ID is required';
    END IF;

    IF p_salary_id IS NULL THEN
        RAISE EXCEPTION 'Salary ID is required';
    END IF;

    IF p_gross_monthly_salary IS NULL OR p_gross_monthly_salary <= 0 THEN
        RAISE EXCEPTION 'Gross monthly salary must be greater than zero';
    END IF;

    IF p_currency IS NULL OR btrim(p_currency) = '' THEN
        RAISE EXCEPTION 'Currency is required';
    END IF;

    IF EXISTS (SELECT 1 FROM employees WHERE employee_id = p_employee_id) THEN
        RAISE EXCEPTION 'Employee with ID % already exists', p_employee_id;
    END IF;

    IF EXISTS (SELECT 1 FROM employees WHERE email = p_email) THEN
        RAISE EXCEPTION 'Employee with email % already exists', p_email;
    END IF;

    IF p_manager_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM employees WHERE employee_id = p_manager_id
    ) THEN
        RAISE EXCEPTION 'Manager ID % does not exist', p_manager_id;
    END IF;

    IF EXISTS (SELECT 1 FROM salary_history WHERE salary_id = p_salary_id) THEN
        RAISE EXCEPTION 'Salary record with ID % already exists', p_salary_id;
    END IF;

-- SECTION: TEMP_TABLES
    CREATE TEMP TABLE tmp_new_employee_staging (
        employee_id          INTEGER PRIMARY KEY,
        first_name           TEXT NOT NULL,
        last_name            TEXT NOT NULL,
        email                TEXT NOT NULL,
        hire_date            TEXT NOT NULL,
        job_title            TEXT NOT NULL,
        department_id        INTEGER NOT NULL,
        manager_id           INTEGER,
        employment_status    TEXT NOT NULL,
        salary_id            INTEGER NOT NULL,
        gross_monthly_salary NUMERIC NOT NULL,
        currency             TEXT NOT NULL,
        reason               TEXT NOT NULL
    ) ON COMMIT DROP;

    INSERT INTO tmp_new_employee_staging (
        employee_id,
        first_name,
        last_name,
        email,
        hire_date,
        job_title,
        department_id,
        manager_id,
        employment_status,
        salary_id,
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
        p_salary_id,
        p_gross_monthly_salary,
        p_currency,
        'hire'
    );

-- SECTION: BACKUP
    CREATE TABLE IF NOT EXISTS onboarded_employees_backup (
        backup_id    SERIAL PRIMARY KEY,
        employee_id  INTEGER NOT NULL,
        salary_id    INTEGER NOT NULL,
        backed_up_at TIMESTAMP NOT NULL DEFAULT now()
    );

    INSERT INTO onboarded_employees_backup (employee_id, salary_id)
    SELECT employee_id, salary_id
    FROM tmp_new_employee_staging;

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
        employment_status
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
        employment_status
    FROM tmp_new_employee_staging;

    GET DIAGNOSTICS v_inserted_emp_count = ROW_COUNT;

    INSERT INTO salary_history (
        salary_id,
        employee_id,
        effective_date,
        gross_monthly_salary,
        currency,
        reason
    )
    SELECT
        salary_id,
        employee_id,
        hire_date,
        gross_monthly_salary,
        currency,
        reason
    FROM tmp_new_employee_staging;

    GET DIAGNOSTICS v_inserted_salary_count = ROW_COUNT;

    RAISE NOTICE 'Onboarded employee % and recorded initial salary record %',
        p_employee_id, p_salary_id;

-- SECTION: POST_VALIDATION
    IF v_inserted_emp_count <> 1 THEN
        RAISE EXCEPTION 'Failed to insert employee record for employee %', p_employee_id;
    END IF;

    IF v_inserted_salary_count <> 1 THEN
        RAISE EXCEPTION 'Failed to insert salary history record for employee %', p_employee_id;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM employees e
        JOIN salary_history s ON e.employee_id = s.employee_id
        WHERE e.employee_id = p_employee_id
          AND s.salary_id = p_salary_id
          AND e.employment_status = 'active'
          AND s.reason = 'hire'
    ) THEN
        RAISE EXCEPTION 'Post-validation failed: onboarded employee and salary records not found consistent';
    END IF;

-- SECTION: ROLLBACK_AUTO
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'onboard_employee failed: %', SQLERRM;
        RAISE;
END;
$$;

-- SECTION: ROLLBACK_MANUAL
CREATE OR REPLACE PROCEDURE rollback_onboard_employee(p_employee_id INTEGER)
LANGUAGE plpgsql
AS $$
DECLARE
    v_batch            TIMESTAMP;
    v_salary_id        INTEGER;
    v_deleted_salary   INTEGER;
    v_deleted_employee INTEGER;
BEGIN
    SELECT MAX(backed_up_at) INTO v_batch
    FROM onboarded_employees_backup
    WHERE employee_id = p_employee_id;

    IF v_batch IS NULL THEN
        RAISE EXCEPTION 'No backup found for employee %, cannot roll back', p_employee_id;
    END IF;

    SELECT salary_id INTO v_salary_id
    FROM onboarded_employees_backup
    WHERE employee_id = p_employee_id
      AND backed_up_at = v_batch;

    DELETE FROM salary_history
    WHERE salary_id = v_salary_id;

    GET DIAGNOSTICS v_deleted_salary = ROW_COUNT;

    DELETE FROM employees
    WHERE employee_id = p_employee_id;

    GET DIAGNOSTICS v_deleted_employee = ROW_COUNT;

    RAISE NOTICE 'Rolled back onboarding: deleted % employee and % salary records for employee %',
        v_deleted_employee, v_deleted_salary, p_employee_id;
END;
$$;