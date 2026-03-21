from werkzeug.security import generate_password_hash

from config import DEFAULT_ADMIN_EMAIL, DEFAULT_ADMIN_NAME, DEFAULT_ADMIN_PASSWORD

EMPLOYEE_PROFILE_COLUMNS = {
    "profile_image": "TEXT",
    "address": "TEXT",
    "date_of_birth": "TEXT",
    "emergency_contact": "TEXT",
    "blood_group": "TEXT",
    "alternate_phone": "TEXT",
}


def ensure_users_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER UNIQUE,
            username TEXT UNIQUE,
            password_hash TEXT,
            must_change_password INTEGER DEFAULT 1,
            device_token TEXT,
            last_login TEXT,
            FOREIGN KEY(employee_id) REFERENCES employees(id)
        )
        """
    )



def ensure_admin_users_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            last_login TEXT
        )
        """
    )

    admin_count = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    if admin_count == 0:
        conn.execute(
            """
            INSERT INTO admin_users (full_name, email, password_hash, is_active, last_login)
            VALUES (?, ?, ?, 1, NULL)
            """,
            (
                DEFAULT_ADMIN_NAME,
                DEFAULT_ADMIN_EMAIL,
                generate_password_hash(DEFAULT_ADMIN_PASSWORD),
            ),
        )
        conn.commit()



def ensure_employee_profile_columns(conn):
    employee_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(employees)").fetchall()
    }
    missing_columns = [
        (column_name, column_type)
        for column_name, column_type in EMPLOYEE_PROFILE_COLUMNS.items()
        if column_name not in employee_columns
    ]
    for column_name, column_type in missing_columns:
        conn.execute(f"ALTER TABLE employees ADD COLUMN {column_name} {column_type}")
    if missing_columns:
        conn.commit()



def ensure_employee_hourly_notes_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS employee_hourly_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            entry_date TEXT NOT NULL,
            slot_key TEXT NOT NULL,
            slot_label TEXT NOT NULL,
            time_range TEXT NOT NULL,
            note_text TEXT,
            status TEXT,
            updated_on TEXT NOT NULL,
            UNIQUE(employee_id, entry_date, slot_key),
            FOREIGN KEY(employee_id) REFERENCES employees(id)
        )
        """
    )
    # Add status column if it doesn't exist
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(employee_hourly_notes)").fetchall()
    }
    if "status" not in existing_columns:
        conn.execute("ALTER TABLE employee_hourly_notes ADD COLUMN status TEXT")
        conn.commit()
