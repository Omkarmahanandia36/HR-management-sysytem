import os
import sqlite3
from datetime import date, timedelta
from flask import Flask, redirect, render_template, request, url_for

app = Flask(__name__)
DB_PATH = "company.db"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def scalar(conn, query, params=()):
    row = conn.execute(query, params).fetchone()
    return row[0] if row else 0


def next_emp_code(conn):
    row = conn.execute(
        """
        SELECT emp_code
        FROM employees
        WHERE emp_code LIKE 'EMP%'
        ORDER BY CAST(SUBSTR(emp_code, 4) AS INTEGER) DESC
        LIMIT 1
        """
    ).fetchone()
    if not row or not row["emp_code"]:
        return "EMP001"

    code = row["emp_code"]
    suffix = code[3:]
    if not suffix.isdigit():
        return "EMP001"
    return f"EMP{int(suffix) + 1:03d}"


HALF_DAY_STATUS_VALUES = {"half day", "half-day", "halfday", "half_day"}


def is_half_day_record(status_value, work_hours_value):
    status = (status_value or "").strip().lower()
    if status in HALF_DAY_STATUS_VALUES:
        return True

    if status in ("absent", "leave", "holiday"):
        return False

    try:
        hours = float(work_hours_value or 0)
    except (TypeError, ValueError):
        return False
    return 0 < hours <= 4.5


@app.route("/")
def login():
    return render_template("login.html")


@app.route("/admin")
def admin_home():
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/dashboard")
def admin_dashboard():
    conn = get_db_connection()
    today = date.today().isoformat()

    total_employees = scalar(conn, "SELECT COUNT(*) FROM employees")
    present_today = scalar(
        conn,
        "SELECT COUNT(*) FROM attendance WHERE date = ? AND LOWER(status) = 'present'",
        (today,),
    )
    late_today = scalar(
        conn,
        "SELECT COUNT(*) FROM attendance WHERE date = ? AND late_flag = 1",
        (today,),
    )
    on_leave_today = scalar(
        conn,
        """
        SELECT COUNT(DISTINCT employee_id)
        FROM (
            SELECT employee_id
            FROM attendance
            WHERE date = ? AND LOWER(status) = 'leave'

            UNION

            SELECT employee_id
            FROM leave_requests
            WHERE LOWER(status) = 'approved'
              AND ? BETWEEN from_date AND to_date
        )
        """,
        (today, today),
    )
    pending_leaves = scalar(
        conn,
        "SELECT COUNT(*) FROM leave_requests WHERE LOWER(status) = 'pending'",
    )

    absent_marked = scalar(
        conn,
        "SELECT COUNT(*) FROM attendance WHERE date = ? AND LOWER(status) = 'absent'",
        (today,),
    )
    attendance_rows_today = scalar(
        conn,
        "SELECT COUNT(*) FROM attendance WHERE date = ?",
        (today,),
    )
    if attendance_rows_today:
        absent_today = max(0, total_employees - present_today - on_leave_today)
    else:
        absent_today = absent_marked

    recent_activity = conn.execute(
        """
        SELECT
            COALESCE(e.full_name, 'System') AS actor_name,
            a.action,
            a.timestamp
        FROM activity_logs AS a
        LEFT JOIN employees AS e ON e.id = a.employee_id
        ORDER BY a.timestamp DESC
        LIMIT 6
        """
    ).fetchall()

    conn.close()
    return render_template(
        "admin_dashboard.html",
        section="dashboard",
        page_title="Dashboard",
        page_subtitle=f"Today's HR snapshot ({today}).",
        today=today,
        total_employees=total_employees,
        present_today=present_today,
        late_today=late_today,
        on_leave_today=on_leave_today,
        absent_today=absent_today,
        pending_leaves=pending_leaves,
        recent_activity=recent_activity,
    )


