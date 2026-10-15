"""Read Agent implementation restricted to read-only Employee and Department tools."""
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
    DomainError,
    NotFoundError,
    PermissionDeniedError,
    ValidationErrorApp,
)
from app.state import AgentState, ToolExecutionResult, load_prompt


class ReadAgent:
    """Specialized agent for reading and searching employees, departments, and headcounts."""

    AGENT_NAME = "read_agent"

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

        # Reject any explicit unauthorized tool override in structured_arguments
        requested_tool = args.pop("requested_tool", None)
        if requested_tool is not None:
            enforce_tool_permission(self.AGENT_NAME, requested_tool)

        tool_name = "list_employees"
        try:
            if entity == "organization" or args.get("query_type") == "department_counts":
                tool_name = "get_department_employee_counts"
                raw = self.invoke_tool(tool_name, {})
                res = ToolExecutionResult(
                    agent_name=self.AGENT_NAME,
                    tool_name=tool_name,
                    status="success",
                    data=raw,
                    message="Retrieved employee counts by department.",
                )
            elif entity == "department":
                if args.get("department_id") is not None or args.get("name"):
                    tool_name = "get_department"
                    raw = self.invoke_tool(
                        tool_name,
                        {
                            "department_id": args.get("department_id"),
                            "name": args.get("name") or args.get("department_name"),
                        },
                    )
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name=tool_name,
                        status="success",
                        data=raw,
                        message=f"Found department '{raw['department']['name']}'.",
                    )
                else:
                    tool_name = "list_departments"
                    raw = self.invoke_tool(tool_name, {})
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name=tool_name,
                        status="success",
                        data=raw,
                        message=f"Retrieved {raw['count']} department(s).",
                    )
            else:
                # Employee entity read/search
                single_lookup = bool(args.get("single_lookup"))
                if args.get("employee_id") is not None or (single_lookup and (args.get("name") or args.get("email"))):
                    tool_name = "get_employee"
                    raw = self.invoke_tool(
                        tool_name,
                        {
                            "employee_id": args.get("employee_id"),
                            "name": args.get("name"),
                            "email": args.get("email"),
                        },
                    )
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name=tool_name,
                        status="success",
                        data=raw,
                        message=f"Found employee {raw['employee']['name']} (ID {raw['employee']['id']}).",
                    )
                elif any(
                    args.get(k) is not None
                    for k in ("name", "email", "department_id", "department_name", "designation")
                ):
                    tool_name = "search_employees"
                    raw = self.invoke_tool(
                        tool_name,
                        {
                            "name": args.get("name"),
                            "email": args.get("email"),
                            "department_id": args.get("department_id"),
                            "department_name": args.get("department_name"),
                            "designation": args.get("designation"),
                        },
                    )
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name=tool_name,
                        status="success",
                        data=raw,
                        message=f"Found {raw['count']} matching employee(s).",
                    )
                else:
                    tool_name = "list_employees"
                    raw = self.invoke_tool(tool_name, {})
                    res = ToolExecutionResult(
                        agent_name=self.AGENT_NAME,
                        tool_name=tool_name,
                        status="success",
                        data=raw,
                        message=f"Retrieved {raw['count']} employee(s).",
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
                status="ambiguous",
                data={"candidates": exc.candidates},
                message=str(exc),
                error_category=exc.error_category,
            )
            log_event(
                logging.INFO,
                str(exc),
                conversation_id=conv_id,
                agent_name=self.AGENT_NAME,
                operation=tool_name,
                status="ambiguous",
                duration_ms=elapsed,
                error_category=exc.error_category,
            )
            return {"tool_result": model_to_dict(res), "status": "awaiting_clarification"}

        except (NotFoundError, ValidationErrorApp, PermissionDeniedError, DomainError) as exc:
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
