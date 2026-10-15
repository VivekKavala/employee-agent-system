"""Restricted LangChain StructuredTools for Employee operations with caller permission checks."""
from __future__ import annotations

from typing import Any, Dict, Optional
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.memory.conversation_memory import ConversationMemoryStore
from app.services.employee_service import EmployeeInformationService
from app.services.permissions import enforce_tool_permission


class ListEmployeesSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be read_agent).")


class GetEmployeeSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be read_agent).")
    employee_id: Optional[int] = Field(None, description="Exact employee ID.")
    name: Optional[str] = Field(None, description="Employee name to resolve.")
    email: Optional[str] = Field(None, description="Employee email to resolve.")


class SearchEmployeesSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be read_agent).")
    employee_id: Optional[int] = Field(None, description="Exact employee ID.")
    name: Optional[str] = Field(None, description="Employee name substring.")
    email: Optional[str] = Field(None, description="Exact employee email.")
    department_id: Optional[int] = Field(None, description="Department ID filter.")
    department_name: Optional[str] = Field(None, description="Department name filter.")
    designation: Optional[str] = Field(None, description="Designation substring filter.")


class CreateEmployeeSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be create_agent).")
    conversation_id: str = Field(default="-", description="Active conversation ID.")
    name: str = Field(..., description="Required employee full name.")
    email: str = Field(..., description="Required unique employee email address.")
    designation: str = Field(..., description="Required job title / designation.")
    department_id: Optional[int] = Field(None, description="Target department ID.")
    department_name: Optional[str] = Field(None, description="Target department name.")


class UpdateEmployeeSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be update_agent).")
    conversation_id: str = Field(default="-", description="Active conversation ID.")
    employee_id: int = Field(..., description="ID of the employee to update.")
    name: Optional[str] = Field(None, description="Updated employee name.")
    email: Optional[str] = Field(None, description="Updated employee email.")
    designation: Optional[str] = Field(None, description="Updated job title / designation.")
    department_id: Optional[int] = Field(None, description="Updated department ID.")
    department_name: Optional[str] = Field(None, description="Updated department name.")


class PrepareDeleteEmployeeSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be delete_agent).")
    conversation_id: str = Field(..., description="Active conversation ID.")
    employee_id: Optional[int] = Field(None, description="Employee ID to prepare for deletion.")
    name: Optional[str] = Field(None, description="Employee name if ID is not provided.")
    email: Optional[str] = Field(None, description="Employee email if ID is not provided.")


class ExecuteDeleteEmployeeSchema(BaseModel):
    caller_agent: str = Field(..., description="Name of the invoking agent (must be delete_agent).")
    conversation_id: str = Field(..., description="Active conversation ID.")
    confirmation_id: str = Field(..., description="Bound confirmation token from prepare_delete_employee.")
    employee_id: int = Field(..., description="Confirmed employee ID to delete.")


