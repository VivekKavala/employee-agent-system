"""Supervisor Agent for intent classification, scope enforcement, pronoun/context resolution,
confirmation management, and grounded response synthesis.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import AppConfig
from app.logging.setup import log_event
from app.memory.conversation_memory import ConversationContextSnapshot, ConversationMemoryStore
from app.services.employee_service import EmployeeInformationService
from app.services.permissions import (
    enforce_intent_and_entity,
    enforce_tool_permission,
    inspect_message_guardrails,
)
from app.services.validation import PermissionDeniedError
from app.state import AgentState, SupervisorDecision, load_prompt

PRONOUN_PATTERN = re.compile(
    r"(?i)\b(he|him|his|she|her|hers|they|them|their|that\s+employee|this\s+employee)\b"
)

CONFIRM_ONLY_PATTERN = re.compile(
    r"(?i)^\s*(yes|y|confirm|confirmed|proceed|ok|okay|sure|go\s+ahead|do\s+it|yes[,\s]+please.*|yes[,\s]+confirm.*|confirm\s+delet(?:e|ion).*)\s*[.!]*\s*$"
)
CANCEL_ONLY_PATTERN = re.compile(
    r"(?i)^\s*(no|n|cancel|abort|stop|never\s*mind|no[,\s]+cancel.*|no[,\s]+abort.*|cancel\s+delet(?:e|ion).*|don'?t\s+delete.*|do\s+not\s+delete.*)\s*[.!]*\s*$"
)


def build_chat_model(config: AppConfig) -> Optional[ChatOpenAI]:
    """Construct a LangChain ChatOpenAI instance with robust base_url and env synchronization."""
    import os
    provider = (config.llm_provider or "").strip().lower()
    if provider == "deterministic":
        return None
    config.validate_for_live_llm()

    base_url = config.llm_base_url
    if not base_url:
        if provider == "groq" or "llama" in (config.llm_model or "").lower():
            base_url = "https://api.groq.com/openai/v1"
        elif provider == "gemini_openai":
            base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
        elif provider == "ollama":
            base_url = "http://localhost:11434/v1"

    # Synchronize environment variables so the underlying client never defaults to api.openai.com
    if base_url:
        os.environ["OPENAI_BASE_URL"] = base_url
        os.environ["OPENAI_API_BASE"] = base_url
    if config.llm_api_key:
        os.environ["OPENAI_API_KEY"] = config.llm_api_key

    return ChatOpenAI(
        model=config.llm_model,
        model_name=config.llm_model,
        api_key=config.llm_api_key or "ollama",
        openai_api_key=config.llm_api_key or "ollama",
        base_url=base_url,
        openai_api_base=base_url,
        temperature=config.llm_temperature,
    )


class SupervisorAgent:
    """Supervisor orchestrating intent routing, guardrails, confirmations, and final responses."""

    AGENT_NAME = "supervisor"

    def __init__(
        self,
        config: AppConfig,
        service: EmployeeInformationService,
        memory: ConversationMemoryStore,
        llm_client: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.service = service
        self.memory = memory
        self.prompt = load_prompt(self.AGENT_NAME)
        self.llm_client = llm_client

    def _get_known_department_names(self) -> List[str]:
        depts = self.service.list_departments(include_counts=False)
        return [str(d["name"]) for d in depts]

    # -------------------------------------------------------------------------
    # Node 1: Route & Validate Incoming Request
    # -------------------------------------------------------------------------

    def route(self, state: AgentState) -> Dict[str, Any]:
        start_ts = time.perf_counter()
        conv_id = state.get("conversation_id", "default")
        message = (state.get("current_message") or "").strip()

        # Persist incoming user turn
        self.memory.append_turn(conv_id, "user", message)

        # 1. Deterministic guardrail check (prompt injection, raw SQL, out-of-scope HR/general)
        allowed, reject_cat, reject_msg = inspect_message_guardrails(message)
        if not allowed:
            elapsed = (time.perf_counter() - start_ts) * 1000
            log_event(
                logging.WARNING,
                reject_msg or "Rejected by guardrails",
                conversation_id=conv_id,
                agent_name=self.AGENT_NAME,
                operation="guardrail_check",
                status="rejected",
                duration_ms=elapsed,
                error_category=reject_cat,
            )
            return {
                "operation_intent": "out_of_scope",
                "target_entity": "none",
                "routed_agent": None,
                "structured_arguments": {},
                "tool_result": {
                    "agent_name": self.AGENT_NAME,
                    "tool_name": "none",
                    "status": "rejected",
                    "data": {},
                    "message": reject_msg,
                    "error_category": reject_cat,
                },
                "status": "rejected",
            }

        # 2. Load conversation context and any active pending confirmation
        context = self.memory.get_context(conv_id)
        pending_conf = self.memory.get_pending_confirmation(conv_id)

        # 3. Determine SupervisorDecision (via injected mock/live LLM or deterministic analyzer)
        decision = self._decide(message, context, pending_conf)

        # 4. Enforce intent and entity allowlists in Python
        try:
            enforce_intent_and_entity(decision.operation_intent, decision.target_entity)
            if decision.structured_arguments.get("requested_tool"):
                target_agent = decision.routed_agent or "supervisor"
                enforce_tool_permission(target_agent, str(decision.structured_arguments["requested_tool"]))
        except (PermissionDeniedError, Exception) as exc:
            elapsed = (time.perf_counter() - start_ts) * 1000
            err_cat = getattr(exc, "error_category", "permission_denied")
            log_event(
                logging.WARNING,
                str(exc),
                conversation_id=conv_id,
                agent_name=self.AGENT_NAME,
                operation="permission_check",
                status="rejected",
                duration_ms=elapsed,
                error_category=err_cat,
            )
            return {
                "operation_intent": decision.operation_intent,
                "target_entity": decision.target_entity,
                "routed_agent": None,
                "structured_arguments": decision.structured_arguments,
                "tool_result": {
                    "agent_name": self.AGENT_NAME,
                    "tool_name": "none",
                    "status": "rejected",
                    "data": {},
                    "message": str(exc),
                    "error_category": err_cat,
                },
                "status": "rejected",
            }

        # 5. Handle out_of_scope, clarify, or cancel directly at supervisor level
        if decision.operation_intent == "out_of_scope":
            reason = (
                decision.rejection_reason
                or "Request rejected: This assistant is restricted to Employee and Department management."
            )
            return {
                "operation_intent": "out_of_scope",
                "target_entity": "none",
                "routed_agent": None,
                "structured_arguments": {},
                "tool_result": {
                    "agent_name": self.AGENT_NAME,
                    "tool_name": "none",
                    "status": "rejected",
                    "data": {},
                    "message": reason,
                    "error_category": "out_of_scope",
                },
                "status": "rejected",
            }

        if decision.operation_intent == "cancel":
            cancelled = self.memory.cancel_pending_confirmation(conv_id)
            if cancelled:
                msg = (
                    f"Cancelled pending deletion of {cancelled.target_summary} "
                    f"[Confirmation ID: {cancelled.confirmation_id}]. No changes were made to the database."
                )
            else:
                msg = "There is no active pending operation to cancel."
            return {
                "operation_intent": "cancel",
                "target_entity": decision.target_entity,
                "routed_agent": None,
                "structured_arguments": {},
                "pending_confirmation": None,
                "tool_result": {
                    "agent_name": self.AGENT_NAME,
                    "tool_name": "none",
                    "status": "cancelled",
                    "data": {},
                    "message": msg,
                },
                "status": "cancelled",
            }

        if decision.operation_intent == "clarify":
            question = (
                decision.clarification_question
                or "Could you please clarify the specific employee or department and the details for your request?"
            )
            pending_clarif = decision.structured_arguments.get("pending_clarification")
            if pending_clarif is not None:
                self.memory.update_context(conv_id, pending_clarification=pending_clarif)
            return {
                "operation_intent": "clarify",
                "target_entity": decision.target_entity,
                "routed_agent": None,
                "structured_arguments": decision.structured_arguments,
                "tool_result": {
                    "agent_name": self.AGENT_NAME,
                    "tool_name": "none",
                    "status": "clarify",
                    "data": decision.structured_arguments,
                    "message": question,
                    "error_category": "clarification_needed",
                },
                "status": "awaiting_clarification",
            }

        elapsed = (time.perf_counter() - start_ts) * 1000
        log_event(
            logging.INFO,
            f"Routed intent='{decision.operation_intent}' entity='{decision.target_entity}' -> {decision.routed_agent}",
            conversation_id=conv_id,
            agent_name=self.AGENT_NAME,
            operation="route",
            status="routed",
            duration_ms=elapsed,
        )
        return {
            "operation_intent": decision.operation_intent,
            "target_entity": decision.target_entity,
            "routed_agent": decision.routed_agent,
            "structured_arguments": decision.structured_arguments,
            "pending_confirmation": pending_conf.to_dict() if pending_conf else None,
            "status": "routed",
        }

    # -------------------------------------------------------------------------
    # Node 3: Validate Tool Result & Produce Grounded Final Response
    # -------------------------------------------------------------------------

    def finalize(self, state: AgentState) -> Dict[str, Any]:
        conv_id = state.get("conversation_id", "default")
        tool_result = state.get("tool_result") or {}
        status = state.get("status", "completed")
        intent = state.get("operation_intent", "read")
        entity = state.get("target_entity", "employee")
        data = tool_result.get("data") or {}

        # Update conversation entity context based on actual tool results
        if status == "completed":
            self.memory.update_context(conv_id, pending_clarification=None)

            if "employee" in data and isinstance(data["employee"], dict):
                emp = data["employee"]
                self.memory.update_context(
                    conv_id,
                    last_employee_id=int(emp["id"]),
                    last_employee_name=str(emp["name"]),
                    last_department_id=int(emp["department_id"]),
                    last_department_name=str(emp.get("department_name") or ""),
                )
            elif "employees" in data and isinstance(data["employees"], list):
                emp_list = data["employees"]
                if len(emp_list) == 1:
                    emp = emp_list[0]
                    self.memory.update_context(
                        conv_id,
                        last_employee_id=int(emp["id"]),
                        last_employee_name=str(emp["name"]),
                        last_department_id=int(emp["department_id"]),
                        last_department_name=str(emp.get("department_name") or ""),
                    )
                else:
                    # Multiple employees returned -> clear single employee pronoun target so follow-ups must clarify
                    self.memory.update_context(
                        conv_id,
                        last_employee_id=None,
                        last_employee_name=None,
                    )
            elif "department" in data and isinstance(data["department"], dict):
                dept = data["department"]
                self.memory.update_context(
                    conv_id,
                    last_department_id=int(dept["id"]),
                    last_department_name=str(dept["name"]),
                )
        elif status == "awaiting_clarification":
            pending_clarif = data.get("pending_clarification")
            if pending_clarif is not None:
                self.memory.update_context(conv_id, pending_clarification=pending_clarif)

        current_msg = state.get("current_message", "")
        final_text = self._format_grounded_response(current_msg, intent, entity, status, tool_result)
        self.memory.append_turn(
            conv_id,
            "assistant",
            final_text,
            metadata={
                "intent": intent,
                "target_entity": entity,
                "routed_agent": state.get("routed_agent"),
                "status": status,
            },
        )
        return {"final_response": final_text}

    def _format_grounded_response(
        self,
        message: str,
        intent: str,
        entity: str,
        status: str,
        tool_result: Dict[str, Any],
    ) -> str:
        msg = tool_result.get("message") or ""
        data = tool_result.get("data") or {}
        tool_name = tool_result.get("tool_name") or ""

        if status in ("rejected", "awaiting_clarification", "awaiting_confirmation", "cancelled", "error"):
            return msg

        if tool_name == "get_department_employee_counts":
            depts = data.get("departments") or []
            total = data.get("total_employees", 0)
            if not depts:
                return "No departments found in the database."
            lines = [f"Employee headcount across {len(depts)} department(s) (Total: {total} employee(s)):"]
            for d in depts:
                lines.append(f"- {d['name']} (ID {d['id']}): {d['employee_count']} employee(s)")
            return "\n".join(lines)

        if tool_name == "list_departments":
            depts = data.get("departments") or []
            if not depts:
                return "No departments found."
            lines = [f"Found {len(depts)} department(s):"]
            for d in depts:
                desc = f" — {d['description']}" if d.get("description") else ""
                lines.append(
                    f"- ID {d['id']}: {d['name']} ({d.get('employee_count', 0)} employee(s)){desc}"
                )
            return "\n".join(lines)

        if tool_name == "get_department":
            d = data.get("department") or {}
            desc = d.get("description") or "No description"
            return (
                f"Department '{d['name']}' (ID {d['id']}): {d.get('employee_count', 0)} employee(s), "
                f"Description: {desc}."
            )

        if tool_name in ("list_employees", "search_employees"):
            emps = data.get("employees") or []
            if not emps:
                return "No matching employees found."
            lines = [f"Found {len(emps)} employee(s):"]
            for e in emps:
                lines.append(
                    f"- ID {e['id']}: {e['name']} | {e['designation']} | "
                    f"Department: {e['department_name']} | Email: {e['email']}"
                )
            return "\n".join(lines)

        if tool_name == "get_employee":
            e = data.get("employee") or {}
            msg_lower = (message or "").lower()

            if re.search(r"\b(email|email\s*address|mail)\b", msg_lower):
                return f"{e['name']}'s email is {e['email']}."
            if re.search(r"\b(department|dept|team)\b", msg_lower):
                return f"{e['name']} is in the {e['department_name']} department (ID {e['department_id']})."
            if re.search(r"\b(designation|role|title|position|job)\b", msg_lower):
                return f"{e['name']}'s designation is {e['designation']}."

            return (
                f"Employee ID {e['id']}: {e['name']} | Designation: {e['designation']} | "
                f"Department: {e['department_name']} (ID {e['department_id']}) | Email: {e['email']}."
            )

        return msg

    # -------------------------------------------------------------------------
    # Intent & Argument Extraction (Supports Injected LLM / Live LLM / Deterministic)
    # -------------------------------------------------------------------------

    def _decide(
        self,
        message: str,
        context: ConversationContextSnapshot,
        pending_conf: Optional[Any],
    ) -> SupervisorDecision:
        if self.llm_client is not None:
            if callable(self.llm_client):
                res = self.llm_client(message, context, pending_conf)
                if isinstance(res, SupervisorDecision):
                    return res
                if isinstance(res, dict):
                    return SupervisorDecision(**res)
            elif hasattr(self.llm_client, "with_structured_output"):
                structured_llm = self.llm_client.with_structured_output(SupervisorDecision)
                sys_prompt = (
                    f"{self.prompt}\n\n"
                    f"Current Context: last_employee_id={context.last_employee_id}, "
                    f"last_employee_name={context.last_employee_name}, "
                    f"last_department_name={context.last_department_name}, "
                    f"pending_confirmation={pending_conf.to_dict() if pending_conf else None}"
                )
                return structured_llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=message)])

        if (self.config.llm_provider or "").strip().lower() != "deterministic":
            chat_model = build_chat_model(self.config)
            if chat_model is not None:
                structured_llm = chat_model.with_structured_output(SupervisorDecision)
                sys_prompt = (
                    f"{self.prompt}\n\n"
                    f"Current Context: last_employee_id={context.last_employee_id}, "
                    f"last_employee_name={context.last_employee_name}, "
                    f"last_department_name={context.last_department_name}, "
                    f"pending_confirmation={pending_conf.to_dict() if pending_conf else None}"
                )
                return structured_llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=message)])

        return self._deterministic_parse(message, context, pending_conf)

    def _deterministic_parse(
        self,
        message: str,
        context: ConversationContextSnapshot,
        pending_conf: Optional[Any],
    ) -> SupervisorDecision:
        text = message.strip()
        lower = text.lower()

        # 1. Check cancellation intent
        if CANCEL_ONLY_PATTERN.match(text):
            return SupervisorDecision(
                operation_intent="cancel",
                target_entity=pending_conf.target_entity if pending_conf else "none",
                routed_agent=None,
            )

        # 2. Check confirmation intent
        if CONFIRM_ONLY_PATTERN.match(text):
            if not pending_conf:
                return SupervisorDecision(
                    operation_intent="confirm",
                    target_entity="employee",
                    routed_agent="delete_agent",
                    structured_arguments={
                        "action": "execute",
                        "confirmation_id": "",
                        "employee_id": -1,
                    },
                )
            explicit_id_match = re.search(r"\b(?:id|employee|department)\s*#?\s*(\d+)\b", lower)
            target_id = int(explicit_id_match.group(1)) if explicit_id_match else pending_conf.target_id

            conf_token_match = re.search(r"\b(conf-[a-z]{3}-\d+-[a-z0-9]+)\b", lower)
            conf_token = conf_token_match.group(1) if conf_token_match else pending_conf.confirmation_id

            args: Dict[str, Any] = {
                "action": "execute",
                "confirmation_id": conf_token,
                "target_id": target_id,
            }
            if pending_conf.target_entity == "department":
                args["department_id"] = target_id
            else:
                args["employee_id"] = target_id

            return SupervisorDecision(
                operation_intent="confirm",
                target_entity=pending_conf.target_entity,
                routed_agent="delete_agent",
                structured_arguments=args,
            )

        # 3. Check if replying to a pending clarification (only if not starting a fresh full command)
        is_fresh_command = bool(
            re.match(
                r"(?i)^\s*(create|add|hire|register|insert|update|change|modify|set|edit|rename|move|transfer|delete|remove|terminate|show|list|find|get|search|lookup|how\s+many)\b",
                text,
            )
        )
        if context.pending_clarification and not is_fresh_command:
            clarif_decision = self._try_resolve_pending_clarification(text, context.pending_clarification)
            if clarif_decision is not None:
                return clarif_decision

        # 4. Headcount / organization-wide count questions
        if re.search(
            r"(?i)\b(how\s+many\s+employees.*each\s+department|employees\s+per\s+department|"
            r"headcount\s+by\s+department|count\s+employees\s+in\s+each\s+department|"
            r"number\s+of\s+employees\s+in\s+each\s+department|department\s+headcount)\b",
            text,
        ):
            return SupervisorDecision(
                operation_intent="read",
                target_entity="organization",
                routed_agent="read_agent",
                structured_arguments={"query_type": "department_counts"},
            )

        # 5. Delete operations
        if re.search(r"(?i)^\s*(delete|remove|terminate|drop)\b", text) or re.search(
            r"(?i)\b(delete|remove)\s+(a\s+)?(the\s+)?(employee|department|emp)\b", text
        ):
            return self._parse_delete(text, context)

        # 6. Create operations
        if re.search(r"(?i)^\s*(create|add|hire|register|insert|new)\b", text) or re.search(
            r"(?i)\b(create|add|hire|register)\s+(a\s+)?(new\s+)?(employee|department)\b", text
        ):
            return self._parse_create(text)

        # 7. Update operations
        if re.search(
            r"(?i)^\s*(update|change|modify|set|edit|rename|move|transfer|promote|reassign)\b",
            text,
        ):
            return self._parse_update(text, context)

        # 8. If a pending clarification is active and the user sent a follow-up phrase that didn't match above
        if context.pending_clarification:
            clarif_decision = self._try_resolve_pending_clarification(text, context.pending_clarification)
            if clarif_decision is not None:
                return clarif_decision

        # 9. Read / Search operations
        if re.search(
            r"(?i)\b(show|list|find|get|search|lookup|who\s+is|who\s+works|what\s+is|tell\s+me\s+about|"
            r"display|view|fetch|employees|departments|employee|department)\b",
            text,
        ):
            return self._parse_read(text, context)

        # 10. Anything else is out of scope
        return SupervisorDecision(
            operation_intent="out_of_scope",
            target_entity="none",
            routed_agent=None,
            rejection_reason=(
                "Request rejected: This assistant only manages Employee and Department records "
                "(read, create, update, and delete)."
            ),
        )

    @staticmethod
    def _extract_labeled_employee_fields(text: str) -> Dict[str, Any]:
        """Extract key-value or keyword-labeled employee attributes from flexible natural language.

        Supports phrasings such as:
        - 'create a new employee with name Vivek, designation AI Engineer and department Engineering'
        - 'add employee name: Vivek, role: AI Engineer, dept: Engineering, email: vivek@example.com'
        - 'create employee named Vivek with designation AI Engineer in Engineering'
        """
        extracted: Dict[str, Any] = {}

        # Stop-lookahead boundary: next field label, comma, or end of sentence
        stop_boundary = (
            r"(?=\s*(?:,|\band\s+(?:name|named|designation|role|title|position|department|dept|email)\b|"
            r"\bwith\s+(?:name|designation|role|title|position|department|dept|email)\b|"
            r"\b(?:name|designation|role|title|position|department|dept|email)\s*(?:is|=|:)\b|$))"
        )

        # 1. Email extraction
        email_match = re.search(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})", text)
        if email_match:
            extracted["email"] = email_match.group(1).lower()
        else:
            bad_email_match = re.search(r"(?i)\bemail\s*(?:is|=|:)?\s*['\"]?([^\s,'\"]+)['\"]?", text)
            if bad_email_match:
                extracted["email"] = bad_email_match.group(1).strip(" .!")

        # 2. Labeled name: "with name X", "named X", "called X", "name: X", "name is X"
        name_labeled = re.search(
            rf"(?i)\b(?:with\s+name|employee\s+named|employee\s+called|named|called|name\s*(?:is|=|:)?)\s+['\"]?([A-Za-z][A-Za-z\s.'\-]+?)['\"]?{stop_boundary}",
            text,
        )
        if name_labeled:
            candidate_name = name_labeled.group(1).strip(" ,.!")
            # Trim trailing 'as <designation>' or 'in <department>' if present inside candidate_name
            candidate_name = re.split(
                r"(?i)\s+(?:as\s+|in\s+|with\s+|designation\b|role\b|title\b|department\b|dept\b)",
                candidate_name,
            )[0].strip(" ,.!")
            if candidate_name and candidate_name.lower() not in (
                "employee",
                "new employee",
                "a new employee",
                "an employee",
            ):
                extracted["name"] = candidate_name

        # 3. Labeled designation: "designation X", "role X", "title X", "position X", "as a/an X"
        desig_labeled = re.search(
            rf"(?i)\b(?:with\s+)?(?:designation|role|job\s+title|title|position)\s*(?:is|=|:|as|to)?\s+['\"]?([A-Za-z0-9][A-Za-z0-9\s/&\-]+?)['\"]?{stop_boundary}",
            text,
        )
        if desig_labeled:
            cand_desig = desig_labeled.group(1).strip(" ,.!")
            cand_desig = re.split(
                r"(?i)\s+(?:and\s+department\b|in\s+department\b|in\s+the\s+|department\b|dept\b|with\s+email\b|email\b)",
                cand_desig,
            )[0].strip(" ,.!")
            if cand_desig:
                extracted["designation"] = cand_desig

        # 4. Labeled department: "department X", "dept X", "in <dept> department"
        dept_id_match = re.search(r"(?i)\b(?:department_id|department\s+id|dept\s+id)\s*(?:is|=|:)?\s*#?(\d+)\b", text)
        if dept_id_match:
            extracted["department_id"] = int(dept_id_match.group(1))

        dept_labeled = re.search(
            rf"(?i)\b(?:(?:and|with|in)\s+)?(?:department|dept)\s*(?:is|=|:|named|called)?\s+['\"]?([A-Za-z0-9][A-Za-z0-9\s&\-]+?)['\"]?{stop_boundary}",
            text,
        )
        if dept_labeled:
            cand_dept = dept_labeled.group(1).strip(" ,.!")
            cand_dept = re.sub(r"(?i)\s+department$", "", cand_dept).strip(" ,.!")
            cand_dept = re.split(r"(?i)\s+(?:with\s+email\b|and\s+email\b|email\b|as\s+)", cand_dept)[0].strip(" ,.!")
            if cand_dept and not cand_dept.isdigit():
                extracted["department_name"] = cand_dept

        return extracted

    def _try_resolve_pending_clarification(
        self,
        text: str,
        pending: Dict[str, Any],
    ) -> Optional[SupervisorDecision]:
        intent = pending.get("operation_intent")
        entity = pending.get("target_entity", "employee")
        partial = dict(pending.get("partial_arguments") or {})
        missing = list(pending.get("missing_fields") or [])

        # Case A: Disambiguating employee_id (e.g. user replies "106", "ID 106", "employee 106")
        id_only_match = re.match(r"(?i)^\s*(?:employee\s+|id\s+|#)?(\d+)\s*[.!]*\s*$", text)
        if id_only_match and "employee_id" in missing:
            emp_id = int(id_only_match.group(1))
            partial["employee_id"] = emp_id
            partial.pop("target_name", None)
            partial.pop("name", None)
            agent_map = {
                "read": "read_agent",
                "update": "update_agent",
                "delete": "delete_agent",
            }
            if intent == "delete":
                partial["action"] = "prepare"
            return SupervisorDecision(
                operation_intent=intent,
                target_entity=entity,
                routed_agent=agent_map.get(intent, "read_agent"),
                structured_arguments=partial,
            )

        # Case B: Supplying missing fields for create_employee
        if intent == "create" and entity == "employee":
            labeled = self._extract_labeled_employee_fields(text)
            if labeled:
                for k, v in labeled.items():
                    if v is not None:
                        partial[k] = v
                return SupervisorDecision(
                    operation_intent="create",
                    target_entity="employee",
                    routed_agent="create_agent",
                    structured_arguments=partial,
                )

            desig_dept = re.search(
                r"(?i)(?:as\s+(?:a|an)?\s*)?([A-Za-z0-9\s/&\-]+?)\s+in\s+(?:the\s+)?([A-Za-z0-9\s&\-]+?)(?:\s+department)?\s*[.!]*$",
                text,
            )
            if desig_dept:
                if not partial.get("designation"):
                    partial["designation"] = desig_dept.group(1).strip()
                if not partial.get("department_name") and not partial.get("department_id"):
                    partial["department_name"] = desig_dept.group(2).strip()
                return SupervisorDecision(
                    operation_intent="create",
                    target_entity="employee",
                    routed_agent="create_agent",
                    structured_arguments=partial,
                )
            if missing == ["department"] and len(text.split()) <= 5:
                cleaned_dept = re.sub(r"(?i)^(?:in|department|dept|is|:)\s+", "", text.strip(" .!")).strip()
                cleaned_dept = re.sub(r"(?i)\s+department$", "", cleaned_dept).strip()
                if cleaned_dept:
                    partial["department_name"] = cleaned_dept
                    return SupervisorDecision(
                        operation_intent="create",
                        target_entity="employee",
                        routed_agent="create_agent",
                        structured_arguments=partial,
                    )

        return None

    def _parse_delete(self, text: str, context: ConversationContextSnapshot) -> SupervisorDecision:
        lower = text.lower()
        if "department" in lower:
            id_match = re.search(r"(?i)\bdepartment\s+(?:with\s+)?(?:id\s*)?#?(\d+)\b", text)
            if id_match:
                return SupervisorDecision(
                    operation_intent="delete",
                    target_entity="department",
                    routed_agent="delete_agent",
                    structured_arguments={"action": "prepare", "department_id": int(id_match.group(1))},
                )
            name_match = re.search(
                r"(?i)(?:delete|remove)\s+(?:the\s+)?(?:department\s+(?:with\s+name|called|named)?\s*)?['\"]?([A-Za-z0-9\s&\-]+?)['\"]?(?:\s+department)?\s*(?:immediately|without\s+confirmation|now)?\s*[.!]*$",
                text,
            )
            dept_name = None
            if name_match:
                candidate = name_match.group(1).strip()
                candidate = re.sub(r"(?i)^(?:department|with\s+name|named|called)\s+", "", candidate).strip()
                if candidate and candidate.lower() not in ("department", "a department", "the department"):
                    dept_name = candidate
            return SupervisorDecision(
                operation_intent="delete",
                target_entity="department",
                routed_agent="delete_agent",
                structured_arguments={"action": "prepare", "name": dept_name},
            )

        id_match = re.search(r"(?i)\b(?:employee|emp|id)\s+(?:with\s+id\s+)?#?(\d+)\b", text)
        if not id_match:
            id_match = re.search(r"(?i)\b(?:employee|emp|id)\s*#?(\d+)\b", text)
        if id_match:
            return SupervisorDecision(
                operation_intent="delete",
                target_entity="employee",
                routed_agent="delete_agent",
                structured_arguments={"action": "prepare", "employee_id": int(id_match.group(1))},
            )

        if PRONOUN_PATTERN.search(text):
            if context.last_employee_id is not None:
                return SupervisorDecision(
                    operation_intent="delete",
                    target_entity="employee",
                    routed_agent="delete_agent",
                    structured_arguments={"action": "prepare", "employee_id": context.last_employee_id},
                )
            return SupervisorDecision(
                operation_intent="clarify",
                target_entity="employee",
                routed_agent=None,
                clarification_question=(
                    "Which employee would you like to delete? No single employee is currently in focus—"
                    "please provide their employee ID or full name."
                ),
            )

        name_match = re.search(
            r"(?i)(?:delete|remove|terminate)\s+(?:an?\s+|the\s+)?(?:employee\s+(?:with\s+name|named|called)\s+|employee\s+)?([A-Za-z][A-Za-z\s.\-']+?)(?:\s+immediately|\s+without.*|\s+now)?\s*[.!]*$",
            text,
        )
        emp_name = None
        if name_match:
            candidate = name_match.group(1).strip()
            if candidate.lower() not in ("employee", "an employee", "the employee"):
                emp_name = candidate

        return SupervisorDecision(
            operation_intent="delete",
            target_entity="employee",
            routed_agent="delete_agent",
            structured_arguments={"action": "prepare", "name": emp_name},
        )

    def _parse_create(self, text: str) -> SupervisorDecision:
        # Check if creating a Department (only when 'employee' is NOT the target noun after create/add)
        is_dept_create = bool(
            re.search(r"(?i)\b(create|add|new)\s+(a\s+)?(new\s+)?department\b", text)
            and not re.search(r"(?i)\b(create|add|hire|register|new)\s+(a\s+|an\s+)?(new\s+)?employee\b", text)
        )
        if is_dept_create:
            desc_match = re.search(
                r"(?i)\b(?:with\s+)?description\s*(?:is|=|:)?\s*['\"]?(.+?)['\"]?\s*[.!]*$",
                text,
            )
            description = desc_match.group(1).strip() if desc_match else None
            base_text = text[: desc_match.start()].strip(" ,") if desc_match else text
            base_text = re.sub(r"(?i)\s+and\s*$", "", base_text).strip()

            name_match = re.search(
                r"(?i)\bdepartment\s+(?:with\s+name|called|named|name\s*(?:is|=|:)?)\s+['\"]?([A-Za-z0-9\s&\-]+?)['\"]?\s*[.!]*$",
                base_text,
            )
            if not name_match:
                name_match = re.search(
                    r"(?i)(?:create|add)\s+(?:a\s+)?(?:new\s+)?department\s+['\"]?([A-Za-z0-9\s&\-]+?)['\"]?\s*[.!]*$",
                    base_text,
                )
            dept_name = None
            if name_match:
                candidate = name_match.group(1).strip(" ,.!")
                if candidate.lower() not in ("department", "a department", "new department", "called", "named"):
                    dept_name = candidate
            return SupervisorDecision(
                operation_intent="create",
                target_entity="department",
                routed_agent="create_agent",
                structured_arguments={"name": dept_name, "description": description},
            )

        # Employee creation:
        # Step 1: Extract any labeled fields ("with name Vivek, designation AI Engineer and department Engineering")
        labeled_args = self._extract_labeled_employee_fields(text)

        email = labeled_args.get("email")
        clean_text = re.sub(r"\(\s*[^)]*@[^)]*\)", "", text)
        clean_text = re.sub(r"(?i)\s+(?:,\s*)?(?:and\s+)?(?:with\s+)?email\s*(?:is|=|:)?\s*\S+", "", clean_text).strip()

        emp_prefix = (
            r"(?:add|create|hire|register)\s+"
            r"(?:(?:a|an)\s+)?"
            r"(?:new\s+)?"
            r"(?:employee\s+)?"
            r"(?:(?:with\s+name|named|called)\s+)?"
        )

        # Pattern 1: "<prefix> <Name> as (a/an) <Designation> in <Department>"
        m1 = re.search(
            rf"(?i){emp_prefix}([A-Za-z][A-Za-z\s.'\-]+?)\s+as\s+(?:a\s+|an\s+)?([A-Za-z0-9\s/&\-]+?)\s+in\s+(?:the\s+)?(?:department\s+)?([A-Za-z0-9\s&\-]+?)(?:\s+department)?\s*[.!]*$",
            clean_text,
        )
        if m1 and not labeled_args.get("designation"):
            return SupervisorDecision(
                operation_intent="create",
                target_entity="employee",
                routed_agent="create_agent",
                structured_arguments={
                    "name": m1.group(1).strip(),
                    "designation": m1.group(2).strip(),
                    "department_name": m1.group(3).strip(),
                    "email": email,
                },
            )

        # Pattern 2: "<prefix> <Name> in <Department> as (a/an) <Designation>"
        m2 = re.search(
            rf"(?i){emp_prefix}([A-Za-z][A-Za-z\s.'\-]+?)\s+in\s+(?:the\s+)?(?:department\s+)?([A-Za-z0-9\s&\-]+?)(?:\s+department)?\s+as\s+(?:a\s+|an\s+)?([A-Za-z0-9\s/&\-]+?)\s*[.!]*$",
            clean_text,
        )
        if m2 and not labeled_args.get("designation"):
            return SupervisorDecision(
                operation_intent="create",
                target_entity="employee",
                routed_agent="create_agent",
                structured_arguments={
                    "name": m2.group(1).strip(),
                    "department_name": m2.group(2).strip(),
                    "designation": m2.group(3).strip(),
                    "email": email,
                },
            )

        # If labeled fields matched (e.g., "with name Vivek, designation AI Engineer [and department Engineering]")
        if labeled_args.get("name") or labeled_args.get("designation") or labeled_args.get("department_name"):
            # If name wasn't explicitly labeled ("create employee Vivek with designation AI Engineer"), extract name before 'with'/'designation'
            if not labeled_args.get("name"):
                lead_name = re.search(
                    rf"(?i){emp_prefix}([A-Za-z][A-Za-z\s.'\-]+?)\s*(?:,|\s+with\b|\s+designation\b|\s+role\b|\s+title\b|\s+in\b)",
                    clean_text,
                )
                if lead_name:
                    cand = lead_name.group(1).strip(" ,.!")
                    if cand.lower() not in ("employee", "new employee", "a new employee", "an employee"):
                        labeled_args["name"] = cand

            # Also check if department was given via "in <Department>" at the end
            if not labeled_args.get("department_name") and not labeled_args.get("department_id"):
                in_dept = re.search(
                    r"(?i)\bin\s+(?:the\s+)?([A-Za-z0-9\s&\-]+?)(?:\s+department)?\s*[.!]*$",
                    clean_text,
                )
                if in_dept:
                    cand_dept = in_dept.group(1).strip(" ,.!")
                    # Strip any trailing designation clause if present
                    cand_dept = re.split(r"(?i)\s+(?:with\s+|and\s+)", cand_dept)[0].strip(" ,.!")
                    if cand_dept:
                        labeled_args["department_name"] = cand_dept

            return SupervisorDecision(
                operation_intent="create",
                target_entity="employee",
                routed_agent="create_agent",
                structured_arguments={
                    "name": labeled_args.get("name"),
                    "designation": labeled_args.get("designation"),
                    "department_id": labeled_args.get("department_id"),
                    "department_name": labeled_args.get("department_name"),
                    "email": email,
                },
            )

        # Pattern 3: Missing department ("<prefix> <Name> as <Designation>")
        m3 = re.search(
            rf"(?i){emp_prefix}([A-Za-z][A-Za-z\s.'\-]+?)\s+as\s+(?:a\s+|an\s+)?([A-Za-z0-9\s/&\-]+?)\s*[.!]*$",
            clean_text,
        )
        if m3:
            return SupervisorDecision(
                operation_intent="create",
                target_entity="employee",
                routed_agent="create_agent",
                structured_arguments={
                    "name": m3.group(1).strip(),
                    "designation": m3.group(2).strip(),
                    "email": email,
                },
            )

        # Pattern 4: Only name provided ("<prefix> <Name>")
        m4 = re.search(
            rf"(?i){emp_prefix}([A-Za-z][A-Za-z\s.'\-]+?)\s*[.!]*$",
            clean_text,
        )
        name_only = m4.group(1).strip() if m4 else None
        if name_only and name_only.lower() in ("employee", "new employee", "a new employee", "an employee"):
            name_only = None

        return SupervisorDecision(
            operation_intent="create",
            target_entity="employee",
            routed_agent="create_agent",
            structured_arguments={
                "name": name_only,
                "email": email,
            },
        )

    def _parse_update(self, text: str, context: ConversationContextSnapshot) -> SupervisorDecision:
        if re.search(r"(?i)\b(?:rename\s+department|update\s+(?:the\s+)?[A-Za-z0-9\s&\-]*department)\b", text):
            id_match = re.search(r"(?i)\bdepartment\s+(?:id\s*)?#?(\d+)\b", text)
            dept_id = int(id_match.group(1)) if id_match else None
            current_name = None
            if not dept_id:
                nm = re.search(
                    r"(?i)(?:update|rename)\s+(?:the\s+)?(?:department\s+)?([A-Za-z0-9\s&\-]+?)(?:\s+department)?\s+(?:description\s+to|name\s+to|to)\b",
                    text,
                )
                if nm:
                    current_name = nm.group(1).strip()

            desc_match = re.search(r"(?i)\bdescription\s+(?:to|is|=|:)\s+['\"]?(.+?)['\"]?\s*[.!]*$", text)
            new_name_match = re.search(r"(?i)\b(?:rename.*?to|name\s+to)\s+['\"]?([A-Za-z0-9\s&\-]+?)['\"]?\s*[.!]*$", text)
            return SupervisorDecision(
                operation_intent="update",
                target_entity="department",
                routed_agent="update_agent",
                structured_arguments={
                    "department_id": dept_id,
                    "current_name": current_name,
                    "new_name": new_name_match.group(1).strip() if new_name_match else None,
                    "description": desc_match.group(1).strip() if desc_match else None,
                },
            )

        args: Dict[str, Any] = {}

        id_match = re.search(r"(?i)\b(?:employee|emp|id)\s*(?:with\s+id\s+)?#?(\d+)(?:'s)?\b", text)
        if id_match:
            args["employee_id"] = int(id_match.group(1))
        elif PRONOUN_PATTERN.search(text):
            if context.last_employee_id is not None:
                args["employee_id"] = context.last_employee_id
            else:
                return SupervisorDecision(
                    operation_intent="clarify",
                    target_entity="employee",
                    routed_agent=None,
                    clarification_question=(
                        "Which employee are you referring to? No single employee is currently in context—"
                        "please provide their employee ID or full name."
                    ),
                )
        else:
            poss_match = re.search(
                r"(?i)(?:update|change|modify|set)\s+(?:employee\s+)?([A-Za-z][A-Za-z\s.'\-]+?)'s\b",
                text,
            )
            if poss_match:
                args["target_name"] = poss_match.group(1).strip()
            else:
                for_match = re.search(
                    r"(?i)\b(?:for|of)\s+(?:employee\s+)?([A-Za-z][A-Za-z\s.'\-]+?)\s+to\b",
                    text,
                )
                if for_match:
                    args["target_name"] = for_match.group(1).strip()
                else:
                    move_match = re.search(
                        r"(?i)(?:move|transfer|reassign)\s+(?:employee\s+)?([A-Za-z][A-Za-z\s.'\-]+?)\s+to\b",
                        text,
                    )
                    if move_match and not re.match(r"(?i)^employee\s*\d+$", move_match.group(1).strip()):
                        args["target_name"] = move_match.group(1).strip()

        desig_match = re.search(
            r"(?i)\b(?:designation|title|role|position)\s+(?:(?:of|for)\s+.*?\s+)?(?:to|is|=|:)\s+['\"]?([A-Za-z0-9\s/&\-]+?)['\"]?\s*[.!]*$",
            text,
        )
        if desig_match:
            args["designation"] = desig_match.group(1).strip()

        dept_match = re.search(
            r"(?i)\b(?:department\s+(?:to|is|=|:)|(?:move|transfer|reassign)\s+.*?\bto\s+(?:the\s+)?)['\"]?([A-Za-z0-9\s&\-]+?)(?:\s+department)?['\"]?\s*[.!]*$",
            text,
        )
        if dept_match and "designation" not in args:
            args["department_name"] = dept_match.group(1).strip()

        email_match = re.search(
            r"(?i)\bemail\s+(?:address\s+)?(?:to|is|=|:)\s+['\"]?([^\s'\"]+?)['\"]?\s*[.!]*$",
            text,
        )
        if email_match:
            args["email"] = email_match.group(1).strip()

        name_match = re.search(
            r"(?i)\bname\s+(?:to|is|=|:)\s+['\"]?([A-Za-z][A-Za-z\s.'\-]+?)['\"]?\s*[.!]*$",
            text,
        )
        if name_match:
            args["name"] = name_match.group(1).strip()

        return SupervisorDecision(
            operation_intent="update",
            target_entity="employee",
            routed_agent="update_agent",
            structured_arguments=args,
        )

    def _parse_read(self, text: str, context: ConversationContextSnapshot) -> SupervisorDecision:
        lower = text.lower()
        known_depts = self._get_known_department_names()

        if re.search(r"(?i)\b(show|list|get|view|display)\s+(all\s+)?(the\s+)?departments\b", text):
            return SupervisorDecision(
                operation_intent="read",
                target_entity="department",
                routed_agent="read_agent",
                structured_arguments={},
            )

        dept_id_match = re.search(r"(?i)\bdepartment\s+(?:id\s*)?#?(\d+)\b", text)
        if dept_id_match and "employee" not in lower:
            return SupervisorDecision(
                operation_intent="read",
                target_entity="department",
                routed_agent="read_agent",
                structured_arguments={"department_id": int(dept_id_match.group(1))},
            )

        dept_lookup_match = re.search(
            r"(?i)\b(?:show|find|get|describe)\s+(?:the\s+)?([A-Za-z0-9\s&\-]+?)\s+department\b",
            text,
        )
        if dept_lookup_match and "employee" not in lower:
            return SupervisorDecision(
                operation_intent="read",
                target_entity="department",
                routed_agent="read_agent",
                structured_arguments={"name": dept_lookup_match.group(1).strip()},
            )

        emp_id_match = re.search(r"(?i)\b(?:employee|emp|id)\s*(?:with\s+id\s+)?#?(\d+)\b", text)
        if emp_id_match:
            return SupervisorDecision(
                operation_intent="read",
                target_entity="employee",
                routed_agent="read_agent",
                structured_arguments={"employee_id": int(emp_id_match.group(1)), "single_lookup": True},
            )

        if PRONOUN_PATTERN.search(text):
            if context.last_employee_id is not None:
                return SupervisorDecision(
                    operation_intent="read",
                    target_entity="employee",
                    routed_agent="read_agent",
                    structured_arguments={"employee_id": context.last_employee_id, "single_lookup": True},
                )
            return SupervisorDecision(
                operation_intent="clarify",
                target_entity="employee",
                routed_agent=None,
                clarification_question=(
                    "Which employee are you asking about? Please specify their employee ID or name."
                ),
            )

        in_dept_match = re.search(
            r"(?i)\b(?:employees|people|staff|who\s+works)\s+(?:working\s+)?in\s+(?:the\s+)?([A-Za-z0-9\s&\-]+?)(?:\s+department)?\s*[.?!]*$",
            text,
        )
        if in_dept_match:
            dept_candidate = in_dept_match.group(1).strip()
            return SupervisorDecision(
                operation_intent="read",
                target_entity="employee",
                routed_agent="read_agent",
                structured_arguments={"department_name": dept_candidate},
            )

        if re.search(r"(?i)\b(show|list|get|view|display)\s+(all\s+)?(the\s+)?employees\b", text):
            return SupervisorDecision(
                operation_intent="read",
                target_entity="employee",
                routed_agent="read_agent",
                structured_arguments={},
            )

        email_match = re.search(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})", text)
        if email_match:
            return SupervisorDecision(
                operation_intent="read",
                target_entity="employee",
                routed_agent="read_agent",
                structured_arguments={"email": email_match.group(1).lower(), "single_lookup": True},
            )

        name_lookup_match = re.search(
            r"(?i)(?:find|search(?:\s+for)?|who\s+is|show|get|fetch|lookup)\s+(?:all\s+)?(?:the\s+)?(?:details\s+(?:for|of|about)\s+)?(?:an?\s+)?(?:employee\s+(?:with\s+name|named|called)\s+|employee\s+)?([A-Za-z][A-Za-z\s.'\-]+?)\s*[.?!]*$",
            text,
        )
        if name_lookup_match:
            candidate = name_lookup_match.group(1).strip()
            for kd in known_depts:
                if candidate.lower() == kd.lower():
                    return SupervisorDecision(
                        operation_intent="read",
                        target_entity="department",
                        routed_agent="read_agent",
                        structured_arguments={"name": kd},
                    )
            return SupervisorDecision(
                operation_intent="read",
                target_entity="employee",
                routed_agent="read_agent",
                structured_arguments={"name": candidate, "single_lookup": True},
            )

        return SupervisorDecision(
            operation_intent="read",
            target_entity="employee",
            routed_agent="read_agent",
            structured_arguments={},
        )
