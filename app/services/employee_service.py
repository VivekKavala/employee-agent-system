"""Application service layer enforcing business rules, referential integrity, and audit trails."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.database.connection import initialize_database
from app.database.models import (
    AuditLogRecord,
    DepartmentCreateInput,
    DepartmentRecord,
    DepartmentSummaryRecord,
    DepartmentUpdateInput,
    EmployeeCreateInput,
    EmployeeRecord,
    EmployeeUpdateInput,
    model_to_dict,
)
from app.database.repositories import (
    AuditRepository,
    DepartmentRepository,
    EmployeeRepository,
)
from app.logging.setup import log_event
from app.services.validation import (
    AmbiguousTargetError,
    DuplicateRecordError,
    NotFoundError,
    ReferentialIntegrityError,
    ValidationErrorApp,
    validate_email_address,
    validate_non_empty_text,
    validate_positive_id,
)


class EmployeeInformationService:
    """Validated application service mediating all access to Employee and Department repositories."""

    def __init__(self, db_path: Union[str, Path]) -> None:
        self.db_path = Path(db_path)
        initialize_database(self.db_path)
        self.departments = DepartmentRepository(self.db_path)
        self.employees = EmployeeRepository(self.db_path)
        self.audits = AuditRepository(self.db_path)

    # -------------------------------------------------------------------------
    # Department Read Operations
    # -------------------------------------------------------------------------

    def list_departments(self, include_counts: bool = True) -> List[Dict[str, Any]]:
        if include_counts:
            records = self.departments.list_with_employee_counts()
            return [model_to_dict(r) for r in records]
        records_basic = self.departments.list_all()
        return [model_to_dict(r) for r in records_basic]

    def get_department(
        self,
        *,
        department_id: Optional[int] = None,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        dept: Optional[DepartmentRecord] = None
        if department_id is not None:
            valid_id = validate_positive_id(department_id, "department_id")
            dept = self.departments.get_by_id(valid_id)
            if not dept:
                raise NotFoundError(f"Department with ID {valid_id} was not found.")
        elif name is not None and str(name).strip():
            cleaned = str(name).strip()
            dept = self.departments.get_by_name(cleaned)
            if not dept:
                matches = self.departments.search_by_name(cleaned)
                if len(matches) == 1:
                    dept = matches[0]
                elif len(matches) > 1:
                    candidates = [model_to_dict(m) for m in matches]
                    raise AmbiguousTargetError(
                        f"Multiple departments match '{cleaned}': "
                        + ", ".join(f"{m.name} (ID {m.id})" for m in matches)
                        + ". Please specify the exact department name or ID.",
                        candidates=candidates,
                    )
                else:
                    raise NotFoundError(f"Department '{cleaned}' was not found.")
        else:
            raise ValidationErrorApp("Either department_id or department name must be provided.")

        emp_count = self.departments.count_employees_in_department(dept.id)
        data = model_to_dict(dept)
        data["employee_count"] = emp_count
        return data

    # -------------------------------------------------------------------------
    # Department Mutation Operations
    # -------------------------------------------------------------------------

    def create_department(
        self,
        *,
        name: str,
        description: Optional[str] = None,
        conversation_id: str = "-",
        agent_name: str = "create_agent",
    ) -> Dict[str, Any]:
        try:
            validated = DepartmentCreateInput(name=name, description=description)
        except Exception as exc:
            raise ValidationErrorApp(str(exc)) from exc

        existing = self.departments.get_by_name(validated.name)
        if existing:
            self.audits.record(
                conversation_id=conversation_id,
                agent_name=agent_name,
                operation="create_department",
                entity_type="department",
                entity_id=str(existing.id),
                outcome="rejected_duplicate",
                details={"name": validated.name},
            )
            raise DuplicateRecordError(
                f"Department '{existing.name}' already exists (ID {existing.id})."
            )

        try:
            created = self.departments.create(
                name=validated.name,
                description=validated.description,
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(f"Department '{validated.name}' already exists.") from exc

        self.audits.record(
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="create_department",
            entity_type="department",
            entity_id=str(created.id),
            outcome="success",
            details={"name": created.name, "description": created.description},
        )
        log_event(
            logging.INFO,
            f"Created department ID {created.id} ({created.name})",
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="create_department",
            status="success",
        )
        result = model_to_dict(created)
        result["employee_count"] = 0
        return result

    def update_department(
        self,
        *,
        department_id: Optional[int] = None,
        current_name: Optional[str] = None,
        new_name: Optional[str] = None,
        description: Optional[str] = None,
        update_description: bool = False,
        conversation_id: str = "-",
        agent_name: str = "update_agent",
    ) -> Dict[str, Any]:
        target = self.get_department(department_id=department_id, name=current_name)
        target_id = int(target["id"])

        if new_name is None and not update_description and description is None:
            raise ValidationErrorApp(
                "At least one field to update (new_name or description) must be provided."
            )

        cleaned_new_name: Optional[str] = None
        if new_name is not None:
            cleaned_new_name = validate_non_empty_text(new_name, "Department new_name", max_length=100)
            conflict = self.departments.get_by_name(cleaned_new_name)
            if conflict and conflict.id != target_id:
                raise DuplicateRecordError(
                    f"Cannot rename department to '{cleaned_new_name}' because department ID {conflict.id} already uses that name."
                )

        if description is not None:
            update_description = True

        updated = self.departments.update(
            target_id,
            name=cleaned_new_name,
            description=description,
            update_description=update_description,
        )
        if not updated:
            raise NotFoundError(f"Department with ID {target_id} was not found.")

        self.audits.record(
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="update_department",
            entity_type="department",
            entity_id=str(updated.id),
            outcome="success",
            details={"new_name": cleaned_new_name, "description": description},
        )
        log_event(
            logging.INFO,
            f"Updated department ID {updated.id} ({updated.name})",
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="update_department",
            status="success",
        )
        result = model_to_dict(updated)
        result["employee_count"] = self.departments.count_employees_in_department(updated.id)
        return result

    def delete_department(
        self,
        *,
        department_id: int,
        conversation_id: str = "-",
        agent_name: str = "delete_agent",
    ) -> Dict[str, Any]:
        valid_id = validate_positive_id(department_id, "department_id")
        existing = self.departments.get_by_id(valid_id)
        if not existing:
            raise NotFoundError(f"Department with ID {valid_id} was not found.")

        emp_count = self.departments.count_employees_in_department(valid_id)
        if emp_count > 0:
            self.audits.record(
                conversation_id=conversation_id,
                agent_name=agent_name,
                operation="delete_department",
                entity_type="department",
                entity_id=str(valid_id),
                outcome="rejected_referential_integrity",
                details={"name": existing.name, "employee_count": emp_count},
            )
            raise ReferentialIntegrityError(
                f"Cannot delete department '{existing.name}' (ID {valid_id}) because {emp_count} "
                f"employee(s) are still assigned to it. Reassign or remove those employees first."
            )

        deleted = self.departments.delete(valid_id)
        if not deleted:
            raise NotFoundError(f"Department with ID {valid_id} could not be deleted.")

        self.audits.record(
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="delete_department",
            entity_type="department",
            entity_id=str(valid_id),
            outcome="success",
            details={"deleted_department": existing.name},
        )
        log_event(
            logging.INFO,
            f"Deleted department ID {valid_id} ({existing.name})",
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="delete_department",
            status="success",
        )
        return model_to_dict(existing)

    # -------------------------------------------------------------------------
    # Employee Read & Resolution Operations
    # -------------------------------------------------------------------------

    def list_employees(self) -> List[Dict[str, Any]]:
        return [model_to_dict(e) for e in self.employees.list_all()]

    def get_employee(self, employee_id: int) -> Dict[str, Any]:
        valid_id = validate_positive_id(employee_id, "employee_id")
        record = self.employees.get_by_id(valid_id)
        if not record:
            raise NotFoundError(f"Employee with ID {valid_id} was not found.")
        return model_to_dict(record)

    def search_employees(
        self,
        *,
        employee_id: Optional[int] = None,
        name: Optional[str] = None,
        email: Optional[str] = None,
        department_id: Optional[int] = None,
        department_name: Optional[str] = None,
        designation: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if employee_id is not None:
            emp = self.get_employee(employee_id)
            return [emp]
        records = self.employees.search(
            name=name,
            email=email,
            department_id=department_id,
            department_name=department_name,
            designation=designation,
        )
        return [model_to_dict(r) for r in records]

    def resolve_unique_employee(
        self,
        *,
        employee_id: Optional[int] = None,
        name: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Resolve a single unambiguous employee by ID, email, or name; raise AmbiguousTargetError if multiple match."""
        if employee_id is not None:
            return self.get_employee(employee_id)

        if email is not None and str(email).strip():
            rec = self.employees.get_by_email(str(email).strip())
            if not rec:
                raise NotFoundError(f"Employee with email '{email}' was not found.")
            return model_to_dict(rec)

        if name is not None and str(name).strip():
            cleaned = str(name).strip()
            exact = self.employees.find_by_exact_name(cleaned)
            if len(exact) == 1:
                return model_to_dict(exact[0])
            if len(exact) > 1:
                candidates = [model_to_dict(e) for e in exact]
                raise AmbiguousTargetError(
                    f"Multiple employees share the name '{cleaned}': "
                    + ", ".join(f"{e.name} (ID {e.id}, {e.designation} in {e.department_name})" for e in exact)
                    + ". Please specify the employee ID.",
                    candidates=candidates,
                )
            partial = self.employees.search(name=cleaned)
            if len(partial) == 1:
                return model_to_dict(partial[0])
            if len(partial) > 1:
                candidates = [model_to_dict(e) for e in partial]
                raise AmbiguousTargetError(
                    f"Multiple employees match '{cleaned}': "
                    + ", ".join(f"{e.name} (ID {e.id}, {e.designation} in {e.department_name})" for e in partial)
                    + ". Please specify the exact employee ID.",
                    candidates=candidates,
                )
            raise NotFoundError(f"No employee matching '{cleaned}' was found.")

        raise ValidationErrorApp("Please provide an employee ID, name, or email to identify the employee.")

    def _resolve_department_id(
        self,
        department_id: Optional[int] = None,
        department_name: Optional[str] = None,
    ) -> DepartmentRecord:
        if department_id is not None:
            valid_id = validate_positive_id(department_id, "department_id")
            dept = self.departments.get_by_id(valid_id)
            if not dept:
                raise NotFoundError(
                    f"Department with ID {valid_id} does not exist. Create the department first or choose an existing department."
                )
            return dept
        if department_name is not None and str(department_name).strip():
            cleaned = str(department_name).strip()
            dept = self.departments.get_by_name(cleaned)
            if not dept:
                raise NotFoundError(
                    f"Department '{cleaned}' does not exist. Available departments can be viewed with 'Show all departments'."
                )
            return dept
        raise ValidationErrorApp("Either department_id or department_name is required.")

    # -------------------------------------------------------------------------
    # Employee Mutation Operations
    # -------------------------------------------------------------------------

    def create_employee(
        self,
        *,
        name: str,
        email: Optional[str] = None,
        designation: str,
        department_id: Optional[int] = None,
        department_name: Optional[str] = None,
        employee_id: Optional[int] = None,
        conversation_id: str = "-",
        agent_name: str = "create_agent",
    ) -> Dict[str, Any]:
        cleaned_name = validate_non_empty_text(name, "Employee name", max_length=120)
        cleaned_designation = validate_non_empty_text(designation, "Employee designation", max_length=120)
        cleaned_email = validate_email_address(email, required=True)
        assert cleaned_email is not None

        dept = self._resolve_department_id(department_id=department_id, department_name=department_name)

        # Check email uniqueness
        existing_email = self.employees.get_by_email(cleaned_email)
        if existing_email:
            self.audits.record(
                conversation_id=conversation_id,
                agent_name=agent_name,
                operation="create_employee",
                entity_type="employee",
                entity_id=str(existing_email.id),
                outcome="rejected_duplicate_email",
                details={"email": cleaned_email, "existing_id": existing_email.id},
            )
            raise DuplicateRecordError(
                f"An employee with email '{cleaned_email}' already exists: "
                f"{existing_email.name} (ID {existing_email.id})."
            )

        # Protect against accidental duplicate create (same name + designation + department)
        exact_name_matches = self.employees.find_by_exact_name(cleaned_name)
        for match in exact_name_matches:
            if (
                match.department_id == dept.id
                and match.designation.lower() == cleaned_designation.lower()
            ):
                self.audits.record(
                    conversation_id=conversation_id,
                    agent_name=agent_name,
                    operation="create_employee",
                    entity_type="employee",
                    entity_id=str(match.id),
                    outcome="rejected_duplicate_employee",
                    details={"name": cleaned_name, "department": dept.name},
                )
                raise DuplicateRecordError(
                    f"Employee '{cleaned_name}' already exists as '{match.designation}' in '{dept.name}' (ID {match.id})."
                )

        try:
            created = self.employees.create(
                name=cleaned_name,
                email=cleaned_email,
                designation=cleaned_designation,
                department_id=dept.id,
                employee_id=employee_id,
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateRecordError(f"Could not create employee due to a uniqueness or integrity constraint: {exc}") from exc

        self.audits.record(
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="create_employee",
            entity_type="employee",
            entity_id=str(created.id),
            outcome="success",
            details={
                "name": created.name,
                "email": created.email,
                "designation": created.designation,
                "department_id": created.department_id,
                "department_name": created.department_name,
            },
        )
        log_event(
            logging.INFO,
            f"Created employee ID {created.id} ({created.name}) in department {created.department_name}",
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="create_employee",
            status="success",
        )
        return model_to_dict(created)

    def update_employee(
        self,
        *,
        employee_id: int,
        name: Optional[str] = None,
        email: Optional[str] = None,
        designation: Optional[str] = None,
        department_id: Optional[int] = None,
        department_name: Optional[str] = None,
        conversation_id: str = "-",
        agent_name: str = "update_agent",
    ) -> Dict[str, Any]:
        valid_id = validate_positive_id(employee_id, "employee_id")
        existing = self.employees.get_by_id(valid_id)
        if not existing:
            raise NotFoundError(f"Employee with ID {valid_id} was not found.")

        if all(v is None for v in (name, email, designation, department_id, department_name)):
            raise ValidationErrorApp(
                "At least one field to update (name, email, designation, or department) must be specified."
            )

        cleaned_name = validate_non_empty_text(name, "Employee name", max_length=120) if name is not None else None
        cleaned_designation = (
            validate_non_empty_text(designation, "Employee designation", max_length=120)
            if designation is not None
            else None
        )
        cleaned_email = validate_email_address(email, required=False) if email is not None else None

        if cleaned_email is not None:
            conflict = self.employees.get_by_email(cleaned_email)
            if conflict and conflict.id != valid_id:
                raise DuplicateRecordError(
                    f"Email '{cleaned_email}' is already used by employee {conflict.name} (ID {conflict.id})."
                )

        resolved_dept_id: Optional[int] = None
        if department_id is not None or department_name is not None:
            dept = self._resolve_department_id(department_id=department_id, department_name=department_name)
            resolved_dept_id = dept.id

        updated = self.employees.update(
            valid_id,
            name=cleaned_name,
            email=cleaned_email,
            designation=cleaned_designation,
            department_id=resolved_dept_id,
        )
        if not updated:
            raise NotFoundError(f"Employee with ID {valid_id} was not found.")

        changed_fields: Dict[str, Any] = {}
        if cleaned_name is not None:
            changed_fields["name"] = cleaned_name
        if cleaned_email is not None:
            changed_fields["email"] = cleaned_email
        if cleaned_designation is not None:
            changed_fields["designation"] = cleaned_designation
        if resolved_dept_id is not None:
            changed_fields["department_id"] = resolved_dept_id
            changed_fields["department_name"] = updated.department_name

        self.audits.record(
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="update_employee",
            entity_type="employee",
            entity_id=str(updated.id),
            outcome="success",
            details=changed_fields,
        )
        log_event(
            logging.INFO,
            f"Updated employee ID {updated.id} ({updated.name}): {list(changed_fields.keys())}",
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="update_employee",
            status="success",
        )
        return model_to_dict(updated)

    def delete_employee(
        self,
        *,
        employee_id: int,
        conversation_id: str = "-",
        agent_name: str = "delete_agent",
    ) -> Dict[str, Any]:
        valid_id = validate_positive_id(employee_id, "employee_id")
        existing = self.employees.get_by_id(valid_id)
        if not existing:
            raise NotFoundError(f"Employee with ID {valid_id} was not found.")

        deleted = self.employees.delete(valid_id)
        if not deleted:
            raise NotFoundError(f"Employee with ID {valid_id} could not be deleted.")

        self.audits.record(
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="delete_employee",
            entity_type="employee",
            entity_id=str(valid_id),
            outcome="success",
            details={
                "deleted_name": existing.name,
                "deleted_email": existing.email,
                "department_name": existing.department_name,
            },
        )
        log_event(
            logging.INFO,
            f"Deleted employee ID {valid_id} ({existing.name})",
            conversation_id=conversation_id,
            agent_name=agent_name,
            operation="delete_employee",
            status="success",
        )
        return model_to_dict(existing)

    def list_audit_logs(self, conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if conversation_id:
            records = self.audits.list_by_conversation(conversation_id)
        else:
            records = self.audits.list_all()
        return [model_to_dict(r) for r in records]
