import sqlite3

def create_database():
    conn = sqlite3.connect("company.db")
    cursor = conn.cursor()

    # Enable foreign keys
    cursor.execute("PRAGMA foreign_keys = ON;")

    # departments
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS departments(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dept_name TEXT UNIQUE
    );
    """)

    # ---------------- roles ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS roles(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role_name TEXT UNIQUE
    );
    """)

    # ---------------- employees ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS employees(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        emp_code TEXT UNIQUE,
        full_name TEXT,
        department_id INTEGER,
        role_id INTEGER,
        phone TEXT,
        email TEXT,
        join_date TEXT,
        status TEXT,
        FOREIGN KEY(department_id) REFERENCES departments(id),
        FOREIGN KEY(role_id) REFERENCES roles(id)
    );
    """)

    # ---------------- users (login) ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER UNIQUE,
        username TEXT UNIQUE,
        password_hash TEXT,
        must_change_password INTEGER DEFAULT 1,
        device_token TEXT,
        last_login TEXT,
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    );
    """)

    # ---------------- attendance ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS attendance(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER,
        date TEXT,
        check_in TEXT,
        check_out TEXT,
        work_hours REAL,
        break_hours REAL,
        status TEXT,
        late_flag INTEGER,
        auto_marked INTEGER,
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    );
    """)

    # ---------------- leave requests ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS leave_requests(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER,
        applicant_name TEXT,
        from_date TEXT,
        to_date TEXT,
        days INTEGER,
        leave_type TEXT,
        reason TEXT,
        status TEXT,
        applied_on TEXT,
        approved_by INTEGER,
        approved_on TEXT,
        FOREIGN KEY(employee_id) REFERENCES employees(id),
        FOREIGN KEY(approved_by) REFERENCES employees(id)
    );
    """)

    # Backward-compatible migration: add applicant_name column in existing DBs
    leave_request_columns = [row[1] for row in cursor.execute("PRAGMA table_info(leave_requests);").fetchall()]
    if "applicant_name" not in leave_request_columns:
        cursor.execute("ALTER TABLE leave_requests ADD COLUMN applicant_name TEXT;")

    # Keep name visible in leave_requests for existing rows
    cursor.execute("""
    UPDATE leave_requests
    SET applicant_name = (
        SELECT full_name
        FROM employees
        WHERE employees.id = leave_requests.employee_id
    )
    WHERE applicant_name IS NULL OR TRIM(applicant_name) = '';
    """)

    # Auto-fill applicant_name whenever a leave request is inserted
    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_leave_requests_fill_applicant_name
    AFTER INSERT ON leave_requests
    FOR EACH ROW
    BEGIN
        UPDATE leave_requests
        SET applicant_name = (
            SELECT full_name
            FROM employees
            WHERE employees.id = NEW.employee_id
        )
        WHERE id = NEW.id;
    END;
    """)

    # Keep applicant_name in sync if employee_id changes on a leave request
    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_leave_requests_sync_on_employee_change
    AFTER UPDATE OF employee_id ON leave_requests
    FOR EACH ROW
    BEGIN
        UPDATE leave_requests
        SET applicant_name = (
            SELECT full_name
            FROM employees
            WHERE employees.id = NEW.employee_id
        )
        WHERE id = NEW.id;
    END;
    """)

    # Keep applicant_name in sync if employee full_name is updated
    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS trg_leave_requests_sync_on_name_change
    AFTER UPDATE OF full_name ON employees
    FOR EACH ROW
    BEGIN
        UPDATE leave_requests
        SET applicant_name = NEW.full_name
        WHERE employee_id = NEW.id;
    END;
    """)

    # ---------------- tasks ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER,
        title TEXT,
        description TEXT,
        assigned_date TEXT,
        deadline TEXT,
        status TEXT,
        updated_on TEXT,
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    );
    """)

    # ---------------- activity logs ----------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS activity_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER,
        action TEXT,
        timestamp TEXT,
        ip_address TEXT,
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    );
    """)

    conn.commit()
    conn.close()
    print("Database and tables created successfully.")

if __name__ == "__main__":
    create_database()
