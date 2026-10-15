"""Restricted LangChain StructuredTools for Department operations with caller permission checks."""
from __future__ import annotations

from typing import Any, Dict, Optional
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.memory.conversation_memory import ConversationMemoryStore
from app.services.employee_service import EmployeeInformationService
from app.services.permissions import enforce_tool_permission
from app.services.validation import ReferentialIntegrityError


class ListDepartmentsSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be read_agent).")


class GetDepartmentSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be read_agent).")
    department_id: Optional[int] = Field(None, description="Department ID to retrieve.")
    name: Optional[str] = Field(None, description="Department name to retrieve.")


class GetDepartmentCountsSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be read_agent).")


class CreateDepartmentSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be create_agent).")
    conversation_id: str = Field(default="-", description="Active conversation ID.")
    name: str = Field(..., description="Unique department name.")
    description: Optional[str] = Field(None, description="Optional department description.")


class UpdateDepartmentSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be update_agent).")
    conversation_id: str = Field(default="-", description="Active conversation ID.")
    department_id: Optional[int] = Field(None, description="ID of the department to update.")
    current_name: Optional[str] = Field(None, description="Current name of the department to update.")
    new_name: Optional[str] = Field(None, description="Updated department name.")
    description: Optional[str] = Field(None, description="Updated department description.")


class PrepareDeleteDepartmentSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be delete_agent).")
    conversation_id: str = Field(..., description="Active conversation ID.")
    department_id: Optional[int] = Field(None, description="Department ID to prepare for deletion.")
    name: Optional[str] = Field(None, description="Department name if ID is not provided.")


class ExecuteDeleteDepartmentSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be delete_agent).")
    conversation_id: str = Field(..., description="Active conversation ID.")
    confirmation_id: str = Field(..., description="Bound confirmation token from prepare_delete_department.")
    department_id: int = Field(..., description="Confirmed department ID to delete.")


def build_department_tools(
    service: EmployeeInformationService,
    memory: ConversationMemoryStore,
) -> Dict[str, StructuredTool]:
    """Create and return the registry of restricted Department tools."""

    def _list_departments(caller_agent: str) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "list_departments")
        records = service.list_departments(include_counts=True)
        return {"departments": records, "count": len(records)}

    def _get_department(
        caller_agent: str,
        department_id: Optional[int] = None,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "get_department")
        dept = service.get_department(department_id=department_id, name=name)
        return {"department": dept}

    def _get_department_employee_counts(caller_agent: str) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "get_department_employee_counts")
        departments = service.list_departments(include_counts=True)
        total_employees = sum(int(d.get("employee_count", 0)) for d in departments)
        return {
            "departments": departments,
            "department_count": len(departments),
            "total_employees": total_employees,
        }

    def _create_department(
        caller_agent: str,
        name: str,
        conversation_id: str = "-",
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "create_department")
        created = service.create_department(
            name=name,
            description=description,
            conversation_id=conversation_id,
            agent_name=caller_agent,
        )
        return {"department": created}

    def _update_department(
        caller_agent: str,
        conversation_id: str = "-",
        department_id: Optional[int] = None,
        current_name: Optional[str] = None,
        new_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "update_department")
        updated = service.update_department(
            department_id=department_id,
            current_name=current_name,
            new_name=new_name,
            description=description,
            update_description=(description is not None),
            conversation_id=conversation_id,
            agent_name=caller_agent,
        )
        return {"department": updated}

    def _prepare_delete_department(
        caller_agent: str,
        conversation_id: str,
        department_id: Optional[int] = None,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "prepare_delete_department")
        target = service.get_department(department_id=department_id, name=name)
        dept_id = int(target["id"])
        emp_count = int(target.get("employee_count", 0))
        if emp_count > 0:
            raise ReferentialIntegrityError(
                f"Cannot delete department '{target['name']}' (ID {dept_id}) because {emp_count} "
                f"employee(s) are still assigned to it. Reassign or remove those employees first."
            )

        summary = f"Department '{target['name']}' (ID {dept_id}, 0 assigned employees)"
        pending = memory.create_pending_confirmation(
            conversation_id=conversation_id,
            operation="delete_department",
            target_entity="department",
            target_id=dept_id,
            target_summary=summary,
            payload={"department_id": dept_id, "department": target},
        )
        return {
            "pending_confirmation": pending.to_dict(),
            "department": target,
        }

    def _execute_delete_department(
        caller_agent: str,
        conversation_id: str,
        confirmation_id: str,
        department_id: int,
    ) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "execute_delete_department")
        consumed = memory.consume_pending_confirmation(
            conversation_id,
            expected_confirmation_id=confirmation_id,
            expected_target_entity="department",
            expected_target_id=department_id,
        )
        deleted = service.delete_department(
            department_id=consumed.target_id,
            conversation_id=conversation_id,
            agent_name=caller_agent,
        )
        return {
            "deleted_department": deleted,
            "confirmation_id": consumed.confirmation_id,
        }

    return {
        "list_departments": StructuredTool.from_function(
            func=_list_departments,
            name="list_departments",
            description="List all departments along with their employee counts.",
            args_schema=ListDepartmentsSchema,
        ),
        "get_department": StructuredTool.from_function(
            func=_get_department,
            name="get_department",
            description="Retrieve a single department by ID or name.",
            args_schema=GetDepartmentSchema,
        ),
        "get_department_employee_counts": StructuredTool.from_function(
            func=_get_department_employee_counts,
            name="get_department_employee_counts",
            description="Get employee headcount breakdown across all departments.",
            args_schema=GetDepartmentCountsSchema,
        ),
        "create_department": StructuredTool.from_function(
            func=_create_department,
            name="create_department",
            description="Create a new department with a unique name and optional description.",
            args_schema=CreateDepartmentSchema,
        ),
        "update_department": StructuredTool.from_function(
            func=_update_department,
            name="update_department",
            description="Update a department's name or description.",
            args_schema=UpdateDepartmentSchema,
        ),
        "prepare_delete_department": StructuredTool.from_function(
            func=_prepare_delete_department,
            name="prepare_delete_department",
            description="Verify an empty department and stage a pending deletion confirmation without deleting.",
            args_schema=PrepareDeleteDepartmentSchema,
        ),
        "execute_delete_department": StructuredTool.from_function(
            func=_execute_delete_department,
            name="execute_delete_department",
            description="Execute a previously confirmed department deletion using its bound confirmation_id.",
            args_schema=ExecuteDeleteDepartmentSchema,
        ),
    }
