"""Parameterized SQLite repositories for Employee, Department, and Audit persistence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.database.connection import get_db_connection, utc_now_iso
from app.database.models import (
    AuditLogRecord,
    DepartmentRecord,
    DepartmentSummaryRecord,
    EmployeeRecord,
)


class DepartmentRepository:
    """Data access layer for Department records using parameterized SQL."""

    def __init__(self, db_path: Union[str, Path]) -> None:
        self.db_path = Path(db_path)

    def get_by_id(self, department_id: int) -> Optional[DepartmentRecord]:
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, description, created_at FROM departments WHERE id = ?",
                (department_id,),
            ).fetchone()
            return DepartmentRecord(**dict(row)) if row else None

    def get_by_name(self, name: str) -> Optional[DepartmentRecord]:
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, description, created_at FROM departments WHERE lower(name) = lower(?)",
                (name.strip(),),
            ).fetchone()
            return DepartmentRecord(**dict(row)) if row else None

    def search_by_name(self, query: str) -> List[DepartmentRecord]:
        with get_db_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, name, description, created_at
                FROM departments
                WHERE lower(name) LIKE lower(?)
                ORDER BY id ASC
                """,
                (f"%{query.strip()}%",),
            ).fetchall()
            return [DepartmentRecord(**dict(r)) for r in rows]

    def list_all(self) -> List[DepartmentRecord]:
        with get_db_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, name, description, created_at FROM departments ORDER BY id ASC"
            ).fetchall()
            return [DepartmentRecord(**dict(r)) for r in rows]

    def list_with_employee_counts(self) -> List[DepartmentSummaryRecord]:
        with get_db_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    d.id,
                    d.name,
                    d.description,
                    d.created_at,
                    COUNT(e.id) AS employee_count
                FROM departments d
                LEFT JOIN employees e ON e.department_id = d.id
                GROUP BY d.id, d.name, d.description, d.created_at
                ORDER BY d.id ASC
                """
            ).fetchall()
            return [DepartmentSummaryRecord(**dict(r)) for r in rows]

    def count_employees_in_department(self, department_id: int) -> int:
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM employees WHERE department_id = ?",
                (department_id,),
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def create(self, name: str, description: Optional[str] = None) -> DepartmentRecord:
        now = utc_now_iso()
        with get_db_connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO departments (name, description, created_at)
                VALUES (?, ?, ?)
                """,
                (name.strip(), description.strip() if description else None, now),
            )
            new_id = int(cursor.lastrowid)
            row = conn.execute(
                "SELECT id, name, description, created_at FROM departments WHERE id = ?",
                (new_id,),
            ).fetchone()
            return DepartmentRecord(**dict(row))

    def update(
        self,
        department_id: int,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        update_description: bool = False,
    ) -> Optional[DepartmentRecord]:
        with get_db_connection(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id, name, description, created_at FROM departments WHERE id = ?",
                (department_id,),
            ).fetchone()
            if not existing:
                return None

            final_name = name.strip() if name is not None else existing["name"]
            final_desc = description.strip() if (update_description and description is not None) else (
                None if (update_description and description is None) else existing["description"]
            )

            conn.execute(
                """
                UPDATE departments
                SET name = ?, description = ?
                WHERE id = ?
                """,
                (final_name, final_desc, department_id),
            )
            row = conn.execute(
                "SELECT id, name, description, created_at FROM departments WHERE id = ?",
                (department_id,),
            ).fetchone()
            return DepartmentRecord(**dict(row))

    def delete(self, department_id: int) -> bool:
        with get_db_connection(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM departments WHERE id = ?",
                (department_id,),
            )
            return cursor.rowcount > 0


