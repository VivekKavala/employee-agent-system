# Architecture Guide

## 1. High-Level Overview

The Employee Information Multi-Agent System is structured as a single Python application using a **LangGraph Supervisor StateGraph** backed by a single **SQLite** database. Every user message traverses a strict layered boundary:

```text
User CLI / Caller
  │
  ▼
LangGraph StateGraph (app/graph.py)
  ├── 1. supervisor_route (app/agents/supervisor.py)
  │        ├── Guardrail & Prompt-Injection Filter (app/services/permissions.py)
  │        ├── Conversation Context & Pending Confirmation Lookup (app/memory/conversation_memory.py)
  │        └── Intent, Entity & Structured Argument Extraction
  │
  ├── 2. Specialized Agent Node (exactly one selected via conditional edge)
  │        ├── read_agent   (app/agents/read_agent.py)
  │        ├── create_agent (app/agents/create_agent.py)
  │        ├── update_agent (app/agents/update_agent.py)
  │        └── delete_agent (app/agents/delete_agent.py)
  │               │
  │               ▼
  │      Restricted LangChain StructuredTools (app/tools/*.py)
  │        └── Enforces per-agent tool allowlist (enforce_tool_permission)
  │               │
  │               ▼
  │      Application Service Layer (app/services/employee_service.py)
  │        └── Enforces business validation, uniqueness, FK rules, and audit logging
  │               │
  │               ▼
  │      Parameterized SQLite Repositories (app/database/repositories.py)
  │        └── Executes parameterized SQL inside transactions with PRAGMA foreign_keys = ON
  │
  └── 3. supervisor_finalize (app/agents/supervisor.py)
           ├── Updates ConversationMemoryStore (turns, active entity focus, pending clarifications)
           └── Formats concise, grounded final_response from verified tool_result
```

## 2. Shared State (`AgentState`)

Defined in `app/state.py`, the `AgentState` `TypedDict` carries structured state across graph nodes:

- `conversation_id` (`str`): Unique session identifier used for memory and checkpointing.
- `current_message` (`str`): The raw user message for the current turn.
- `operation_intent` (`str`): Allowlisted intent (`read`, `create`, `update`, `delete`, `confirm`, `cancel`, `clarify`, `out_of_scope`).
- `target_entity` (`str`): Allowlisted entity (`employee`, `department`, `organization`, `none`).
- `routed_agent` (`Optional[str]`): Selected specialized agent (`read_agent`, `create_agent`, `update_agent`, `delete_agent`, or `None`).
- `structured_arguments` (`Dict[str, Any]`): Validated parameters extracted for the target operation.
- `pending_confirmation` (`Optional[Dict[str, Any]]`): Active confirmation metadata when a deletion is staged.
- `tool_result` (`Optional[Dict[str, Any]]`): Standardized `ToolExecutionResult` dictionary produced by the specialized agent.
- `status` (`str`): Execution status (`pending`, `routed`, `awaiting_clarification`, `awaiting_confirmation`, `completed`, `cancelled`, `rejected`, `error`).
- `final_response` (`str`): Grounded natural-language response returned to the user.

## 3. Database Schema & Boundaries

Initialized in `app/database/connection.py` with `PRAGMA foreign_keys = ON;` enforced on every connection:

### Business Tables
1. **`departments`**
   - `id`: `INTEGER PRIMARY KEY AUTOINCREMENT`
   - `name`: `TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK(length(trim(name)) > 0)`
   - `description`: `TEXT` (nullable)
   - `created_at`: `TEXT NOT NULL` (UTC ISO-8601 timestamp)
2. **`employees`**
   - `id`: `INTEGER PRIMARY KEY AUTOINCREMENT` (auto-increment sequence initialized at `100` so IDs start at `101`)
   - `name`: `TEXT NOT NULL CHECK(length(trim(name)) > 0)`
   - `email`: `TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK(email LIKE '%_@_%.__%')`
   - `designation`: `TEXT NOT NULL CHECK(length(trim(designation)) > 0)`
   - `department_id`: `INTEGER NOT NULL REFERENCES departments(id) ON DELETE RESTRICT ON UPDATE CASCADE`
   - `created_at`: `TEXT NOT NULL`
   - `updated_at`: `TEXT NOT NULL`

### Internal Persistence & Audit Tables
3. **`audit_logs`**: Tracks every database mutation and rejected mutation (`timestamp`, `conversation_id`, `agent_name`, `operation`, `entity_type`, `entity_id`, `outcome`, `details_json`).
4. **`conversation_turns`**: Persists user and assistant turns with automatic secret scrubbing.
5. **`conversation_context`**: Persists `last_employee_id`, `last_employee_name`, `last_department_id`, `last_department_name`, and `pending_clarification_json` across restarts.
6. **`pending_confirmations`**: Persists bound deletion confirmation tokens (`confirmation_id`, `conversation_id`, `operation`, `target_entity`, `target_id`, `target_summary`, `payload_json`, `status`, `created_at`).
7. **`langgraph_checkpoints`**: Used by `SqliteSaver` to checkpoint graph state by `thread_id`.

## 4. Conversation Memory & Pronoun Resolution

`ConversationMemoryStore` (`app/memory/conversation_memory.py`) is used strictly to resolve conversational references—never as the source of truth for employee attributes:
- When a turn resolves a **single unambiguous employee** (e.g., `"Find employee 102"`, `"Add Rahul Sharma..."`, or `"Update employee 102..."`), `last_employee_id` is updated in `conversation_context`.
- When a subsequent turn uses a pronoun (`"Change his designation to Team Lead"`), the Supervisor resolves `employee_id = last_employee_id` and queries/mutates SQLite directly.
- When a turn returns **multiple employees** (e.g., `"Show all employees"` or `"List employees in Engineering"`), `last_employee_id` is cleared (`None`) so that an ambiguous follow-up like `"Change his designation to Team Lead"` triggers a clarifying question instead of guessing.

## 5. Shared Logging & Auditability

- **Operational Logging (`app/logging/setup.py`)**: Writes single-line JSON records to `logs/app.log` via `RotatingFileHandler` (`5 MB` per file, `3` backups) and optionally to stderr (`--console-logs`). Every log record includes `timestamp`, `level`, `conversation_id`, `agent_name`, `operation`, `status`, `duration_ms`, `error_category`, and `message`. A regex scrubber (`scrub_sensitive_text`) redacts API keys and bearer tokens before formatting.
- **Audit Logging (`AuditRepository`)**: Persists structured audit records in the `audit_logs` SQLite table for all create, update, and delete operations (including rejected duplicates and referential integrity blocks).
