"""Update Agent implementation restricted to Employee and Department update tools."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from langchain_core.tools import StructuredTool

from app.database.models import model_to_dict
from app.logging.setup import log_event
from app.services.employee_service import EmployeeInformationService
from app.services.permissions import AGENT_TOOL_PERMISSIONS, enforce_tool_permission
from app.services.validation import (
    AmbiguousTargetError,
    DomainError,
    DuplicateRecordError,
    NotFoundError,
    PermissionDeniedError,
    ValidationErrorApp,
)
from app.state import AgentState, ToolExecutionResult, load_prompt


class UpdateAgent:
    """Specialized agent for updating existing Employee and Department records."""

    AGENT_NAME = "update_agent"

    def __init__(
        self,
        tools: Dict[str, StructuredTool],
        service: EmployeeInformationService,
    ) -> None:
        self.prompt = load_prompt(self.AGENT_NAME)
        allowed = AGENT_TOOL_PERMISSIONS[self.AGENT_NAME]
        self.tools = {k: v for k, v in tools.items() if k in allowed}
        self.service = service

    def invoke_tool(self, tool_name: str, payload: Dict[str, Any]) -> Any:
        enforce_tool_permission(self.AGENT_NAME, tool_name)
        if tool_name not in self.tools:
            raise PermissionDeniedError(f"Tool '{tool_name}' is not bound to {self.AGENT_NAME}.")
        call_args = dict(payload)
        call_args["caller_agent"] = self.AGENT_NAME
        return self.tools[tool_name].invoke(call_args)

    def run(self, state: AgentState) -> Dict[str, Any]:
        start_ts = time.perf_counter()
        conv_id = state.get("conversation_id", "-")
        entity = state.get("target_entity", "employee")
        args = dict(state.get("structured_arguments") or {})

        requested_tool = args.pop("requested_tool", None)
        if requested_tool is not None:
            enforce_tool_permission(self.AGENT_NAME, requested_tool)

        tool_name = "update_department" if entity == "department" else "update_employee"
        try:
            if entity == "department":
                dept_id = args.get("department_id")
                current_name = args.get("current_name") or args.get("name")
                new_name = args.get("new_name")
                description = args.get("description")

                if dept_id is None and not current_name:
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name=tool_name,
                        status="clarify",
                        data={},
                        message="Which department would you like to update? Please specify the department name or ID.",
                        error_category="missing_target",
                    )
                    return {"tool_result": model_to_dict(res), "status": "awaiting_clarification"}

                if new_name is None and description is None:
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name=tool_name,
                        status="clarify",
                        data={
                            "pending_clarification": {
                                "operation_intent": "update",
                                "target_entity": "department",
                                "partial_arguments": args,
                                "missing_fields": ["new_name_or_description"],
                            }
                        },
                        message="Please specify the new name or description for the department.",
                        error_category="missing_update_fields",
                    )
                    return {"tool_result": model_to_dict(res), "status": "awaiting_clarification"}

                raw = self.invoke_tool(
                    "update_department",
                    {
                        "conversation_id": conv_id,
                        "department_id": dept_id,
                        "current_name": current_name,
                        "new_name": new_name,
                        "description": description,
                    },
                )
                dept = raw["department"]
                res = ToolExecutionResult(
                    agent_name=self.AGENT_NAME,
                    tool_name="update_department",
                    status="success",
                    data=raw,
                    message=(
                        f"Updated department ID {dept['id']}: name='{dept['name']}', "
                        f"description='{dept.get('description') or ''}'."
                    ),
                )
            else:
                emp_id = args.get("employee_id")
                target_name = args.get("target_name")
                target_email = args.get("target_email")

                if emp_id is None:
                    if target_name or target_email:
                        resolved = self.service.resolve_unique_employee(
                            name=target_name,
                            email=target_email,
                        )
                        emp_id = int(resolved["id"])
                    else:
                        res = ToolExecutionResult(
                            agent_name=self.AGENT_NAME,
                            tool_name=tool_name,
                            status="clarify",
                            data={
                                "pending_clarification": {
                                    "operation_intent": "update",
                                    "target_entity": "employee",
                                    "partial_arguments": args,
                                    "missing_fields": ["employee_id"],
                                }
                            },
                            message="Which employee would you like to update? Please provide their employee ID or full name.",
                            error_category="ambiguous_or_missing_target",
                        )
                        return {"tool_result": model_to_dict(res), "status": "awaiting_clarification"}

                new_name = args.get("name")
                new_email = args.get("email")
                new_designation = args.get("designation")
                new_dept_id = args.get("department_id")
                new_dept_name = args.get("department_name")

                if all(v is None for v in (new_name, new_email, new_designation, new_dept_id, new_dept_name)):
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name=tool_name,
                        status="clarify",
                        data={
                            "pending_clarification": {
                                "operation_intent": "update",
                                "target_entity": "employee",
                                "partial_arguments": {"employee_id": emp_id},
                                "missing_fields": ["update_field"],
                            }
                        },
                        message=f"What would you like to update for employee ID {emp_id} (name, email, designation, or department)?",
                        error_category="missing_update_fields",
                    )
                    return {"tool_result": model_to_dict(res), "status": "awaiting_clarification"}

                raw = self.invoke_tool(
                    "update_employee",
                    {
                        "conversation_id": conv_id,
                        "employee_id": int(emp_id),
                        "name": new_name,
                        "email": new_email,
                        "designation": new_designation,
                        "department_id": new_dept_id,
                        "department_name": new_dept_name,
                    },
                )
                emp = raw["employee"]
                res = ToolExecutionResult(
                    agent_name=self.AGENT_NAME,
                    tool_name="update_employee",
                    status="success",
                    data=raw,
                    message=(
                        f"Updated employee {emp['name']} (ID {emp['id']}): "
                        f"designation='{emp['designation']}', department='{emp['department_name']}', "
                        f"email='{emp['email']}'."
                    ),
                )

            elapsed = (time.perf_counter() - start_ts) * 1000
            log_event(
                logging.INFO,
                res.message,
                conversation_id=conv_id,
                agent_name=self.AGENT_NAME,
                operation=tool_name,
                status="success",
                duration_ms=elapsed,
            )
            return {"tool_result": model_to_dict(res), "status": "completed"}

        except AmbiguousTargetError as exc:
            elapsed = (time.perf_counter() - start_ts) * 1000
            res = ToolExecutionResult(
                agent_name=self.AGENT_NAME,
                tool_name=tool_name,
                status="clarify",
                data={
                    "candidates": exc.candidates,
                    "pending_clarification": {
                        "operation_intent": "update",
                        "target_entity": entity,
                        "partial_arguments": args,
                        "missing_fields": ["employee_id"],
                    },
                },
                message=str(exc),
                error_category=exc.error_category,
            )
            log_event(
                logging.INFO,
                str(exc),
                conversation_id=conv_id,
                agent_name=self.AGENT_NAME,
                operation=tool_name,
                status="clarify",
                duration_ms=elapsed,
                error_category=exc.error_category,
            )
            return {"tool_result": model_to_dict(res), "status": "awaiting_clarification"}

        except (NotFoundError, DuplicateRecordError, ValidationErrorApp, PermissionDeniedError, DomainError) as exc:
            elapsed = (time.perf_counter() - start_ts) * 1000
            status_code = "not_found" if isinstance(exc, NotFoundError) else "error"
            res = ToolExecutionResult(
                agent_name=self.AGENT_NAME,
                tool_name=tool_name,
                status=status_code,
                data={},
                message=str(exc),
                error_category=getattr(exc, "error_category", "domain_error"),
            )
            log_event(
                logging.WARNING,
                str(exc),
                conversation_id=conv_id,
                agent_name=self.AGENT_NAME,
                operation=tool_name,
                status=status_code,
                duration_ms=elapsed,
                error_category=res.error_category,
            )
            return {"tool_result": model_to_dict(res), "status": "error"}
        except Exception as exc:
            elapsed = (time.perf_counter() - start_ts) * 1000
            res = ToolExecutionResult(
                agent_name=self.AGENT_NAME,
                tool_name=tool_name,
                status="validation_error",
                data={},
                message=str(exc),
                error_category="validation_error",
            )
            return {"tool_result": model_to_dict(res), "status": "error"}