@app.route("/admin/employees", methods=["GET"])
def employee_management():
    conn = get_db_connection()
    query = request.args.get("q", "").strip()
    edit_id = request.args.get("edit_id", type=int)
    message = request.args.get("msg", "").strip()

    departments = conn.execute(
        "SELECT id, dept_name FROM departments ORDER BY dept_name"
    ).fetchall()
    roles = conn.execute("SELECT id, role_name FROM roles ORDER BY role_name").fetchall()

    employee_sql = """
        SELECT
            e.id,
            e.emp_code,
            e.full_name,
            e.department_id,
            e.role_id,
            d.dept_name,
            r.role_name,
            e.email,
            e.phone,
            e.join_date,
            e.status
        FROM employees AS e
        LEFT JOIN departments AS d ON d.id = e.department_id
        LEFT JOIN roles AS r ON r.id = e.role_id
    """
    employee_params = ()
    if query:
        like = f"%{query}%"
        employee_sql += """
            WHERE
                e.emp_code LIKE ?
                OR e.full_name LIKE ?
                OR e.email LIKE ?
                OR d.dept_name LIKE ?
                OR r.role_name LIKE ?
        """
        employee_params = (like, like, like, like, like)

    employee_sql += " ORDER BY e.emp_code"
    employees = conn.execute(employee_sql, employee_params).fetchall()

    edit_employee = None
    if edit_id:
        edit_employee = conn.execute(
            """
            SELECT
                id,
                emp_code,
                full_name,
                department_id,
                role_id,
                phone,
                email,
                join_date,
                status
            FROM employees
            WHERE id = ?
            """,
            (edit_id,),
        ).fetchone()

    new_emp_code = next_emp_code(conn)
    conn.close()

    return render_template(
        "admin_employees.html",
        section="employees",
        page_title="Employee Management",
        page_subtitle="Add, edit, deactivate, view, and search employees.",
        employees=employees,
        departments=departments,
        roles=roles,
        query=query,
        message=message,
        edit_employee=edit_employee,
        new_emp_code=new_emp_code,
    )


