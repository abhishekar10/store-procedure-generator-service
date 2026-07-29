-- Pattern: BACKUP
-- Snapshot the rows about to be mutated, so an auto or manual rollback has
-- something to restore.

CREATE TABLE IF NOT EXISTS payroll_entries_backup (
    backup_id          SERIAL PRIMARY KEY,
    payroll_entry_id   INTEGER NOT NULL,
    payroll_run_id     INTEGER NOT NULL,
    employee_id        INTEGER NOT NULL,
    gross_pay          NUMERIC NOT NULL,
    total_deductions   NUMERIC NOT NULL,
    net_pay            NUMERIC NOT NULL,
    status             TEXT NOT NULL,
    backed_up_at       TIMESTAMP NOT NULL DEFAULT now()
);

INSERT INTO payroll_entries_backup
    (payroll_entry_id, payroll_run_id, employee_id, gross_pay, total_deductions, net_pay, status)
SELECT payroll_entry_id, payroll_run_id, employee_id, gross_pay, total_deductions, net_pay, status
FROM payroll_entries
WHERE payroll_run_id = p_payroll_run_id;
