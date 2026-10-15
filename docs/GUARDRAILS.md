# Guardrails & Safety Reference

Prompts are not treated as a security boundary. All scope restrictions, tool permissions, input validations, and destructive-operation safeguards are enforced deterministically in Python.

## 1. Domain Scope Restrictions (`app/services/permissions.py`)

`inspect_message_guardrails(message)` and `enforce_intent_and_entity(intent, target_entity)` enforce strict domain boundaries before any specialized agent runs:
- **Allowed Entities**: `employee`, `department`, `organization` (for department headcount summaries), and `none`.
- **Allowed Intents**: `read`, `create`, `update`, `delete`, `confirm`, `cancel`, `clarify`, `out_of_scope`.
- **Out-of-Scope HR Domains Rejected**: Payroll, salaries, bonuses, compensation, attendance, timesheets, PTO/sick leave, performance reviews/appraisals, and recruitment/job postings.
- **General-Purpose Requests Rejected**: Creative writing (poems, stories, jokes), weather, recipes, general trivia, and coding exercises.

## 2. Prompt-Injection & Arbitrary SQL Protection

- **Prompt Injection**: Patterns attempting to override instructions (`"Ignore all previous instructions..."`, `"System override"`, `"Developer mode"`, `"Forget your rules and bypass confirmation"`) are intercepted and rejected with `error_category="prompt_injection"`.
- **No Raw SQL**: No tool in the application accepts raw SQL strings. Any user message containing SQL statements (`DROP TABLE`, `DELETE FROM`, `SELECT * FROM`, `; DROP ...`) is rejected upfront with `error_category="arbitrary_sql"`, and all repository queries use parameterized `?` placeholders exclusively.

## 3. Per-Agent Tool Permission Matrix (`enforce_tool_permission`)

Every tool in `app/tools/employee_tools.py` and `app/tools/department_tools.py` requires `caller_agent` and invokes `enforce_tool_permission(caller_agent, tool_name)` prior to calling `EmployeeInformationService`:
- `supervisor` has `0` permitted tools and cannot mutate or query data directly.
- `read_agent` can only invoke read tools (`list_*`, `get_*`, `search_*`, `get_department_employee_counts`).
- `create_agent` can only invoke `create_employee` and `create_department`.
- `update_agent` can only invoke `update_employee` and `update_department`.
- `delete_agent` can only invoke `prepare_delete_*` and `execute_delete_*`.
- Any attempt to invoke an unregistered tool or a tool belonging to another agent raises `PermissionDeniedError`.

## 4. Input Validation & Database Integrity (`app/services/validation.py`)

- **Required Fields**: Employee creation requires non-empty `name`, valid `email` (`^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$`), non-empty `designation`, and an existing `department`. Department creation requires a non-empty `name`.
- **Case-Insensitive Uniqueness**: Enforced at both the service layer and SQLite schema (`UNIQUE COLLATE NOCASE`) for `departments.name` and `employees.email`.
- **Accidental Duplicate Creation Protection**: Creating an employee with the same `(name, designation, department_id)` as an existing employee is rejected with `DuplicateRecordError`.
- **Foreign-Key & Department Deletion Protection**: `PRAGMA foreign_keys = ON` is enabled on every SQLite connection with `ON DELETE RESTRICT`. Attempting to delete a department while employees are still assigned to it raises `ReferentialIntegrityError` during both the `prepare` and `execute` stages.

## 5. Two-Step Deletion Confirmation Safeguards

Destructive operations (`delete_employee` and `delete_department`) never execute on the initial request turn:
1. **Stage 1 (`prepare_delete_*`)**: Resolves the target record, verifies it is unambiguous (and empty if a department), writes a `PendingConfirmation` row into `pending_confirmations` with a unique `confirmation_id` (`conf-emp-105-...`), and returns `status="awaiting_confirmation"`.
2. **Stage 2 (`execute_delete_*`)**: Requires a subsequent explicit confirmation turn (`"yes"`, `"confirm"`). `ConversationMemoryStore.consume_pending_confirmation()` verifies that:
   - An active `pending` confirmation exists for `conversation_id`.
   - `confirmation_id` matches the active token.
   - `target_entity` and `target_id` match the bound record (so confirming `"employee 102"` when `"employee 105"` is pending fails with `ConfirmationRequiredError`).
3. **Single-Use Consumption**: Once confirmed or cancelled, the pending confirmation row is deleted so re-sending `"yes"` cannot replay or authorize another deletion.

## 6. Known Limitations

- **No User Authentication / RBAC**: As designed for this demonstration scope, there is no login system or per-user role-based access control; permissions are enforced at the operation and agent-tool boundary.
- **Single Active Pending Confirmation per Conversation**: Each `conversation_id` holds at most one active pending deletion confirmation at a time; initiating a new deletion in the same conversation replaces the prior unconfirmed deletion.
