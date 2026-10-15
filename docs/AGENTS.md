# Agents & Prompts Reference

All five agents load their system prompts from readable Markdown files under `app/prompts/` via `load_prompt(agent_name)` in `app/state.py`.

## Summary Matrix

| Agent | Module | Prompt File | Permitted Tools |
| :--- | :--- | :--- | :--- |
| **Supervisor Agent** | `app/agents/supervisor.py` | `app/prompts/supervisor.md` | *None* (routing, guardrails, confirmation, synthesis only) |
| **Read Agent** | `app/agents/read_agent.py` | `app/prompts/read_agent.md` | `list_employees`, `get_employee`, `search_employees`, `list_departments`, `get_department`, `get_department_employee_counts` |
| **Create Agent** | `app/agents/create_agent.py` | `app/prompts/create_agent.md` | `create_employee`, `create_department` |
| **Update Agent** | `app/agents/update_agent.py` | `app/prompts/update_agent.md` | `update_employee`, `update_department` |
| **Delete Agent** | `app/agents/delete_agent.py` | `app/prompts/delete_agent.md` | `prepare_delete_employee`, `execute_delete_employee`, `prepare_delete_department`, `execute_delete_department` |

## 1. Supervisor Agent (`supervisor`)

- **Purpose**: Entry and exit node of the LangGraph workflow (`supervisor_route` and `supervisor_finalize`). Interprets user intent, enforces domain restrictions, resolves conversational context, manages pending confirmations/clarifications, routes to one specialized agent, and formats grounded responses.
- **Prompt Location**: `app/prompts/supervisor.md`
- **Input Contract**: `AgentState` with `conversation_id` and `current_message`.
- **Output Contract**:
  - From `supervisor_route`: Produces `SupervisorDecision` fields (`operation_intent`, `target_entity`, `routed_agent`, `structured_arguments`, `status`).
  - From `supervisor_finalize`: Updates `ConversationMemoryStore` and writes `final_response`.
- **Routing Behavior**:
  - `read` -> `read_agent`
  - `create` -> `create_agent`
  - `update` -> `update_agent`
  - `delete` or `confirm` -> `delete_agent`
  - `cancel`, `clarify`, or `out_of_scope` -> bypasses specialized agents and transitions directly to `supervisor_finalize`.

## 2. Read Agent (`read_agent`)

- **Purpose**: Retrieves employee records, department records, filtered search results, and department headcount summaries.
- **Prompt Location**: `app/prompts/read_agent.md`
- **Available Tools**:
  - `list_employees`: Lists all employees ordered by ID.
  - `get_employee`: Resolves a single employee by `employee_id`, `email`, or unambiguous `name`.
  - `search_employees`: Filters employees by `department_id`, `department_name`, `designation`, `name`, or `email`.
  - `list_departments`: Lists all departments with employee counts.
  - `get_department`: Retrieves a single department by `department_id` or `name`.
  - `get_department_employee_counts`: Computes employee counts per department and organization total.
- **Input/Output Contract**: Reads `target_entity` and `structured_arguments` from `AgentState`; writes `ToolExecutionResult` to `state["tool_result"]`.

## 3. Create Agent (`create_agent`)

- **Purpose**: Creates new employee and department records after checking required fields, email/name uniqueness, and foreign-key validity.
- **Prompt Location**: `app/prompts/create_agent.md`
- **Available Tools**:
  - `create_department`: Creates a new department with unique `name` and optional `description`.
  - `create_employee`: Creates a new employee with `name`, unique `email`, `designation`, and valid `department_id`/`department_name`.
- **Ambiguity / Missing-Field Handling**: If `name`, `designation`, or `department` is missing for an employee (or `name` for a department), returns `status="awaiting_clarification"` and stores `pending_clarification` in conversation context so the user can supply the missing details on the next turn.

## 4. Update Agent (`update_agent`)

- **Purpose**: Modifies specified attributes on existing employee or department records while preserving all unspecified fields.
- **Prompt Location**: `app/prompts/update_agent.md`
- **Available Tools**:
  - `update_employee`: Updates `name`, `email`, `designation`, or `department_id`/`department_name` for an existing `employee_id`.
  - `update_department`: Updates `new_name` or `description` for an existing department.
- **Ambiguity Handling**: If an update targets an employee by name and multiple employees share that name (e.g., two `"Kiran Rao"` records), catches `AmbiguousTargetError` and returns `status="awaiting_clarification"` with candidate IDs.

## 5. Delete Agent (`delete_agent`)

- **Purpose**: Safely stages and executes deletions of employees and departments under a mandatory two-phase confirmation protocol.
- **Prompt Location**: `app/prompts/delete_agent.md`
- **Available Tools**:
  - `prepare_delete_employee`: Verifies target employee and creates a `PendingConfirmation` in SQLite.
  - `execute_delete_employee`: Consumes the bound `confirmation_id` and deletes the employee.
  - `prepare_delete_department`: Verifies target department has `0` assigned employees and creates a `PendingConfirmation`.
  - `execute_delete_department`: Consumes the bound `confirmation_id` and deletes the empty department.
