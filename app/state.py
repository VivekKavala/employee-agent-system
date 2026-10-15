"""Typed shared state definitions, structured routing models, and prompt loader."""
from pathlib import Path
from typing import Any, Dict, Optional, TypedDict
from pydantic import BaseModel, Field

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class AgentState(TypedDict, total=False):
    """Shared state passed across nodes in the LangGraph Supervisor workflow."""

    conversation_id: str
    current_message: str
    operation_intent: str
    target_entity: str
    routed_agent: Optional[str]
    structured_arguments: Dict[str, Any]
    pending_confirmation: Optional[Dict[str, Any]]
    tool_result: Optional[Dict[str, Any]]
    status: str
    final_response: str


class SupervisorDecision(BaseModel):
    """Structured routing output produced by the Supervisor Agent."""

    operation_intent: str = Field(
        ...,
        description="One of: read, create, update, delete, confirm, cancel, clarify, out_of_scope",
    )
    target_entity: str = Field(
        default="none",
        description="One of: employee, department, organization, none",
    )
    routed_agent: Optional[str] = Field(
        default=None,
        description="One of: read_agent, create_agent, update_agent, delete_agent, or None",
    )
    structured_arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Validated structured parameters extracted for the target operation.",
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description="Clarifying question if required fields are missing or target identity is ambiguous.",
    )
    rejection_reason: Optional[str] = Field(
        default=None,
        description="Explanation when a request is rejected as out of scope or unsafe.",
    )


class ToolExecutionResult(BaseModel):
    """Standardized output contract returned by specialized agents and tools."""

    agent_name: str
    tool_name: str
    status: str
    data: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    error_category: Optional[str] = None


def load_prompt(agent_name: str) -> str:
    """Load the Markdown system prompt for `agent_name` from `app/prompts/{agent_name}.md`."""
    clean_name = agent_name.strip().lower()
    if not clean_name.endswith(".md"):
        clean_name = f"{clean_name}.md"
    prompt_path = PROMPTS_DIR / clean_name
    if not prompt_path.exists():
        raise FileNotFoundError(f"Agent prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")
