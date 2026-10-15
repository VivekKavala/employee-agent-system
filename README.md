# Employee Information Multi-Agent System

A production-minded, deliberately straightforward **Employee and Department Information Multi-Agent System** built with **Python**, **LangGraph**, **LangChain**, **Pydantic**, and **SQLite**.

The system enables users to query, create, update, and safely delete `Employee` and `Department` records through natural-language conversations while enforcing strict domain boundaries, per-agent tool permissions, referential integrity, persistent conversation context, and two-step destructive-action confirmations.

## Architecture Summary

All requests follow a single, auditable execution path:

```text
User -> Supervisor Agent -> Specialized Agent (Read | Create | Update | Delete)
     -> Approved LangChain StructuredTool -> EmployeeInformationService
     -> Parameterized SQLite Repository -> SQLite Database
```

Key architectural properties:
- **Single Repository & Single Database**: One SQLite database (`data/employee_system.db` by default) with `PRAGMA foreign_keys = ON` enforced on every connection.
- **Five Focused Agents**:
  - `Supervisor Agent`: Classifies intent and target entity, enforces domain guardrails, resolves unambiguous conversational pronouns, manages confirmation/cancellation of pending deletions, routes to at most one specialized agent, and formats grounded responses.
  - `Read Agent`: Invokes read-only employee, department, and headcount tools.
  - `Create Agent`: Invokes creation tools after validating required fields, uniqueness, and foreign-key relationships.
  - `Update Agent`: Invokes partial-update tools while preserving unspecified fields.
  - `Delete Agent`: Enforces a mandatory two-step confirmation lifecycle (`prepare_delete_*` followed by `execute_delete_*`) and prevents deleting departments that still have assigned employees.
- **Readable Prompt Files**: Loaded dynamically from `app/prompts/*.md`.
- **Configurable LLM Provider**: Works with free-tier OpenAI-compatible APIs (`groq`, `gemini_openai`, `openai_compatible`, `openai`, `ollama`) as well as an offline `deterministic` mode for deterministic testing and evaluation without paid API keys.

## Prerequisites

- Python `3.10+` (tested on Python `3.11.2`)
- SQLite 3 (bundled with Python standard library `sqlite3`)

## Environment Setup & Installation

1. Clone or unpack the repository and navigate to the project root:
   ```bash
   cd employee-agent-system
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and configure your preferred provider:
   ```bash
   cp .env.example .env
   ```
   - **Hosted Free-Tier LLM (e.g., Groq or Google Gemini OpenAI endpoint)**:
     Set `LLM_PROVIDER=groq` (or `gemini_openai`), `LLM_MODEL`, and `LLM_API_KEY`.
   - **Deterministic Offline Mode (no API key required)**:
     Set `LLM_PROVIDER=deterministic` in `.env` or pass `--provider deterministic` on the CLI.

## Database Initialization & Demo Seeding

The SQLite schema initializes automatically on startup. To seed the demonstration dataset (4 departments and 5 employees, IDs `101`–`105`), pass `--seed`:

```bash
python3 -m app.main --provider deterministic --seed -m "Show all employees."
```

Seeding is strictly idempotent and opt-in: `seed_demo_data()` only inserts records when both `departments` and `employees` tables are completely empty and never overwrites existing records.

### Seeded Demo Dataset

| Entity | ID | Name | Details |
| :--- | :--- | :--- | :--- |
| Department | 1 | Engineering | Core product, platform, and software engineering (3 employees) |
| Department | 2 | Human Resources | People operations, talent management, and workplace policies (1 employee) |
| Department | 3 | Finance | Financial planning, accounting, and compliance (1 employee) |
| Department | 4 | Design | Product design and user experience (0 employees) |
| Employee | 101 | Aarav Patel | Engineering Manager, Engineering (`aarav.patel@example.com`) |
| Employee | 102 | Priya Nair | Software Developer, Engineering (`priya.nair@example.com`) |
| Employee | 103 | Rohan Verma | DevOps Engineer, Engineering (`rohan.verma@example.com`) |
| Employee | 104 | Ananya Iyer | HR Business Partner, Human Resources (`ananya.iyer@example.com`) |
| Employee | 105 | Vikram Singh | Financial Analyst, Finance (`vikram.singh@example.com`) |

## Startup Commands

### Interactive CLI Mode
```bash
# With configured LLM in .env:
python3 -m app.main --seed --conversation-id demo-session

# Or in deterministic offline mode:
python3 -m app.main --provider deterministic --seed --conversation-id demo-session
```

### Single-Command Non-Interactive Mode
```bash
python3 -m app.main --provider deterministic --seed --conversation-id demo-1 -m "How many employees work in each department?"
```

## Automated Test Commands

Run the full automated test suite (42 unit, integration, and guardrail tests) using a temporary SQLite database:

```bash
pytest -v
```

Run individual test suites:
```bash
pytest tests/unit -v
pytest tests/integration -v
pytest tests/test_guardrails.py -v
```

## Evaluation Suite Commands

Run the 28-case evaluation benchmark (`evals/dataset.json`):

```bash
python3 evals/run_evals.py
```

Measured evaluation results on the 28-case benchmark:
- **Total Cases**: 28
- **Passed Cases**: 28 (`0` failed)
- **Routing Accuracy**: `100.0%` (`28/28`)
- **Argument Correctness**: `100.0%` (`28/28`)
- **Task Success Rate**: `100.0%` (`28/28`)
- **Guardrail Pass Rate**: `100.0%` (`17/17`)

## Example Conversations

### 1. Reading, Context Follow-Up, and Pronoun Resolution
```text
[demo-1] You > Find employee 102.
Assistant > Employee ID 102: Priya Nair | Designation: Software Developer | Department: Engineering (ID 1) | Email: priya.nair@example.com.

[demo-1] You > Change his designation to Team Lead.
Assistant > Updated employee Priya Nair (ID 102): designation='Team Lead', department='Engineering', email='priya.nair@example.com'.
```

### 2. Creating Departments and Employees
```text
[demo-1] You > Create a department called Security with description Application and cloud security.
Assistant > Created department 'Security' (ID 5).

[demo-1] You > Add Rahul Sharma as a Software Engineer in Engineering.
Assistant > Created employee Rahul Sharma (ID 106) as Software Engineer in Engineering (email: rahul.sharma@example.com).
```

### 3. Two-Step Deletion Confirmation & Cancellation
```text
[demo-1] You > Delete employee 105.
Assistant > Confirmation required: You are about to permanently delete employee Vikram Singh (ID 105, Financial Analyst in Finance). [Confirmation ID: conf-emp-105-...] Please reply 'yes' or 'confirm' to proceed, or 'no' / 'cancel' to abort.

[demo-1] You > No, cancel.
Assistant > Cancelled pending deletion of Employee Vikram Singh (ID 105, Financial Analyst, Department: Finance, Email: vikram.singh@example.com) [Confirmation ID: conf-emp-105-...]. No changes were made to the database.
```

### 4. Out-of-Scope Rejection
```text
[demo-1] You > Write me a poem.
Assistant > Request rejected: This assistant is restricted to Employee and Department information management and cannot answer general-purpose or creative requests.
```

## Documentation Index

- [Architecture Guide](docs/ARCHITECTURE.md)
- [Agents & Prompts Reference](docs/AGENTS.md)
- [Guardrails & Safety Reference](docs/GUARDRAILS.md)
- [Developer Guide](docs/DEVELOPMENT.md)
