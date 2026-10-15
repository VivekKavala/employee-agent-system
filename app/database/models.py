"""Pydantic data models and parameter schemas for Employee and Department entities (Pydantic v1 & v2 compatible)."""
import re
from typing import Any, Callable, Dict, Optional
from pydantic import BaseModel, Field

try:
    from pydantic import field_validator as _pydantic_field_validator  # type: ignore

    def _field_validator(field_name: str) -> Callable[..., Any]:
        return _pydantic_field_validator(field_name)

except ImportError:
    from pydantic import validator as _pydantic_v1_validator  # type: ignore

    def _field_validator(field_name: str) -> Callable[..., Any]:
        return _pydantic_v1_validator(field_name)


EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    """Compatibility helper for Pydantic v1 and v2 serialization."""
    if hasattr(model, "model_dump"):
        return model.model_dump()  # type: ignore[attr-defined]
    return model.dict()


class DepartmentRecord(BaseModel):
    """Database representation of a Department entity."""

    id: int
    name: str
    description: Optional[str] = None
    created_at: str


class DepartmentSummaryRecord(BaseModel):
    """Department record enriched with its current employee count."""

    id: int
    name: str
    description: Optional[str] = None
    employee_count: int = 0
    created_at: str


class EmployeeRecord(BaseModel):
    """Database representation of an Employee entity joined with department name."""

    id: int
    name: str
    email: str
    designation: str
    department_id: int
    department_name: Optional[str] = None
    created_at: str
    updated_at: str


class AuditLogRecord(BaseModel):
    """Database representation of an operational mutation audit record."""

    id: int
    timestamp: str
    conversation_id: str
    agent_name: str
    operation: str
    entity_type: str
    entity_id: Optional[str] = None
    outcome: str
    details_json: Optional[str] = None


class DepartmentCreateInput(BaseModel):
    """Validated schema for creating a new department."""

    name: str = Field(..., description="Unique department name (required).")
    description: Optional[str] = Field(None, description="Optional department description.")

    @_field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        cleaned = (v or "").strip()
        if not cleaned:
            raise ValueError("Department name cannot be empty.")
        if len(cleaned) > 100:
            raise ValueError("Department name cannot exceed 100 characters.")
        return cleaned

    @_field_validator("description")
    @classmethod
    def validate_description(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        return cleaned if cleaned else None


class DepartmentUpdateInput(BaseModel):
    """Validated schema for updating an existing department."""

    department_id: Optional[int] = Field(None, description="ID of the department to update.")
    current_name: Optional[str] = Field(None, description="Current name of the department if ID is not provided.")
    new_name: Optional[str] = Field(None, description="New department name.")
    description: Optional[str] = Field(None, description="New department description.")

    @_field_validator("new_name")
    @classmethod
    def validate_new_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("New department name cannot be empty string.")
        if len(cleaned) > 100:
            raise ValueError("Department name cannot exceed 100 characters.")
        return cleaned


class DepartmentDeleteInput(BaseModel):
    """Validated schema for deleting a department."""

    department_id: Optional[int] = Field(None, description="ID of the department to delete.")
    name: Optional[str] = Field(None, description="Name of the department to delete.")


class DepartmentQueryInput(BaseModel):
    """Validated schema for querying or searching departments."""

    department_id: Optional[int] = Field(None, description="Department ID to retrieve.")
    name: Optional[str] = Field(None, description="Department name to retrieve or search.")


class EmployeeCreateInput(BaseModel):
    """Validated schema for creating a new employee."""

    name: str = Field(..., description="Full name of the employee (required).")
    email: Optional[str] = Field(None, description="Unique email address of the employee (required).")
    designation: str = Field(..., description="Job title / designation of the employee (required).")
    department_id: Optional[int] = Field(None, description="Foreign key ID of the department.")
    department_name: Optional[str] = Field(None, description="Name of the department if department_id is not given.")

    @_field_validator("name")
    @classmethod
    def validate_emp_name(cls, v: str) -> str:
        cleaned = (v or "").strip()
        if not cleaned:
            raise ValueError("Employee name cannot be empty.")
        if len(cleaned) > 120:
            raise ValueError("Employee name cannot exceed 120 characters.")
        return cleaned

    @_field_validator("email")
    @classmethod
    def validate_emp_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip().lower()
        if not cleaned:
            return None
        if not EMAIL_REGEX.match(cleaned):
            raise ValueError(f"Invalid email address format: '{v}'.")
        return cleaned

    @_field_validator("designation")
    @classmethod
    def validate_emp_designation(cls, v: str) -> str:
        cleaned = (v or "").strip()
        if not cleaned:
            raise ValueError("Employee designation cannot be empty.")
        if len(cleaned) > 120:
            raise ValueError("Employee designation cannot exceed 120 characters.")
        return cleaned


class EmployeeUpdateInput(BaseModel):
    """Validated schema for updating an existing employee record while preserving unspecified fields."""

    employee_id: int = Field(..., description="Unique employee ID to update.")
    name: Optional[str] = Field(None, description="Updated full name.")
    email: Optional[str] = Field(None, description="Updated unique email address.")
    designation: Optional[str] = Field(None, description="Updated job title / designation.")
    department_id: Optional[int] = Field(None, description="Updated department ID.")
    department_name: Optional[str] = Field(None, description="Updated department name.")

    @_field_validator("name")
    @classmethod
    def validate_opt_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Employee name cannot be blank.")
        return cleaned

    @_field_validator("email")
    @classmethod
    def validate_opt_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip().lower()
        if not EMAIL_REGEX.match(cleaned):
            raise ValueError(f"Invalid email address format: '{v}'.")
        return cleaned

    @_field_validator("designation")
    @classmethod
    def validate_opt_designation(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Employee designation cannot be blank.")
        return cleaned


class EmployeeDeleteInput(BaseModel):
    """Validated schema for deleting an employee."""

    employee_id: Optional[int] = Field(None, description="Unique ID of the employee to delete.")
    name: Optional[str] = Field(None, description="Name of the employee if ID is not specified.")


class EmployeeQueryInput(BaseModel):
    """Validated schema for reading/searching employees."""

    employee_id: Optional[int] = Field(None, description="Exact employee ID to retrieve.")
    name: Optional[str] = Field(None, description="Employee name or substring to search.")
    email: Optional[str] = Field(None, description="Exact email address to search.")
    department_id: Optional[int] = Field(None, description="Filter employees by department ID.")
    department_name: Optional[str] = Field(None, description="Filter employees by department name.")
    designation: Optional[str] = Field(None, description="Filter employees by designation substring.")
