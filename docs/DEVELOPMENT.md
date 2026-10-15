# Developer Guide

## 1. Repository Layout

```text
employee-agent-system/
├── app/
│   ├── main.py                     # CLI entrypoint (--seed, --message, interactive REPL)
│   ├── config.py                   # Environment-based configuration & live-LLM validation
│   ├── graph.py                    # LangGraph StateGraph builder & workflow runner
│   ├── state.py                    # AgentState TypedDict, Pydantic contracts & prompt loader
│   ├── agents/
│   │   ├── supervisor.py           # Supervisor routing, context resolution & response synthesis
│   │   ├── read_agent.py           # Read-only employee/department/headcount agent
│   │   ├── create_agent.py         # Employee & department creation agent
│   │   ├── update_agent.py         # Partial-update agent for employees & departments
│   │   └── delete_agent.py         # Two-phase confirmation delete agent
│   ├── prompts/
│   │   ├── supervisor.md           # Supervisor system prompt
│   │   ├── read_agent.md           # Read Agent system prompt
│   │   ├── create_agent.md         # Create Agent system prompt
│   │   ├── update_agent.md         # Update Agent system prompt
│   │   └── delete_agent.md         # Delete Agent system prompt
│   ├── tools/
│   │   ├── employee_tools.py       # LangChain StructuredTools for Employee operations
│   │   └── department_tools.py     # LangChain StructuredTools for Department operations
│   ├── database/
│   │   ├── connection.py           # SQLite connection manager, schema init & demo seeding
│   │   ├── models.py               # Pydantic entity models & tool input schemas
│   │   └── repositories.py         # Parameterized SQL repositories (Employee, Department, Audit)
│   ├── services/
│   │   ├── employee_service.py     # Validated business logic & audit logging
│   │   ├── validation.py           # Domain exceptions & deterministic field validators
│   │   └── permissions.py          # Allowlists, agent-tool permissions & guardrail regexes
│   ├── memory/
│   │   └── conversation_memory.py  # SQLite-backed turns, entity context & pending confirmations
│   └── logging/
│       └── setup.py                # Shared JSON logger with secret scrubbing & file rotation
├── data/                           # Default directory for local SQLite database files
├── logs/                           # Default directory for rotating operational logs
├── tests/
│   ├── conftest.py                 # Shared temporary SQLite fixtures
│   ├── unit/                       # Unit tests for DB, services, validation & memory
│   ├── integration/                # End-to-end LangGraph workflow & multi-turn tests
│   └── test_guardrails.py          # Security, scope, SQLi, prompt-injection & confirmation tests
├── evals/
│   ├── dataset.json                # 28 benchmark evaluation cases
│   └── run_evals.py                # Automated evaluation runner & scorecard reporter
└── docs/                           # Technical documentation
```

## 2. Coding Conventions

- **Strict Layering**: Never call `sqlite3` directly from agents or tools. Always follow `Agent -> StructuredTool -> EmployeeInformationService -> Repository -> SQLite`.
- **Parameterized SQL Only**: Every SQL query in `app/database/repositories.py` must use `?` placeholders.
- **Explicit Domain Exceptions**: Raise specific subclasses of `DomainError` (`ValidationErrorApp`, `NotFoundError`, `DuplicateRecordError`, `ReferentialIntegrityError`, `AmbiguousTargetError`, `ConfirmationRequiredError`, `PermissionDeniedError`) from services and catch them cleanly in agents.
- **No Hardcoded Prompts**: Store agent prompt instructions in `app/prompts/*.md` and load them via `load_prompt()`.

## 3. How to Add a New Tool

1. **Define the Pydantic Input Schema**: Add the input model in `app/tools/employee_tools.py` or `app/tools/department_tools.py`, including `caller_agent: str`.
2. **Implement the Service Method**: Add the business logic and audit logging method to `EmployeeInformationService` in `app/services/employee_service.py`.
3. **Register Permission**: Add the tool name to the appropriate agent's set in `AGENT_TOOL_PERMISSIONS` inside `app/services/permissions.py`.
4. **Wrap as `StructuredTool`**: Inside `build_employee_tools` or `build_department_tools`, call `enforce_tool_permission(caller_agent, "<tool_name>")` first, invoke the service method, and return a `StructuredTool.from_function(...)`.
5. **Update Agent & Prompt**: Invoke the tool from the designated agent module and document the tool in `app/prompts/<agent>.md` and `docs/AGENTS.md`.
6. **Add Tests**: Add unit tests in `tests/unit/`, integration tests in `tests/integration/`, and negative permission tests in `tests/test_guardrails.py`.

## 4. How to Modify Prompts Safely

1. Edit the target Markdown file in `app/prompts/*.md`.
2. Keep all 9 required sections intact (`Role and Objective`, `Supported Operations`, `Inputs and Expected Structured Outputs`, `Available Tools`, `Constraints and Forbidden Actions`, `Missing-Information and Ambiguity Behavior`, `Error-Handling Behavior`, `Rules for Reporting Execution Results`, `Representative Examples`).
3. Ensure the JSON schema fields in `supervisor.md` match `SupervisorDecision` in `app/state.py`.
4. Run `pytest -v` and `python3 evals/run_evals.py` after any prompt or routing change to verify zero regressions.

## 5. How to Add Evaluation Cases

Add a new JSON object to `evals/dataset.json` with the following structure:
```json
{
  "id": "eval_29_custom_case",
  "category": "crud_read",
  "description": "Description of the scenario being evaluated",
  "setup_turns": [],
  "input": "Natural language user prompt",
  "expected_intent": "read",
  "expected_entity": "employee",
  "expected_agent": "read_agent",
  "expected_status": "completed",
  "expected_args_subset": {"employee_id": 101},
  "expected_response_substrings": ["Aarav Patel", "Engineering Manager"],
  "db_assertion": {"employee_count": 5},
  "is_guardrail_case": false
}
```
Then execute `python3 evals/run_evals.py` to verify all metrics remain at `100%`.
