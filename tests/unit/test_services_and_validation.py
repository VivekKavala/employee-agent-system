"""Unit tests for EmployeeInformationService CRUD, uniqueness, validation, and referential integrity."""
from __future__ import annotations

import pytest

from app.services.employee_service import EmployeeInformationService
from app.services.validation import (
    AmbiguousTargetError,
    DuplicateRecordError,
    NotFoundError,
    ReferentialIntegrityError,
    ValidationErrorApp,
)


def test_department_crud_and_uniqueness(employee_service: EmployeeInformationService) -> None:
    # Create
    created = employee_service.create_department(
        name="Security",
        description="Information and infrastructure security",
        conversation_id="unit-conv-1",
    )
    assert created["name"] == "Security"
    assert created["employee_count"] == 0

    # Duplicate department name (case-insensitive)
    with pytest.raises(DuplicateRecordError, match="already exists"):
        employee_service.create_department(name="security")

    # Read
    fetched = employee_service.get_department(name="Security")
    assert fetched["id"] == created["id"]

    # Update description and name
    updated = employee_service.update_department(
        department_id=created["id"],
        new_name="Cybersecurity",
        description="Updated security team",
        conversation_id="unit-conv-1",
    )
    assert updated["name"] == "Cybersecurity"
    assert updated["description"] == "Updated security team"

    # Delete empty department succeeds
    deleted = employee_service.delete_department(
        department_id=created["id"],
        conversation_id="unit-conv-1",
    )
    assert deleted["name"] == "Cybersecurity"

    with pytest.raises(NotFoundError):
        employee_service.get_department(department_id=created["id"])


def test_cannot_delete_department_with_assigned_employees(employee_service: EmployeeInformationService) -> None:
    # Engineering (ID 1) has 3 seeded employees
    with pytest.raises(ReferentialIntegrityError, match="employee\(s\) are still assigned"):
        employee_service.delete_department(department_id=1, conversation_id="unit-conv-fk")

    # Verify Engineering still exists
    eng = employee_service.get_department(department_id=1)
    assert eng["employee_count"] == 3


def test_employee_crud_and_partial_update_preserves_fields(employee_service: EmployeeInformationService) -> None:
    # Create employee
    emp = employee_service.create_employee(
        name="Rahul Sharma",
        email="rahul.sharma@example.com",
        designation="Software Engineer",
        department_name="Engineering",
        conversation_id="unit-conv-emp",
    )
    assert emp["id"] >= 106
    assert emp["name"] == "Rahul Sharma"
    assert emp["email"] == "rahul.sharma@example.com"
    assert emp["designation"] == "Software Engineer"
    assert emp["department_name"] == "Engineering"

    # Update only designation; verify name, email, and department are preserved
    updated = employee_service.update_employee(
        employee_id=emp["id"],
        designation="Senior Software Engineer",
        conversation_id="unit-conv-emp",
    )
    assert updated["name"] == "Rahul Sharma"
    assert updated["email"] == "rahul.sharma@example.com"
    assert updated["designation"] == "Senior Software Engineer"
    assert updated["department_name"] == "Engineering"

    # Delete employee
    deleted = employee_service.delete_employee(
        employee_id=emp["id"],
        conversation_id="unit-conv-emp",
    )
    assert deleted["id"] == emp["id"]

    with pytest.raises(NotFoundError):
        employee_service.get_employee(emp["id"])


def test_duplicate_email_and_accidental_duplicate_create_rejected(employee_service: EmployeeInformationService) -> None:
    # Duplicate email against seeded Priya Nair (priya.nair@example.com)
    with pytest.raises(DuplicateRecordError, match="already exists"):
        employee_service.create_employee(
            name="Another Priya",
            email="PRIYA.NAIR@example.com",
            designation="QA Engineer",
            department_name="Engineering",
        )

    # Accidental duplicate create (same name + designation + department)
    with pytest.raises(DuplicateRecordError, match="already exists"):
        employee_service.create_employee(
            name="Priya Nair",
            email="priya.nair2@example.com",
            designation="Software Developer",
            department_name="Engineering",
        )


def test_required_fields_and_invalid_values(employee_service: EmployeeInformationService) -> None:
    with pytest.raises(ValidationErrorApp, match="Employee name"):
        employee_service.create_employee(
            name="   ",
            email="valid@example.com",
            designation="Developer",
            department_name="Engineering",
        )

    with pytest.raises(ValidationErrorApp, match="Invalid email"):
        employee_service.create_employee(
            name="Test User",
            email="not-an-email",
            designation="Developer",
            department_name="Engineering",
        )

    with pytest.raises(NotFoundError, match="Department 'NonExistent' does not exist"):
        employee_service.create_employee(
            name="Test User",
            email="test.user@example.com",
            designation="Developer",
            department_name="NonExistent",
        )

    with pytest.raises(NotFoundError, match="Employee with ID 9999 was not found"):
        employee_service.get_employee(9999)


def test_ambiguous_employee_resolution_raises_ambiguous_target_error(employee_service: EmployeeInformationService) -> None:
    employee_service.create_employee(
        name="Kiran Rao",
        email="kiran.rao.eng@example.com",
        designation="Backend Engineer",
        department_name="Engineering",
    )
    employee_service.create_employee(
        name="Kiran Rao",
        email="kiran.rao.fin@example.com",
        designation="Tax Analyst",
        department_name="Finance",
    )
    with pytest.raises(AmbiguousTargetError, match="Multiple employees share the name 'Kiran Rao'"):
        employee_service.resolve_unique_employee(name="Kiran Rao")


def test_audit_trail_recorded_for_mutations(employee_service: EmployeeInformationService) -> None:
    dept = employee_service.create_department(name="Legal", conversation_id="audit-conv")
    employee_service.update_department(
        department_id=dept["id"],
        description="Corporate legal affairs",
        conversation_id="audit-conv",
    )
    audits = employee_service.list_audit_logs(conversation_id="audit-conv")
    assert len(audits) == 2
    assert audits[0]["operation"] == "create_department"
    assert audits[0]["outcome"] == "success"
    assert audits[1]["operation"] == "update_department"
    assert audits[1]["outcome"] == "success"
