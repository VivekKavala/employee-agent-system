"""Domain exceptions and deterministic input validation rules."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.database.models import EMAIL_REGEX


class DomainError(Exception):
    """Base class for expected domain and business-rule errors."""

    error_category: str = "domain_error"


class ValidationErrorApp(DomainError):
    """Raised when required fields are missing or field values are invalid."""

    error_category = "validation_error"


class NotFoundError(DomainError):
    """Raised when a requested employee or department record does not exist."""

    error_category = "not_found"


class DuplicateRecordError(DomainError):
    """Raised when creating or updating a record violates uniqueness constraints."""

    error_category = "duplicate_record"


class ReferentialIntegrityError(DomainError):
    """Raised when an operation violates foreign-key or department-membership rules."""

    error_category = "referential_integrity"


class AmbiguousTargetError(DomainError):
    """Raised when a request matches multiple records and requires user clarification."""

    error_category = "ambiguous_target"

    def __init__(self, message: str, candidates: Optional[List[Dict[str, Any]]] = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


class PermissionDeniedError(DomainError):
    """Raised when an agent or caller attempts an unauthorized tool or operation."""

    error_category = "permission_denied"


class ConfirmationRequiredError(DomainError):
    """Raised when a destructive operation is attempted without a valid bound confirmation."""

    error_category = "confirmation_required"


class OutOfScopeError(DomainError):
    """Raised when a request falls outside the Employee and Department domain."""

    error_category = "out_of_scope"


def validate_email_address(email: Optional[str], *, required: bool = True) -> Optional[str]:
    """Validate and normalize an employee email address."""
    if email is None or not str(email).strip():
        if required:
            raise ValidationErrorApp("Employee email is required (e.g., 'name@example.com').")
        return None
    cleaned = str(email).strip().lower()
    if not EMAIL_REGEX.match(cleaned):
        raise ValidationErrorApp(f"Invalid email format '{email}'. Expected a valid address like 'user@example.com'.")
    return cleaned


def validate_non_empty_text(value: Optional[str], field_name: str, max_length: int = 120) -> str:
    """Validate that a required string field is non-empty and within length limits."""
    if value is None or not str(value).strip():
        raise ValidationErrorApp(f"{field_name} is required and cannot be empty.")
    cleaned = str(value).strip()
    if len(cleaned) > max_length:
        raise ValidationErrorApp(f"{field_name} cannot exceed {max_length} characters.")
    return cleaned


def validate_positive_id(value: Optional[int], field_name: str = "ID") -> int:
    """Validate that an entity identifier is a positive integer."""
    if value is None or isinstance(value, bool):
        raise ValidationErrorApp(f"A valid integer {field_name} is required.")
    try:
        int_val = int(value)
    except (ValueError, TypeError) as exc:
        raise ValidationErrorApp(f"{field_name} must be an integer, got '{value}'.") from exc
    if int_val <= 0:
        raise ValidationErrorApp(f"{field_name} must be a positive integer, got {int_val}.")
    return int_val


def generate_default_email(name: str, existing_Check_fn=None) -> str:
    """Generate a deterministic corporate email from employee name ONLY when explicitly requested by helper."""
    tokens = [ re.sub(r"[^a-z0-9]", "", t.lower()) for t in name.strip().split() ]
    tokens = [ t for t in tokens if t ]
    if not tokens:
        raise ValidationErrorApp("Cannot derive email from empty employee name.")
    local = ".".join(tokens)
    return f"{local}@example.com"
