"""Delete Agent implementation enforcing two-phase confirmation and referential integrity."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from langchain_core.tools import StructuredTool

from app.database.models import model_to_dict
from app.logging.setup import log_event
from app.services.permissions import AGENT_TOOL_PERMISSIONS, enforce_tool_permission
from app.services.validation import (
    AmbiguousTargetError,
    ConfirmationRequiredError,
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    ReferentialIntegrityError,
    ValidationErrorApp,
)
from app.state import AgentState, ToolExecutionResult, load_prompt


class DeleteAgent:
    """Specialized agent for safely preparing and executing confirmed deletions."""

    AGENT_NAME = "delete_agent"

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
        intent = state.get("operation_intent", "delete")
        entity = state.get("target_entity", "employee")
        args = dict(state.get("structured_arguments") or {})

        requested_tool = args.pop("requested_tool", None)
        if requested_tool is not None:
            enforce_tool_permission(self.AGENT_NAME, requested_tool)

        action = args.get("action") or ("execute" if intent == "confirm" else "prepare")

        # Hard safety rule: an initial "delete" intent can NEVER execute deletion in the same step
        if intent == "delete" and action == "execute":
            action = "prepare"

        tool_name = (
            f"{action}_delete_department" if entity == "department" else f"{action}_delete_employee"
        )

        try:
            if action == "prepare":
                if entity == "department":
                    dept_id = args.get("department_id")
                    name = args.get("name") or args.get("department_name")
                    if dept_id is None and not name:
                        res = ToolExecutionResult(
                            agent_name=self.AGENT_NAME,
                            tool_name=tool_name,
                            status="clarify",
                            data={},
                            message="Which department would you like to delete? Please provide the department name or ID.",
                            error_category="missing_target",
                        )
                        return {"tool_result": model_to_dict(res), "status": "awaiting_clarification"}

                    raw = self.invoke_tool(
                        "prepare_delete_department",
                        {
                            "conversation_id": conv_id,
                            "department_id": dept_id,
                            "name": name,
                        },
                    )
                    pending = raw["pending_confirmation"]
                    dept = raw["department"]
                    msg = (
                        f"Confirmation required: You are about to permanently delete department "
                        f"'{dept['name']}' (ID {dept['id']}). "
                        f"[Confirmation ID: {pending['confirmation_id']}] "
                        f"Please reply 'yes' or 'confirm' to proceed, or 'no' / 'cancel' to abort."
                    )
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name="prepare_delete_department",
                        status="awaiting_confirmation",
                        data=raw,
                        message=msg,
                    )
                    return {
                        "tool_result": model_to_dict(res),
                        "pending_confirmation": pending,
                        "status": "awaiting_confirmation",
                    }
                else:
                    emp_id = args.get("employee_id")
                    name = args.get("name") or args.get("target_name")
                    email = args.get("email")
                    if emp_id is None and not name and not email:
                        res = ToolExecutionResult(
                            agent_name=self.AGENT_NAME,
                            tool_name=tool_name,
                            status="clarify",
                            data={
                                "pending_clarification": {
                                    "operation_intent": "delete",
                                    "target_entity": "employee",
                                    "partial_arguments": {},
                                    "missing_fields": ["employee_id"],
                                }
                            },
                            message="Which employee would you like to delete? Please provide their employee ID or full name.",
                            error_category="missing_target",
                        )
                        return {"tool_result": model_to_dict(res), "status": "awaiting_clarification"}

                    raw = self.invoke_tool(
                        "prepare_delete_employee",
                        {
                            "conversation_id": conv_id,
                            "employee_id": emp_id,
                            "name": name,
                            "email": email,
                        },
                    )
                    pending = raw["pending_confirmation"]
                    emp = raw["employee"]
                    msg = (
                        f"Confirmation required: You are about to permanently delete employee "
                        f"{emp['name']} (ID {emp['id']}, {emp['designation']} in {emp['department_name']}). "
                        f"[Confirmation ID: {pending['confirmation_id']}] "
                        f"Please reply 'yes' or 'confirm' to proceed, or 'no' / 'cancel' to abort."
                    )
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name="prepare_delete_employee",
                        status="awaiting_confirmation",
                        data=raw,
                        message=msg,
                    )
                    return {
                        "tool_result": model_to_dict(res),
                        "pending_confirmation": pending,
                        "status": "awaiting_confirmation",
                    }

            else:
                # Phase 2: Execute confirmed deletion
                confirmation_id = args.get("confirmation_id") or ""
                if entity == "department":
                    dept_id = args.get("department_id") or args.get("target_id")
                    if dept_id is None:
                        raise ConfirmationRequiredError("Missing confirmed department_id for deletion.")
                    raw = self.invoke_tool(
                        "execute_delete_department",
                        {
                            "conversation_id": conv_id,
                            "confirmation_id": confirmation_id,
                            "department_id": int(dept_id),
                        },
                    )
                    deleted = raw["deleted_department"]
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name="execute_delete_department",
                        status="completed",
                        data=raw,
                        message=f"Confirmed and permanently deleted department '{deleted['name']}' (ID {deleted['id']}).",
                    )
                else:
                    emp_id = args.get("employee_id") or args.get("target_id")
                    if emp_id is None:
                        raise ConfirmationRequiredError("Missing confirmed employee_id for deletion.")
                    raw = self.invoke_tool(
                        "execute_delete_employee",
                        {
                            "conversation_id": conv_id,
                            "confirmation_id": confirmation_id,
                            "employee_id": int(emp_id),
                        },
                    )
                    deleted = raw["deleted_employee"]
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name="execute_delete_employee",
                        status="completed",
                        data=raw,
                        message=(
                            f"Confirmed and permanently deleted employee {deleted['name']} "
                            f"(ID {deleted['id']}, {deleted['designation']} in {deleted['department_name']})."
                        ),
                    )

                elapsed = (time.perf_counter() - start_ts) * 1000
                log_event(
                    logging.INFO,
                    res.message,
                    conversation_id=conv_id,
                    agent_name=self.AGENT_NAME,
                    operation=tool_name,
                    status="completed",
                    duration_ms=elapsed,
                )
                return {
                    "tool_result": model_to_dict(res),
                    "pending_confirmation": None,
                    "status": "completed",
                }

        except AmbiguousTargetError as exc:
            elapsed = (time.perf_counter() - start_ts) * 1000
            res = ToolExecutionResult(
                agent_name=self.AGENT_NAME,
                tool_name=tool_name,
                status="clarify",
                data={
                    "candidates": exc.candidates,
                    "pending_clarification": {
                        "operation_intent": "delete",
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

        except (
            ReferentialIntegrityError,
            ConfirmationRequiredError,
            NotFoundError,
            ValidationErrorApp,
            PermissionDeniedError,
            DomainError,
        ) as exc:
            elapsed = (time.perf_counter() - start_ts) * 1000
            status_code = (
                "referential_integrity_error"
                if isinstance(exc, ReferentialIntegrityError)
                else (
                    "confirmation_error"
                    if isinstance(exc, ConfirmationRequiredError)
                    else ("not_found" if isinstance(exc, NotFoundError) else "error")
                )
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
