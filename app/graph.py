"""LangGraph StateGraph assembly and workflow execution façade."""
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, Union

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.create_agent import CreateAgent
from app.agents.delete_agent import DeleteAgent
from app.agents.read_agent import ReadAgent
from app.agents.supervisor import SupervisorAgent
from app.agents.update_agent import UpdateAgent
from app.config import AppConfig, load_config
from app.memory.conversation_memory import ConversationMemoryStore
from app.services.employee_service import EmployeeInformationService
from app.state import AgentState
from app.tools.department_tools import build_department_tools
from app.tools.employee_tools import build_employee_tools


def _create_checkpointer(db_path: Path) -> Any:
    """Create a LangGraph checkpointer using SqliteSaver when available, falling back to MemorySaver.

    Note: All application conversation turns, entity context, and pending confirmations are
    independently persisted in SQLite via `ConversationMemoryStore`.
    """
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        return SqliteSaver(conn)
    except Exception:
        return MemorySaver()


class EmployeeAgentWorkflow:
    """Orchestrates the Supervisor and specialized CRUD agents via a compiled LangGraph StateGraph."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        *,
        db_path: Optional[Union[str, Path]] = None,
        llm_client: Optional[Any] = None,
    ) -> None:
        self.config = config or load_config(override_db_path=Path(db_path) if db_path else None)
        if db_path is not None:
            self.db_path = Path(db_path)
        else:
            self.db_path = self.config.database_path

        self.service = EmployeeInformationService(self.db_path)
        self.memory = ConversationMemoryStore(self.db_path)

        # Build restricted tool registries
        emp_tools = build_employee_tools(self.service, self.memory)
        dept_tools = build_department_tools(self.service, self.memory)
        self.all_tools = {**emp_tools, **dept_tools}

        # Instantiate agents
        self.supervisor = SupervisorAgent(
            config=self.config,
            service=self.service,
            memory=self.memory,
            llm_client=llm_client,
        )
        self.read_agent = ReadAgent(self.all_tools)
        self.create_agent = CreateAgent(self.all_tools)
        self.update_agent = UpdateAgent(self.all_tools, self.service)
        self.delete_agent = DeleteAgent(self.all_tools)

        self.checkpointer = _create_checkpointer(self.db_path)
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(AgentState)

        builder.add_node("supervisor_route", self.supervisor.route)
        builder.add_node("read_agent", self.read_agent.run)
        builder.add_node("create_agent", self.create_agent.run)
        builder.add_node("update_agent", self.update_agent.run)
        builder.add_node("delete_agent", self.delete_agent.run)
        builder.add_node("supervisor_finalize", self.supervisor.finalize)

        builder.add_edge(START, "supervisor_route")

        def route_selector(state: AgentState) -> str:
            if state.get("status") in ("rejected", "awaiting_clarification", "cancelled"):
                return "supervisor_finalize"
            routed = state.get("routed_agent")
            if routed in ("read_agent", "create_agent", "update_agent", "delete_agent"):
                return routed
            return "supervisor_finalize"

        builder.add_conditional_edges(
            "supervisor_route",
            route_selector,
            {
                "read_agent": "read_agent",
                "create_agent": "create_agent",
                "update_agent": "update_agent",
                "delete_agent": "delete_agent",
                "supervisor_finalize": "supervisor_finalize",
            },
        )

        builder.add_edge("read_agent", "supervisor_finalize")
        builder.add_edge("create_agent", "supervisor_finalize")
        builder.add_edge("update_agent", "supervisor_finalize")
        builder.add_edge("delete_agent", "supervisor_finalize")
        builder.add_edge("supervisor_finalize", END)

        return builder.compile(checkpointer=self.checkpointer)

    def handle_message(
        self,
        message: str,
        *,
        conversation_id: str = "default-session",
    ) -> Dict[str, Any]:
        """Process a single user message within `conversation_id` and return the final state."""
        initial_state: AgentState = {
            "conversation_id": conversation_id,
            "current_message": message,
            "operation_intent": "read",
            "target_entity": "none",
            "routed_agent": None,
            "structured_arguments": {},
            "pending_confirmation": None,
            "tool_result": None,
            "status": "pending",
            "final_response": "",
        }
        run_config = {"configurable": {"thread_id": conversation_id}}
        return self.graph.invoke(initial_state, config=run_config)