@app.route("/admin/employees/add", methods=["POST"])
def add_employee():
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    join_date = request.form.get("join_date", "").strip()
    status = request.form.get("status", "Active").strip() or "Active"
    department_id = request.form.get("department_id", type=int)
    role_id = request.form.get("role_id", type=int)
    emp_code = request.form.get("emp_code", "").strip()

    if not full_name or not department_id or not role_id:
        return redirect(
            url_for("employee_management", msg="Please fill required fields for new employee.")
        )

    conn = get_db_connection()
    try:
        if not emp_code:
            emp_code = next_emp_code(conn)

        cursor = conn.execute(
            """
            INSERT INTO employees
            (emp_code, full_name, department_id, role_id, phone, email, join_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (emp_code, full_name, department_id, role_id, phone, email, join_date, status),
        )
        employee_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO activity_logs (employee_id, action, timestamp, ip_address)
            VALUES (?, ?, datetime('now', 'localtime'), ?)
            """,
            (employee_id, f"Employee created: {full_name} ({emp_code})", request.remote_addr),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return redirect(
            url_for(
                "employee_management",
                msg=f"Employee code '{emp_code}' already exists. Use a unique code.",
            )
        )

    conn.close()
    return redirect(url_for("employee_management", msg=f"Employee '{full_name}' added successfully."))


@app.route("/admin/employees/<int:employee_id>/edit", methods=["POST"])
def edit_employee(employee_id):
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    join_date = request.form.get("join_date", "").strip()
    status = request.form.get("status", "Active").strip() or "Active"
    department_id = request.form.get("department_id", type=int)
    role_id = request.form.get("role_id", type=int)

    if not full_name or not department_id or not role_id:
        return redirect(
            url_for(
                "employee_management",
                edit_id=employee_id,
                msg="Please fill required fields before saving changes.",
            )
        )

    conn = get_db_connection()
    row = conn.execute(
        "SELECT full_name, emp_code FROM employees WHERE id = ?",
        (employee_id,),
    ).fetchone()
    if not row:
        conn.close()
        return redirect(url_for("employee_management", msg="Employee not found."))

    conn.execute(
        """
        UPDATE employees
        SET
            full_name = ?,
            department_id = ?,
            role_id = ?,
            phone = ?,
            email = ?,
            join_date = ?,
            status = ?
        WHERE id = ?
        """
        ,
        (
            full_name,
            department_id,
            role_id,
            phone,
            email,
            join_date,
            status,
            employee_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO activity_logs (employee_id, action, timestamp, ip_address)
        VALUES (?, ?, datetime('now', 'localtime'), ?)
        """,
        (
            employee_id,
            f"Employee updated: {row['full_name']} ({row['emp_code']})",
            request.remote_addr,
        ),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("employee_management", msg=f"Employee '{full_name}' updated successfully."))


@app.route("/admin/employees/<int:employee_id>/deactivate", methods=["POST"])
def deactivate_employee(employee_id):
    query = request.form.get("q", "").strip()
    conn = get_db_connection()
    row = conn.execute(
        "SELECT full_name, emp_code, status FROM employees WHERE id = ?",
        (employee_id,),
    ).fetchone()
    if not row:
        conn.close()
        return redirect(url_for("employee_management", q=query, msg="Employee not found."))

    if (row["status"] or "").lower() != "inactive":
        conn.execute(
            "UPDATE employees SET status = 'Inactive' WHERE id = ?",
            (employee_id,),
        )
        conn.execute(
            """
            INSERT INTO activity_logs (employee_id, action, timestamp, ip_address)
            VALUES (?, ?, datetime('now', 'localtime'), ?)
            """,
            (
                employee_id,
                f"Employee deactivated: {row['full_name']} ({row['emp_code']})",
                request.remote_addr,
            ),
        )
        conn.commit()
        msg = f"Employee '{row['full_name']}' deactivated."
    else:
        msg = f"Employee '{row['full_name']}' is already inactive."

    conn.close()
    return redirect(url_for("employee_management", q=query, msg=msg))


@app.route("/admin/attendance")
def attendance_management():
    conn = get_db_connection()
    view_mode = request.args.get("view", "daily").strip().lower()
    if view_mode not in ("daily", "monthly"):
        view_mode = "daily"

    selected_date = request.args.get("date", "").strip() or date.today().isoformat()
    selected_month = request.args.get("month", "").strip() or date.today().strftime("%Y-%m")
    override_id = request.args.get("override_id", type=int)
    message = request.args.get("msg", "").strip()

    attendance_rows = []
    monthly_rows = []
    override_row = None

    # Graph 1: daily attendance distribution
    daily_counts = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(status, '')) = 'present' THEN 1 ELSE 0 END), 0) AS present_count,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(status, '')) = 'leave' THEN 1 ELSE 0 END), 0) AS leave_count,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(status, '')) = 'absent' THEN 1 ELSE 0 END), 0) AS absent_count,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(status, '')) = 'holiday' THEN 1 ELSE 0 END), 0) AS holiday_count
        FROM attendance
        WHERE date = ?
        """,
        (selected_date,),
    ).fetchone()
    daily_chart = {
        "labels": ["Present", "Leave", "Absent", "Holiday"],
        "values": [
            int(daily_counts["present_count"] or 0),
            int(daily_counts["leave_count"] or 0),
            int(daily_counts["absent_count"] or 0),
            int(daily_counts["holiday_count"] or 0),
        ],
    }

    # Graph 2: monthly attendance trend by date
    monthly_trend_rows = conn.execute(
        """
        SELECT
            date,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(status, '')) = 'present' THEN 1 ELSE 0 END), 0) AS present_count,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(status, '')) = 'leave' THEN 1 ELSE 0 END), 0) AS leave_count,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(status, '')) = 'absent' THEN 1 ELSE 0 END), 0) AS absent_count
        FROM attendance
        WHERE SUBSTR(date, 1, 7) = ?
        GROUP BY date
        ORDER BY date
        """,
        (selected_month,),
    ).fetchall()
    monthly_chart = {
        "labels": [row["date"] for row in monthly_trend_rows],
        "present": [int(row["present_count"] or 0) for row in monthly_trend_rows],
        "leave": [int(row["leave_count"] or 0) for row in monthly_trend_rows],
        "absent": [int(row["absent_count"] or 0) for row in monthly_trend_rows],
    }

    if view_mode == "daily":
        attendance_rows = conn.execute(
            """
            SELECT
                a.id,
                e.emp_code,
                e.full_name,
                a.date,
                a.check_in,
                a.check_out,
                a.work_hours,
                a.break_hours,
                a.status,
                a.late_flag,
                a.auto_marked
            FROM attendance AS a
            JOIN employees AS e ON e.id = a.employee_id
            WHERE a.date = ?
            ORDER BY e.emp_code
            """,
            (selected_date,),
        ).fetchall()

        if override_id:
            override_row = conn.execute(
                """
                SELECT
                    a.id,
                    a.employee_id,
                    e.emp_code,
                    e.full_name,
                    a.date,
                    a.check_in,
                    a.check_out,
                    a.work_hours,
                    a.break_hours,
                    a.status,
                    a.late_flag,
                    a.auto_marked
                FROM attendance AS a
                JOIN employees AS e ON e.id = a.employee_id
                WHERE a.id = ?
                """,
                (override_id,),
            ).fetchone()
    else:
        monthly_rows = conn.execute(
            """
            SELECT
                e.id AS employee_id,
                e.emp_code,
                e.full_name,
                SUM(CASE WHEN LOWER(COALESCE(a.status, '')) = 'present' THEN 1 ELSE 0 END) AS present_days,
                SUM(CASE WHEN LOWER(COALESCE(a.status, '')) = 'leave' THEN 1 ELSE 0 END) AS leave_days,
                SUM(CASE WHEN LOWER(COALESCE(a.status, '')) = 'absent' THEN 1 ELSE 0 END) AS absent_days,
                SUM(CASE WHEN COALESCE(a.late_flag, 0) = 1 THEN 1 ELSE 0 END) AS late_days,
                ROUND(COALESCE(SUM(a.work_hours), 0), 2) AS total_work_hours
            FROM employees AS e
            LEFT JOIN attendance AS a
                ON a.employee_id = e.id
               AND SUBSTR(a.date, 1, 7) = ?
            GROUP BY e.id, e.emp_code, e.full_name
            ORDER BY e.emp_code
            """,
            (selected_month,),
        ).fetchall()

    conn.close()

    return render_template(
        "admin_attendance.html",
        section="attendance",
        page_title="Attendance Management",
        page_subtitle="Daily/Monthly attendance, filtering, and manual override.",
        view_mode=view_mode,
        selected_date=selected_date,
        selected_month=selected_month,
        message=message,
        attendance_rows=attendance_rows,
        monthly_rows=monthly_rows,
        override_row=override_row,
        daily_chart=daily_chart,
        monthly_chart=monthly_chart,
    )


@app.route("/admin/attendance/<int:attendance_id>/override", methods=["POST"])
def override_attendance(attendance_id):
    view_mode = request.form.get("view", "daily").strip().lower()
    selected_date = request.form.get("date", "").strip() or date.today().isoformat()
    selected_month = request.form.get("month", "").strip() or date.today().strftime("%Y-%m")

    status = request.form.get("status", "Present").strip() or "Present"
    check_in = request.form.get("check_in", "").strip() or None
    check_out = request.form.get("check_out", "").strip() or None
    work_hours_raw = request.form.get("work_hours", "").strip()
    break_hours_raw = request.form.get("break_hours", "").strip()
    late_flag = 1 if request.form.get("late_flag", "0") in ("1", "true", "True", "on") else 0

    try:
        work_hours = float(work_hours_raw) if work_hours_raw else 0.0
        break_hours = float(break_hours_raw) if break_hours_raw else 0.0
    except ValueError:
        return redirect(
            url_for(
                "attendance_management",
                view=view_mode,
                date=selected_date,
                month=selected_month,
                override_id=attendance_id,
                msg="Invalid number format for work/break hours.",
            )
        )

    conn = get_db_connection()
    row = conn.execute(
        "SELECT id, employee_id FROM attendance WHERE id = ?",
        (attendance_id,),
    ).fetchone()
    if not row:
        conn.close()
        return redirect(
            url_for(
                "attendance_management",
                view=view_mode,
                date=selected_date,
                month=selected_month,
                msg="Attendance record not found.",
            )
        )

    conn.execute(
        """
        UPDATE attendance
        SET
            check_in = ?,
            check_out = ?,
            work_hours = ?,
            break_hours = ?,
            status = ?,
            late_flag = ?,
            auto_marked = 0
        WHERE id = ?
        """,
        (check_in, check_out, work_hours, break_hours, status, late_flag, attendance_id),
    )
    conn.execute(
        """
        INSERT INTO activity_logs (employee_id, action, timestamp, ip_address)
        VALUES (?, ?, datetime('now', 'localtime'), ?)
        """,
        (
            row["employee_id"],
            f"Attendance override applied for record #{attendance_id}",
            request.remote_addr,
        ),
    )
    conn.commit()
    conn.close()
    return redirect(
        url_for(
            "attendance_management",
            view=view_mode,
            date=selected_date,
            month=selected_month,
            msg="Attendance updated successfully.",
        )
    )


@app.route("/admin/leaves")
def leave_management():
    conn = get_db_connection()
    selected_date = request.args.get("date", "").strip() or date.today().isoformat()
    selected_department_id = request.args.get("dept_detail", type=int)
    message = request.args.get("msg", "").strip()

    employee_snapshot_rows = conn.execute(
        """
        SELECT
            e.id AS employee_id,
            e.emp_code,
            e.full_name,
            d.id AS department_id,
            d.dept_name,
            LOWER(COALESCE(a.status, '')) AS attendance_status,
            COALESCE(a.status, '') AS attendance_status_label,
            approved_leave.id AS approved_leave_id,
            COALESCE(latest_request.status, '') AS leave_request_status,
            latest_request.applied_on AS leave_request_applied_on,
            latest_request.from_date AS leave_request_from_date,
            latest_request.to_date AS leave_request_to_date
        FROM employees AS e
        LEFT JOIN departments AS d ON d.id = e.department_id
        LEFT JOIN attendance AS a
            ON a.employee_id = e.id
           AND a.date = ?
        LEFT JOIN leave_requests AS approved_leave
            ON approved_leave.id = (
                SELECT lr_a.id
                FROM leave_requests AS lr_a
                WHERE lr_a.employee_id = e.id
                  AND LOWER(COALESCE(lr_a.status, '')) = 'approved'
                  AND ? BETWEEN lr_a.from_date AND lr_a.to_date
                ORDER BY COALESCE(lr_a.approved_on, lr_a.applied_on, '') DESC, lr_a.id DESC
                LIMIT 1
            )
        LEFT JOIN leave_requests AS latest_request
            ON latest_request.id = (
                SELECT lr_l.id
                FROM leave_requests AS lr_l
                WHERE lr_l.employee_id = e.id
                ORDER BY COALESCE(lr_l.applied_on, '') DESC, lr_l.id DESC
                LIMIT 1
            )
        WHERE LOWER(COALESCE(e.status, 'active')) != 'inactive'
        ORDER BY COALESCE(d.dept_name, 'Unassigned'), e.emp_code
        """,
        (selected_date, selected_date),
    ).fetchall()

    department_lookup = {}
    for row in employee_snapshot_rows:
        department_id = row["department_id"] if row["department_id"] is not None else 0
        department_name = row["dept_name"] or "Unassigned"

        if department_id not in department_lookup:
            department_lookup[department_id] = {
                "department_id": department_id,
                "dept_name": department_name,
                "total_members": 0,
                "present_count": 0,
                "absent_count": 0,
                "leave_count": 0,
                "holiday_count": 0,
                "new_request_count": 0,
                "absent_employees": [],
            }

        card = department_lookup[department_id]
        card["total_members"] += 1

        attendance_status = (row["attendance_status"] or "").strip().lower()
        has_approved_leave = bool(row["approved_leave_id"])
        is_present = attendance_status == "present"
        is_on_leave = attendance_status == "leave" or has_approved_leave
        is_holiday = attendance_status == "holiday"
        is_absent = attendance_status == "absent" or (not is_present and not is_on_leave and not is_holiday)

        if is_present:
            card["present_count"] += 1
            continue
        if is_on_leave:
            card["leave_count"] += 1
            continue
        if is_holiday:
            card["holiday_count"] += 1
            continue
        if not is_absent:
            continue

        card["absent_count"] += 1
        leave_request_status = (row["leave_request_status"] or "").strip() or "No Request"
        leave_request_status_lower = leave_request_status.lower()
        is_new_request = leave_request_status_lower in ("pending", "new", "new request")

        leave_request_window = ""
        if row["leave_request_from_date"] and row["leave_request_to_date"]:
            if row["leave_request_from_date"] == row["leave_request_to_date"]:
                leave_request_window = row["leave_request_from_date"]
            else:
                leave_request_window = f"{row['leave_request_from_date']} to {row['leave_request_to_date']}"

        card["absent_employees"].append(
            {
                "employee_id": row["employee_id"],
                "emp_code": row["emp_code"],
                "full_name": row["full_name"],
                "attendance_status": row["attendance_status_label"] or "Absent",
                "leave_request_status": leave_request_status,
                "leave_request_applied_on": row["leave_request_applied_on"] or "",
                "leave_request_window": leave_request_window,
                "is_new_request": is_new_request,
            }
        )
        if is_new_request:
            card["new_request_count"] += 1

    department_cards = sorted(
        department_lookup.values(),
        key=lambda item: item["dept_name"].lower(),
    )
    selected_department = None
    if selected_department_id is not None:
        selected_department = next(
            (item for item in department_cards if item["department_id"] == selected_department_id),
            None,
        )

    leave_requests = conn.execute(
        """
        SELECT
            id,
            applicant_name,
            leave_type,
            from_date,
            to_date,
            days,
            reason,
            status,
            applied_on
        FROM leave_requests
        ORDER BY applied_on DESC
        """
    ).fetchall()
    conn.close()

    return render_template(
        "admin_leaves.html",
        section="leaves",
        page_title="Leave Management",
        page_subtitle="Review leave requests and employee details.",
        selected_date=selected_date,
        selected_department_id=selected_department_id,
        selected_department=selected_department,
        department_cards=department_cards,
        message=message,
        leave_requests=leave_requests,
    )


@app.route("/admin/leaves/<int:leave_id>/approve", methods=["POST"])
def approve_leave_request(leave_id):
    selected_date = request.form.get("date", "").strip() or date.today().isoformat()
    selected_department_id = request.form.get("dept_detail", type=int)

    conn = get_db_connection()
    leave_row = conn.execute(
        """
        SELECT
            id,
            employee_id,
            applicant_name,
            status
        FROM leave_requests
        WHERE id = ?
        """,
        (leave_id,),
    ).fetchone()

    if not leave_row:
        conn.close()
        return redirect(
            url_for(
                "leave_management",
                date=selected_date,
                dept_detail=selected_department_id,
                msg="Leave request not found.",
            )
        )

    current_status = (leave_row["status"] or "").strip().lower()
    if current_status not in ("pending", "new", "new request"):
        conn.close()
        return redirect(
            url_for(
                "leave_management",
                date=selected_date,
                dept_detail=selected_department_id,
                msg="Only pending leave requests can be approved.",
            )
        )

    conn.execute(
        """
        UPDATE leave_requests
        SET
            status = 'Approved',
            approved_on = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (leave_id,),
    )
    conn.execute(
        """
        INSERT INTO activity_logs (employee_id, action, timestamp, ip_address)
        VALUES (?, ?, datetime('now', 'localtime'), ?)
        """,
        (
            leave_row["employee_id"],
            f"Leave request #{leave_id} approved for {leave_row['applicant_name'] or 'employee'}",
            request.remote_addr,
        ),
    )
    conn.commit()
    conn.close()

    return redirect(
        url_for(
            "leave_management",
            date=selected_date,
            dept_detail=selected_department_id,
            msg=f"Leave request #{leave_id} approved and activity updated.",
        )
    )


@app.route("/admin/leaves/individual-snapshot")
def individual_attendance_snapshot():
    conn = get_db_connection()
    query = request.args.get("q", "").strip()

    employee_sql = """
        SELECT
            e.id AS employee_id,
            e.emp_code,
            e.full_name,
            COALESCE(d.dept_name, 'Unassigned') AS dept_name,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(a.status, '')) = 'present' THEN 1 ELSE 0 END), 0) AS total_present,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(a.status, '')) = 'absent' THEN 1 ELSE 0 END), 0) AS total_absent,
            COALESCE(SUM(CASE WHEN LOWER(COALESCE(a.status, '')) = 'leave' THEN 1 ELSE 0 END), 0) AS total_leave_days,
            COALESCE(SUM(CASE WHEN COALESCE(a.late_flag, 0) = 1 THEN 1 ELSE 0 END), 0) AS total_late_days
        FROM employees AS e
        LEFT JOIN departments AS d ON d.id = e.department_id
        LEFT JOIN attendance AS a ON a.employee_id = e.id
        WHERE LOWER(COALESCE(e.status, 'active')) != 'inactive'
    """
    employee_params = []
    if query:
        employee_sql += " AND LOWER(COALESCE(e.full_name, '')) LIKE ?"
        employee_params.append(f"%{query.lower()}%")

    employee_sql += """
        GROUP BY e.id, e.emp_code, e.full_name, d.dept_name
        ORDER BY e.emp_code
    """
    employee_totals = conn.execute(employee_sql, tuple(employee_params)).fetchall()

    attendance_rows = []
    if employee_totals:
        employee_ids = [row["employee_id"] for row in employee_totals]
        placeholders = ",".join("?" for _ in employee_ids)
        attendance_rows = conn.execute(
            f"""
            SELECT
                employee_id,
                date,
                status,
                work_hours
            FROM attendance
            WHERE employee_id IN ({placeholders})
            ORDER BY employee_id, date
            """,
            tuple(employee_ids),
        ).fetchall()
    conn.close()

    streak_state = {}
    for row in attendance_rows:
        employee_id = row["employee_id"]
        if employee_id not in streak_state:
            streak_state[employee_id] = {
                "current_streak": 0,
                "max_streak": 0,
                "previous_date": None,
                "previous_half_day": False,
            }

        state = streak_state[employee_id]
        record_date = None
        if row["date"]:
            try:
                record_date = date.fromisoformat(row["date"])
            except ValueError:
                record_date = None

        is_half_day = is_half_day_record(row["status"], row["work_hours"])
        if is_half_day:
            if (
                state["previous_half_day"]
                and state["previous_date"]
                and record_date
                and record_date == state["previous_date"] + timedelta(days=1)
            ):
                state["current_streak"] += 1
            elif (
                state["previous_half_day"]
                and state["previous_date"]
                and record_date
                and record_date == state["previous_date"]
            ):
                state["current_streak"] = max(1, state["current_streak"])
            else:
                state["current_streak"] = 1

            state["max_streak"] = max(state["max_streak"], state["current_streak"])
        else:
            state["current_streak"] = 0

        state["previous_half_day"] = is_half_day
        state["previous_date"] = record_date

    employee_cards = []
    for row in employee_totals:
        state = streak_state.get(row["employee_id"], {})
        max_half_day_streak = int(state.get("max_streak", 0) or 0)
        total_leave_days = int(row["total_leave_days"] or 0)
        total_late_days = int(row["total_late_days"] or 0)
        red_flag = total_leave_days >= 3 and total_late_days >= 3
        orange_flag = total_leave_days >= 3 and not red_flag

        employee_cards.append(
            {
                "employee_id": row["employee_id"],
                "emp_code": row["emp_code"],
                "full_name": row["full_name"],
                "dept_name": row["dept_name"],
                "total_present": int(row["total_present"] or 0),
                "total_absent": int(row["total_absent"] or 0),
                "total_leave_days": total_leave_days,
                "total_late_days": total_late_days,
                "max_half_day_streak": max_half_day_streak,
                "red_flag": red_flag,
                "orange_flag": orange_flag,
                "yellow_card": max_half_day_streak >= 3,
            }
        )

    employee_cards.sort(
        key=lambda item: (
            -int(item["red_flag"]),
            -int(item["orange_flag"]),
            -int(item["yellow_card"]),
            item["full_name"].lower(),
        )
    )

    return render_template(
        "admin_individual_snapshot.html",
        section="leaves",
        page_title="Individual Attendance Snapshot",
        page_subtitle="Red: 3+ total leave and 3+ total late marks. Orange: 3+ total leave. Yellow: 3+ continuous half-days.",
        query=query,
        employee_cards=employee_cards,
    )


@app.route("/admin/reports")
def reports():
    conn = get_db_connection()

    department_summary = conn.execute(
        """
        SELECT d.dept_name, COUNT(e.id) AS employee_count
        FROM departments AS d
        LEFT JOIN employees AS e ON e.department_id = d.id
        GROUP BY d.id, d.dept_name
        ORDER BY d.dept_name
        """
    ).fetchall()

    leave_status_summary = conn.execute(
        """
        SELECT status, COUNT(*) AS total
        FROM leave_requests
        GROUP BY status
        ORDER BY total DESC
        """
    ).fetchall()

    conn.close()
    return render_template(
        "admin_reports.html",
        section="reports",
        page_title="Reports",
        page_subtitle="High-level workforce and leave analytics.",
        department_summary=department_summary,
        leave_status_summary=leave_status_summary,
    )


@app.route("/admin/settings")
def system_settings():
    return render_template(
        "admin_settings.html",
        section="settings",
        page_title="System Settings",
        page_subtitle="Application preferences and policy controls.",
    )


@app.route("/admin/logs")
def activity_logs():
    conn = get_db_connection()
    logs = conn.execute(
        """
        SELECT
            COALESCE(e.full_name, 'System') AS actor_name,
            a.action,
            a.timestamp,
            a.ip_address
        FROM activity_logs AS a
        LEFT JOIN employees AS e ON e.id = a.employee_id
        ORDER BY a.timestamp DESC
        LIMIT 100
        """
    ).fetchall()
    conn.close()

    return render_template(
        "admin_logs.html",
        section="logs",
        page_title="Activity Logs",
        page_subtitle="Recent administrative and user activity.",
        logs=logs,
    )


@app.route("/admin/backup")
def backup_restore():
    file_size_bytes = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    return render_template(
        "admin_backup.html",
        section="backup",
        page_title="Backup and Restore",
        page_subtitle="Database backup status and restore controls.",
        file_size_bytes=file_size_bytes,
    )


@app.route("/logout")
def logout():
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True, use_reloader=True)
