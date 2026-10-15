"""Create Agent implementation restricted to Employee and Department creation tools."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from langchain_core.tools import StructuredTool

from app.database.models import model_to_dict
from app.logging.setup import log_event
from app.services.permissions import AGENT_TOOL_PERMISSIONS, enforce_tool_permission
from app.services.validation import (
    DomainError,
    DuplicateRecordError,
    NotFoundError,
    PermissionDeniedError,
    ValidationErrorApp,
    generate_default_email,
)
from app.state import AgentState, ToolExecutionResult, load_prompt


class CreateAgent:
    """Specialized agent for creating new Employee and Department records."""

    AGENT_NAME = "create_agent"

    def __init__(self, tools: Dict[str, StructuredTool]) -> None:
        self.prompt = load_prompt(self.AGENT_NAME)
        allowed = AGENT_TOOL_PERMISSIONS[self.AGENT_NAME]
        self.tools = {k: v for k, v in tools.items() if k in allowed}

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

        tool_name = "create_department" if entity == "department" else "create_employee"
        try:
            if entity == "department":
                name = (args.get("name") or args.get("department_name") or "").strip()
                if not name:
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name=tool_name,
                        status="clarify",
                        data={
                            "pending_clarification": {
                                "operation_intent": "create",
                                "target_entity": "department",
                                "partial_arguments": args,
                                "missing_fields": ["name"],
                            }
                        },
                        message="To create a department, please provide the department name.",
                        error_category="missing_required_fields",
                    )
                    return {"tool_result": model_to_dict(res), "status": "awaiting_clarification"}

                raw = self.invoke_tool(
                    "create_department",
                    {
                        "conversation_id": conv_id,
                        "name": name,
                        "description": args.get("description"),
                    },
                )
                dept = raw["department"]
                res = ToolExecutionResult(
                    agent_name=self.AGENT_NAME,
                    tool_name="create_department",
                    status="success",
                    data=raw,
                    message=f"Created department '{dept['name']}' (ID {dept['id']}).",
                )
            else:
                name = (args.get("name") or "").strip()
                designation = (args.get("designation") or "").strip()
                dept_id = args.get("department_id")
                dept_name = (args.get("department_name") or "").strip() or None
                email = (args.get("email") or "").strip() or None

                missing: List[str] = []
                if not name:
                    missing.append("name")
                if not designation:
                    missing.append("designation")
                if dept_id is None and not dept_name:
                    missing.append("department")

                if missing:
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name=tool_name,
                        status="clarify",
                        data={
                            "pending_clarification": {
                                "operation_intent": "create",
                                "target_entity": "employee",
                                "partial_arguments": args,
                                "missing_fields": missing,
                            }
                        },
                        message=(
                            f"To add this employee, please provide the missing required information: "
                            f"{', '.join(missing)}."
                        ),
                        error_category="missing_required_fields",
                    )
                    return {"tool_result": model_to_dict(res), "status": "awaiting_clarification"}

                if not email:
                    email = generate_default_email(name)

                raw = self.invoke_tool(
                    "create_employee",
                    {
                        "conversation_id": conv_id,
                        "name": name,
                        "email": email,
                        "designation": designation,
                        "department_id": dept_id,
                        "department_name": dept_name,
                    },
                )
                emp = raw["employee"]
                res = ToolExecutionResult(
                    agent_name=self.AGENT_NAME,
                    tool_name="create_employee",
                    status="success",
                    data=raw,
                    message=(
                        f"Created employee {emp['name']} (ID {emp['id']}) as {emp['designation']} "
                        f"in {emp['department_name']} (email: {emp['email']})."
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

        except (DuplicateRecordError, NotFoundError, ValidationErrorApp, PermissionDeniedError, DomainError) as exc:
            elapsed = (time.perf_counter() - start_ts) * 1000
            status_code = "duplicate_error" if isinstance(exc, DuplicateRecordError) else (
                "not_found" if isinstance(exc, NotFoundError) else "validation_error"
            )
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
            log_event(
                logging.WARNING,
                str(exc),
                conversation_id=conv_id,
                agent_name=self.AGENT_NAME,
                operation=tool_name,
                status="validation_error",
                duration_ms=elapsed,
                error_category="validation_error",
            )
            return {"tool_result": model_to_dict(res), "status": "error"}
