"""SQLite connection management, schema initialization, and opt-in demo seeding."""
from __future__ import annotations

import contextlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Union

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK(length(trim(name)) > 0),
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    email TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK(email LIKE '%_@_%.__%'),
    designation TEXT NOT NULL CHECK(length(trim(designation)) > 0),
    department_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (department_id) REFERENCES departments(id) ON DELETE RESTRICT ON UPDATE CASCADE
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    operation TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    outcome TEXT NOT NULL,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_context (
    conversation_id TEXT PRIMARY KEY,
    last_employee_id INTEGER,
    last_employee_name TEXT,
    last_department_id INTEGER,
    last_department_name TEXT,
    pending_clarification_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL UNIQUE,
    operation TEXT NOT NULL,
    target_entity TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    target_summary TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_employees_department_id ON employees(department_id);
CREATE INDEX IF NOT EXISTS idx_employees_name ON employees(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_employees_email ON employees(email COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_departments_name ON departments(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_audit_logs_conv_id ON audit_logs(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_turns_conv_id ON conversation_turns(conversation_id);
"""


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_connection(db_path: Union[str, Path]) -> sqlite3.Connection:
    """Create a SQLite connection with foreign-key enforcement and Row factory enabled."""
    path_obj = Path(db_path)
    if str(path_obj) != ":memory:":
        path_obj.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path_obj))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextlib.contextmanager
def get_db_connection(db_path: Union[str, Path]) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a connection with foreign keys enabled and transactional commit/rollback."""
    conn = create_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_database(db_path: Union[str, Path]) -> None:
    """Initialize required schema tables and indexes without overwriting existing data."""
    with get_db_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute("PRAGMA foreign_keys = ON;")
        # Ensure employee auto-increment IDs start at 101 if table is completely empty and unseeded
        emp_count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        seq_row = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'employees'"
        ).fetchone()
        if emp_count == 0 and seq_row is None:
            conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('employees', 100)")


DEMO_DEPARTMENTS = [
    (1, "Engineering", "Core product, platform, and software engineering"),
    (2, "Human Resources", "People operations, talent management, and workplace policies"),
    (3, "Finance", "Financial planning, accounting, and compliance"),
    (4, "Design", "Product design and user experience"),
]

DEMO_EMPLOYEES = [
    (101, "Aarav Patel", "aarav.patel@example.com", "Engineering Manager", 1),
    (102, "Priya Nair", "priya.nair@example.com", "Software Developer", 1),
    (103, "Rohan Verma", "rohan.verma@example.com", "DevOps Engineer", 1),
    (104, "Ananya Iyer", "ananya.iyer@example.com", "HR Business Partner", 2),
    (105, "Vikram Singh", "vikram.singh@example.com", "Financial Analyst", 3),
]


def seed_demo_data(db_path: Union[str, Path]) -> bool:
    """Seed the demonstration dataset ONLY if both departments and employees tables are empty.

    Returns True if demo data was inserted, or False if the database already contained records.
    """
    initialize_database(db_path)
    now = utc_now_iso()
    with get_db_connection(db_path) as conn:
        dept_count = conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        emp_count = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        if dept_count > 0 or emp_count > 0:
            return False

        for dept_id, name, description in DEMO_DEPARTMENTS:
            conn.execute(
                """
                INSERT INTO departments (id, name, description, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (dept_id, name, description, now),
            )

        for emp_id, name, email, designation, department_id in DEMO_EMPLOYEES:
            conn.execute(
                """
                INSERT INTO employees (id, name, email, designation, department_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (emp_id, name, email, designation, department_id, now, now),
            )

        conn.execute("UPDATE sqlite_sequence SET seq = 105 WHERE name = 'employees'")
        return True