class EmployeeRepository:
    """Data access layer for Employee records using parameterized SQL."""

    def __init__(self, db_path: Union[str, Path]) -> None:
        self.db_path = Path(db_path)

    _SELECT_BASE = """
        SELECT
            e.id,
            e.name,
            e.email,
            e.designation,
            e.department_id,
            d.name AS department_name,
            e.created_at,
            e.updated_at
        FROM employees e
        JOIN departments d ON d.id = e.department_id
    """

    def get_by_id(self, employee_id: int) -> Optional[EmployeeRecord]:
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(
                f"{self._SELECT_BASE} WHERE e.id = ?",
                (employee_id,),
            ).fetchone()
            return EmployeeRecord(**dict(row)) if row else None

    def get_by_email(self, email: str) -> Optional[EmployeeRecord]:
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(
                f"{self._SELECT_BASE} WHERE lower(e.email) = lower(?)",
                (email.strip(),),
            ).fetchone()
            return EmployeeRecord(**dict(row)) if row else None

    def list_all(self) -> List[EmployeeRecord]:
        with get_db_connection(self.db_path) as conn:
            rows = conn.execute(f"{self._SELECT_BASE} ORDER BY e.id ASC").fetchall()
            return [EmployeeRecord(**dict(r)) for r in rows]

    def search(
        self,
        *,
        name: Optional[str] = None,
        email: Optional[str] = None,
        department_id: Optional[int] = None,
        department_name: Optional[str] = None,
        designation: Optional[str] = None,
    ) -> List[EmployeeRecord]:
        clauses: List[str] = []
        params: List[Any] = []

        if name:
            clauses.append("lower(e.name) LIKE lower(?)")
            params.append(f"%{name.strip()}%")
        if email:
            clauses.append("lower(e.email) = lower(?)")
            params.append(email.strip())
        if department_id is not None:
            clauses.append("e.department_id = ?")
            params.append(department_id)
        if department_name:
            clauses.append("lower(d.name) = lower(?)")
            params.append(department_name.strip())
        if designation:
            clauses.append("lower(e.designation) LIKE lower(?)")
            params.append(f"%{designation.strip()}%")

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"{self._SELECT_BASE}{where_sql} ORDER BY e.id ASC"

        with get_db_connection(self.db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
            return [EmployeeRecord(**dict(r)) for r in rows]

    def find_by_exact_name(self, name: str) -> List[EmployeeRecord]:
        with get_db_connection(self.db_path) as conn:
            rows = conn.execute(
                f"{self._SELECT_BASE} WHERE lower(e.name) = lower(?) ORDER BY e.id ASC",
                (name.strip(),),
            ).fetchall()
            return [EmployeeRecord(**dict(r)) for r in rows]

    def create(
        self,
        *,
        name: str,
        email: str,
        designation: str,
        department_id: int,
        employee_id: Optional[int] = None,
    ) -> EmployeeRecord:
        now = utc_now_iso()
        with get_db_connection(self.db_path) as conn:
            if employee_id is not None:
                cursor = conn.execute(
                    """
                    INSERT INTO employees (id, name, email, designation, department_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        employee_id,
                        name.strip(),
                        email.strip().lower(),
                        designation.strip(),
                        department_id,
                        now,
                        now,
                    ),
                )
                new_id = int(employee_id)
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO employees (name, email, designation, department_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name.strip(),
                        email.strip().lower(),
                        designation.strip(),
                        department_id,
                        now,
                        now,
                    ),
                )
                new_id = int(cursor.lastrowid)

            row = conn.execute(
                f"{self._SELECT_BASE} WHERE e.id = ?",
                (new_id,),
            ).fetchone()
            return EmployeeRecord(**dict(row))

    def update(
        self,
        employee_id: int,
        *,
        name: Optional[str] = None,
        email: Optional[str] = None,
        designation: Optional[str] = None,
        department_id: Optional[int] = None,
    ) -> Optional[EmployeeRecord]:
        now = utc_now_iso()
        with get_db_connection(self.db_path) as conn:
            existing = conn.execute(
                "SELECT id, name, email, designation, department_id FROM employees WHERE id = ?",
                (employee_id,),
            ).fetchone()
            if not existing:
                return None

            final_name = name.strip() if name is not None else existing["name"]
            final_email = email.strip().lower() if email is not None else existing["email"]
            final_designation = designation.strip() if designation is not None else existing["designation"]
            final_dept_id = department_id if department_id is not None else existing["department_id"]

            conn.execute(
                """
                UPDATE employees
                SET name = ?, email = ?, designation = ?, department_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    final_name,
                    final_email,
                    final_designation,
                    final_dept_id,
                    now,
                    employee_id,
                ),
            )
            row = conn.execute(
                f"{self._SELECT_BASE} WHERE e.id = ?",
                (employee_id,),
            ).fetchone()
            return EmployeeRecord(**dict(row))

    def delete(self, employee_id: int) -> bool:
        with get_db_connection(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM employees WHERE id = ?",
                (employee_id,),
            )
            return cursor.rowcount > 0


class AuditRepository:
    """Repository for recording and querying database mutation audit trails."""

    def __init__(self, db_path: Union[str, Path]) -> None:
        self.db_path = Path(db_path)

    def record(
        self,
        *,
        conversation_id: str,
        agent_name: str,
        operation: str,
        entity_type: str,
        entity_id: Optional[str],
        outcome: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> AuditLogRecord:
        now = utc_now_iso()
        details_json = json.dumps(details, ensure_ascii=False) if details is not None else None
        with get_db_connection(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO audit_logs (
                    timestamp, conversation_id, agent_name, operation,
                    entity_type, entity_id, outcome, details_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    conversation_id or "-",
                    agent_name,
                    operation,
                    entity_type,
                    str(entity_id) if entity_id is not None else None,
                    outcome,
                    details_json,
                ),
            )
            new_id = int(cursor.lastrowid)
            row = conn.execute(
                "SELECT * FROM audit_logs WHERE id = ?",
                (new_id,),
            ).fetchone()
            return AuditLogRecord(**dict(row))

    def list_by_conversation(self, conversation_id: str) -> List[AuditLogRecord]:
        with get_db_connection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM audit_logs WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
            return [AuditLogRecord(**dict(r)) for r in rows]

    def list_all(self) -> List[AuditLogRecord]:
        with get_db_connection(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM audit_logs ORDER BY id ASC").fetchall()
            return [AuditLogRecord(**dict(r)) for r in rows]