def build_employee_tools(
    service: EmployeeInformationService,
    memory: ConversationMemoryStore,
) -> Dict[str, StructuredTool]:
    """Create and return the registry of restricted Employee tools."""

    def _list_employees(caller_agent: str) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "list_employees")
        records = service.list_employees()
        return {"employees": records, "count": len(records)}

    def _get_employee(
        caller_agent: str,
        employee_id: Optional[int] = None,
        name: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "get_employee")
        record = service.resolve_unique_employee(
            employee_id=employee_id,
            name=name,
            email=email,
        )
        return {"employee": record}

    def _search_employees(
        caller_agent: str,
        employee_id: Optional[int] = None,
        name: Optional[str] = None,
        email: Optional[str] = None,
        department_id: Optional[int] = None,
        department_name: Optional[str] = None,
        designation: Optional[str] = None,
    ) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "search_employees")
        records = service.search_employees(
            employee_id=employee_id,
            name=name,
            email=email,
            department_id=department_id,
            department_name=department_name,
            designation=designation,
        )
        return {
            "employees": records,
            "count": len(records),
            "filters": {
                k: v
                for k, v in {
                    "employee_id": employee_id,
                    "name": name,
                    "email": email,
                    "department_id": department_id,
                    "department_name": department_name,
                    "designation": designation,
                }.items()
                if v is not None
            },
        }

    def _create_employee(
        caller_agent: str,
        name: str,
        email: str,
        designation: str,
        conversation_id: str = "-",
        department_id: Optional[int] = None,
        department_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "create_employee")
        created = service.create_employee(
            name=name,
            email=email,
            designation=designation,
            department_id=department_id,
            department_name=department_name,
            conversation_id=conversation_id,
            agent_name=caller_agent,
        )
        return {"employee": created}

    def _update_employee(
        caller_agent: str,
        employee_id: int,
        conversation_id: str = "-",
        name: Optional[str] = None,
        email: Optional[str] = None,
        designation: Optional[str] = None,
        department_id: Optional[int] = None,
        department_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "update_employee")
        updated = service.update_employee(
            employee_id=employee_id,
            name=name,
            email=email,
            designation=designation,
            department_id=department_id,
            department_name=department_name,
            conversation_id=conversation_id,
            agent_name=caller_agent,
        )
        return {"employee": updated}

    def _prepare_delete_employee(
        caller_agent: str,
        conversation_id: str,
        employee_id: Optional[int] = None,
        name: Optional[str] = None,
        email: Optional[str] = None,
    ) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "prepare_delete_employee")
        target = service.resolve_unique_employee(
            employee_id=employee_id,
            name=name,
            email=email,
        )
        emp_id = int(target["id"])
        summary = (
            f"Employee {target['name']} (ID {emp_id}, {target['designation']}, "
            f"Department: {target['department_name']}, Email: {target['email']})"
        )
        pending = memory.create_pending_confirmation(
            conversation_id=conversation_id,
            operation="delete_employee",
            target_entity="employee",
            target_id=emp_id,
            target_summary=summary,
            payload={"employee_id": emp_id, "employee": target},
        )
        return {
            "pending_confirmation": pending.to_dict(),
            "employee": target,
        }

    def _execute_delete_employee(
        caller_agent: str,
        conversation_id: str,
        confirmation_id: str,
        employee_id: int,
    ) -> Dict[str, Any]:
        enforce_tool_permission(caller_agent, "execute_delete_employee")
        consumed = memory.consume_pending_confirmation(
            conversation_id,
            expected_confirmation_id=confirmation_id,
            expected_target_entity="employee",
            expected_target_id=employee_id,
        )
        deleted = service.delete_employee(
            employee_id=consumed.target_id,
            conversation_id=conversation_id,
            agent_name=caller_agent,
        )
        memory.clear_employee_from_context_if_matches(conversation_id, consumed.target_id)
        return {
            "deleted_employee": deleted,
            "confirmation_id": consumed.confirmation_id,
        }

    return {
        "list_employees": StructuredTool.from_function(
            func=_list_employees,
            name="list_employees",
            description="List all employee records ordered by employee ID.",
            args_schema=ListEmployeesSchema,
        ),
        "get_employee": StructuredTool.from_function(
            func=_get_employee,
            name="get_employee",
            description="Retrieve a single unambiguous employee by ID, name, or email.",
            args_schema=GetEmployeeSchema,
        ),
        "search_employees": StructuredTool.from_function(
            func=_search_employees,
            name="search_employees",
            description="Search and filter employees by department, designation, name, or email.",
            args_schema=SearchEmployeesSchema,
        ),
        "create_employee": StructuredTool.from_function(
            func=_create_employee,
            name="create_employee",
            description="Create a new employee record after validating uniqueness and department existence.",
            args_schema=CreateEmployeeSchema,
        ),
        "update_employee": StructuredTool.from_function(
            func=_update_employee,
            name="update_employee",
            description="Update specified fields on an existing employee record while preserving unspecified fields.",
            args_schema=UpdateEmployeeSchema,
        ),
        "prepare_delete_employee": StructuredTool.from_function(
            func=_prepare_delete_employee,
            name="prepare_delete_employee",
            description="Verify an employee record and stage a pending deletion confirmation without deleting.",
            args_schema=PrepareDeleteEmployeeSchema,
        ),
        "execute_delete_employee": StructuredTool.from_function(
            func=_execute_delete_employee,
            name="execute_delete_employee",
            description="Execute a previously confirmed employee deletion using its bound confirmation_id.",
            args_schema=ExecuteDeleteEmployeeSchema,
        ),
    }
