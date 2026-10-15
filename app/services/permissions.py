"""Permission enforcement, intent/entity allowlists, and prompt-injection/scope guardrails."""
from __future__ import annotations

import re
from typing import Dict, Optional, Set, Tuple

from app.services.validation import OutOfScopeError, PermissionDeniedError

ALLOWED_INTENTS: Set[str] = {
    "read",
    "create",
    "update",
    "delete",
    "confirm",
    "cancel",
    "clarify",
    "out_of_scope",
}

ALLOWED_ENTITIES: Set[str] = {
    "employee",
    "department",
    "organization",
    "none",
}

AGENT_NAMES: Set[str] = {
    "supervisor",
    "read_agent",
    "create_agent",
    "update_agent",
    "delete_agent",
}

AGENT_TOOL_PERMISSIONS: Dict[str, Set[str]] = {
    "supervisor": set(),  # Supervisor never invokes data tools directly
    "read_agent": {
        "list_employees",
        "get_employee",
        "search_employees",
        "list_departments",
        "get_department",
        "get_department_employee_counts",
    },
    "create_agent": {
        "create_employee",
        "create_department",
    },
    "update_agent": {
        "update_employee",
        "update_department",
    },
    "delete_agent": {
        "prepare_delete_employee",
        "execute_delete_employee",
        "prepare_delete_department",
        "execute_delete_department",
    },
}

ALL_APPROVED_TOOLS: Set[str] = set().union(*AGENT_TOOL_PERMISSIONS.values())

# Patterns for prompt injection, arbitrary SQL execution, and out-of-scope business/general domains
PROMPT_INJECTION_PATTERNS = [
    re.compile(r"(?i)\bignore\s+(all\s+)?(previous|prior|system|above)\s+(instructions|prompts|rules)\b"),
    re.compile(r"(?i)\b(system\s+override|developer\s+mode|jailbreak|bypass\s+guardrails|bypass\s+confirmation)\b"),
    re.compile(r"(?i)\byou\s+are\s+now\s+(a|an)\s+(?!employee|hr|department)"),
    re.compile(r"(?i)\bforget\s+(your|all)\s+(rules|instructions|constraints)\b"),
]

RAW_SQL_PATTERNS = [
    re.compile(r"(?i)\b(DROP\s+TABLE|TRUNCATE\s+TABLE|ALTER\s+TABLE|DELETE\s+FROM\s+\w+|INSERT\s+INTO\s+\w+|UPDATE\s+\w+\s+SET)\b"),
    re.compile(r"(?i);\s*(DROP|DELETE|UPDATE|INSERT|ALTER)\b"),
    re.compile(r"(?i)\bSELECT\s+\*\s+FROM\s+(sqlite_master|employees|departments)\b"),
    re.compile(r"(?i)\bEXEC(UTE)?\s+SQL\b"),
]

OUT_OF_SCOPE_HR_PATTERNS = [
    (
        re.compile(r"(?i)\b(payroll|salary|salaries|compensation|bonus|bonuses|paycheck|payslip|wage|wages|tax\s+withholding)\b"),
        "payroll and compensation management",
    ),
    (
        re.compile(r"(?i)\b(attendance|timesheet|time\s+off|pto|vacation\s+balance|sick\s+leave|leave\s+request|clock\s+in)\b"),
        "attendance and leave tracking",
    ),
    (
        re.compile(r"(?i)\b(performance\s+review|appraisal|okr|kpi\s+rating|pip|promotion\s+cycle)\b"),
        "performance reviews",
    ),
    (
        re.compile(r"(?i)\b(recruit|recruitment|job\s+posting|Job\s+opening|applicant|candidate\s+pipeline|interview\s+schedule|resume\s+screen)\b"),
        "recruitment and applicant tracking",
    ),
]

OUT_OF_SCOPE_GENERAL_PATTERNS = [
    re.compile(r"(?i)\b(write\s+(me\s+)?(a|an)\s+(poem|story|song|essay|joke|haiku|script|email|code))\b"),
    re.compile(r"(?i)\b(tell\s+(me\s+)?a\s+(joke|story|riddle))\b"),
    re.compile(r"(?i)\b(weather\s+in|forecast\s+for|stock\s+price|bitcoin|crypto|recipe\s+for|bake\s+a\s+cake)\b"),
    re.compile(r"(?i)\b(who\s+won\s+the\s+world\s+cup|capital\s+of\s+france|translate\s+to\s+spanish|solve\s+this\s+equation)\b"),
    re.compile(r"(?i)\b(fibonacci|quicksort|leetcode|binary\s+tree|python\s+script\s+to)\b"),
]


def enforce_intent_and_entity(intent: str, target_entity: str) -> None:
    """Validate that the routed intent and target entity belong to explicit allowlists."""
    norm_intent = (intent or "").strip().lower()
    norm_entity = (target_entity or "").strip().lower()
    if norm_intent not in ALLOWED_INTENTS:
        raise PermissionDeniedError(
            f"Unsupported operation intent '{intent}'. Allowed intents: {', '.join(sorted(ALLOWED_INTENTS))}."
        )
    if norm_entity not in ALLOWED_ENTITIES:
        raise OutOfScopeError(
            f"Unsupported target entity '{target_entity}'. Only Employee and Department records are in scope."
        )


def enforce_tool_permission(agent_name: str, tool_name: str) -> None:
    """Verify that `agent_name` is explicitly authorized to invoke `tool_name`."""
    norm_agent = (agent_name or "").strip().lower()
    norm_tool = (tool_name or "").strip()

    if norm_agent not in AGENT_NAMES:
        raise PermissionDeniedError(f"Unknown or unauthorized agent '{agent_name}'.")

    if norm_tool not in ALL_APPROVED_TOOLS:
        raise PermissionDeniedError(
            f"Tool '{tool_name}' is not an approved application tool. Arbitrary or unregistered tools are forbidden."
        )

    allowed_for_agent = AGENT_TOOL_PERMISSIONS.get(norm_agent, set())
    if norm_tool not in allowed_for_agent:
        raise PermissionDeniedError(
            f"Agent '{norm_agent}' is not permitted to invoke tool '{norm_tool}'. "
            f"Allowed tools for '{norm_agent}': {sorted(allowed_for_agent) or 'none'}."
        )


def inspect_message_guardrails(message: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """Inspect a user message for prompt injection, raw SQL attempts, or out-of-scope topics.

    Returns:
        (is_allowed, rejection_category, rejection_message)
    """
    text = (message or "").strip()
    if not text:
        return False, "empty_input", "Please provide a request related to employees or departments."

    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.search(text):
            return (
                False,
                "prompt_injection",
                "Request rejected: System instructions, scope restrictions, and safety confirmation rules cannot be overridden.",
            )

    for pattern in RAW_SQL_PATTERNS:
        if pattern.search(text):
            return (
                False,
                "arbitrary_sql",
                "Request rejected: Direct SQL execution is prohibited. Please describe the employee or department operation in natural language.",
            )

    for pattern, domain_label in OUT_OF_SCOPE_HR_PATTERNS:
        if pattern.search(text):
            return (
                False,
                "out_of_scope_hr",
                f"Request rejected: {domain_label.capitalize()} is outside the scope of this system. "
                "I can only manage Employee and Department directory records (read, create, update, and delete).",
            )

    for pattern in OUT_OF_SCOPE_GENERAL_PATTERNS:
        if pattern.search(text):
            return (
                False,
                "out_of_scope_general",
                "Request rejected: This assistant is restricted to Employee and Department information management "
                "and cannot answer general-purpose or creative requests.",
            )

    return True, None, None
