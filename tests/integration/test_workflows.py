"""Integration tests for Supervisor routing, CRUD workflows, multi-turn context,
ambiguous identity clarification, and destructive confirmation/cancellation.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from app.config import AppConfig
from app.graph import EmployeeAgentWorkflow
from app.state import SupervisorDecision


@pytest.fixture
def workflow(seeded_db_path: Path, tmp_path: Path) -> EmployeeAgentWorkflow:
    cfg = AppConfig(
        llm_provider="deterministic",
        llm_model="deterministic-test",
        database_path=seeded_db_path,
        log_file_path=tmp_path / "test_app.log",
    )
    return EmployeeAgentWorkflow(config=cfg)


def test_supervisor_routing_and_read_workflows(workflow: EmployeeAgentWorkflow) -> None:
    # 1. Show all employees
    s1 = workflow.handle_message("Show all employees.", conversation_id="int-read-1")
    assert s1["operation_intent"] == "read"
    assert s1["target_entity"] == "employee"
    assert s1["routed_agent"] == "read_agent"
    assert s1["status"] == "completed"
    assert "Found 5 employee(s)" in s1["final_response"]
    assert "Priya Nair" in s1["final_response"]

    # 2. Find employee 102
    s2 = workflow.handle_message("Find employee 102.", conversation_id="int-read-1")
    assert s2["routed_agent"] == "read_agent"
    assert s2["tool_result"]["tool_name"] == "get_employee"
    assert "Employee ID 102: Priya Nair" in s2["final_response"]
    assert "Software Developer" in s2["final_response"]

    # 3. List employees in Engineering
    s3 = workflow.handle_message("List employees in Engineering.", conversation_id="int-read-1")
    assert s3["routed_agent"] == "read_agent"
    assert s3["tool_result"]["tool_name"] == "search_employees"
    assert s3["tool_result"]["data"]["count"] == 3

    # 4. How many employees work in each department?
    s4 = workflow.handle_message("How many employees work in each department?", conversation_id="int-read-1")
    assert s4["operation_intent"] == "read"
    assert s4["target_entity"] == "organization"
    assert s4["routed_agent"] == "read_agent"
    assert s4["tool_result"]["tool_name"] == "get_department_employee_counts"
    assert "Engineering (ID 1): 3 employee(s)" in s4["final_response"]
    assert "Total: 5 employee(s)" in s4["final_response"]


def test_create_and_update_workflows_match_database(workflow: EmployeeAgentWorkflow) -> None:
    conv_id = "int-crud-1"
    # Create a new department
    c_dept = workflow.handle_message(
        "Create a department called Platform with description Cloud infrastructure and SRE.",
        conversation_id=conv_id,
    )
    assert c_dept["routed_agent"] == "create_agent"
    assert c_dept["status"] == "completed"
    dept_id = c_dept["tool_result"]["data"]["department"]["id"]
    assert workflow.service.get_department(department_id=dept_id)["name"] == "Platform"

    # Create a new employee in Engineering
    c_emp = workflow.handle_message(
        "Add Rahul Sharma as a Software Engineer in Engineering.",
        conversation_id=conv_id,
    )
    assert c_emp["routed_agent"] == "create_agent"
    assert c_emp["status"] == "completed"
    emp_data = c_emp["tool_result"]["data"]["employee"]
    assert emp_data["name"] == "Rahul Sharma"
    assert emp_data["designation"] == "Software Engineer"
    assert emp_data["department_name"] == "Engineering"
    assert emp_data["email"] == "rahul.sharma@example.com"

    # Update employee 102's designation to Senior Developer
    u_emp = workflow.handle_message(
        "Update employee 102's designation to Senior Developer.",
        conversation_id=conv_id,
    )
    assert u_emp["routed_agent"] == "update_agent"
    assert u_emp["status"] == "completed"
    db_emp_102 = workflow.service.get_employee(102)
    assert db_emp_102["designation"] == "Senior Developer"
    assert db_emp_102["name"] == "Priya Nair"  # Unspecified fields preserved
    assert db_emp_102["email"] == "priya.nair@example.com"


def test_multi_turn_context_preservation_and_restart_persistence(
    seeded_db_path: Path, tmp_path: Path
) -> None:
    conv_id = "int-context-restart"
    cfg = AppConfig(
        llm_provider="deterministic",
        llm_model="deterministic-test",
        database_path=seeded_db_path,
        log_file_path=tmp_path / "test_app.log",
    )

    # Instance 1: Find employee 102
    wf1 = EmployeeAgentWorkflow(config=cfg)
    turn1 = wf1.handle_message("Find employee 102.", conversation_id=conv_id)
    assert turn1["status"] == "completed"
    assert "Priya Nair" in turn1["final_response"]

    # Simulate application restart with a fresh workflow instance connected to same SQLite DB
    wf2 = EmployeeAgentWorkflow(config=cfg)
    turn2 = wf2.handle_message("Change his designation to Team Lead.", conversation_id=conv_id)
    assert turn2["routed_agent"] == "update_agent"
    assert turn2["status"] == "completed"
    assert wf2.service.get_employee(102)["designation"] == "Team Lead"

    # Now run a multi-employee query ("Show all employees.") which clears single-employee focus
    turn3 = wf2.handle_message("Show all employees.", conversation_id=conv_id)
    assert turn3["status"] == "completed"

    # Pronoun follow-up after listing multiple employees must ask for clarification instead of guessing
    turn4 = wf2.handle_message("Change his designation to Director.", conversation_id=conv_id)
    assert turn4["status"] == "awaiting_clarification"
    assert "Which employee" in turn4["final_response"]


def test_clarification_for_ambiguous_employee_identities(workflow: EmployeeAgentWorkflow) -> None:
    conv_id = "int-ambig-1"
    # Create two employees with the same name "Kiran Rao"
    workflow.service.create_employee(
        name="Kiran Rao",
        email="kiran.eng@example.com",
        designation="Backend Developer",
        department_name="Engineering",
    )
    emp2 = workflow.service.create_employee(
        name="Kiran Rao",
        email="kiran.fin@example.com",
        designation="Risk Analyst",
        department_name="Finance",
    )

    # Attempt to update Kiran Rao by name -> must ask clarifying question with candidate IDs
    step1 = workflow.handle_message(
        "Update Kiran Rao's designation to Staff Analyst.",
        conversation_id=conv_id,
    )
    assert step1["status"] == "awaiting_clarification"
    assert "Multiple employees share the name 'Kiran Rao'" in step1["final_response"]
    assert str(emp2["id"]) in step1["final_response"]

    # User replies with the specific employee ID to complete the pending update
    step2 = workflow.handle_message(f"ID {emp2['id']}", conversation_id=conv_id)
    assert step2["status"] == "completed"
    assert workflow.service.get_employee(emp2["id"])["designation"] == "Staff Analyst"


def test_destructive_operation_confirmation_and_cancellation(workflow: EmployeeAgentWorkflow) -> None:
    conv_id = "int-delete-flow"

    # 1. Request deletion of employee 105 -> must stage confirmation and NOT delete yet
    req = workflow.handle_message("Delete employee 105.", conversation_id=conv_id)
    assert req["routed_agent"] == "delete_agent"
    assert req["status"] == "awaiting_confirmation"
    assert "Confirmation required" in req["final_response"]
    assert workflow.service.get_employee(105)["name"] == "Vikram Singh"

    # 2. Cancel the pending deletion -> record remains intact
    cancel = workflow.handle_message("No, cancel", conversation_id=conv_id)
    assert cancel["status"] == "cancelled"
    assert "Cancelled pending deletion" in cancel["final_response"]
    assert workflow.service.get_employee(105)["name"] == "Vikram Singh"

    # 3. Request deletion again and explicitly confirm
    req2 = workflow.handle_message("Delete employee 105.", conversation_id=conv_id)
    assert req2["status"] == "awaiting_confirmation"
    confirm = workflow.handle_message("Yes, confirm", conversation_id=conv_id)
    assert confirm["routed_agent"] == "delete_agent"
    assert confirm["status"] == "completed"
    assert "permanently deleted employee Vikram Singh" in confirm["final_response"]
    assert len(workflow.service.list_employees()) == 4

    # 4. Department deletion confirmation for empty department (Design, ID 4)
    dept_req = workflow.handle_message("Delete the Design department.", conversation_id=conv_id)
    assert dept_req["status"] == "awaiting_confirmation"
    assert "Design" in dept_req["final_response"]

    dept_confirm = workflow.handle_message("yes", conversation_id=conv_id)
    assert dept_confirm["status"] == "completed"
    assert "deleted department 'Design'" in dept_confirm["final_response"]
    assert len(workflow.service.list_departments()) == 3


def test_mock_llm_client_structured_routing(seeded_db_path: Path, tmp_path: Path) -> None:
    """Verify that a mocked LLM returning SupervisorDecision objects integrates cleanly."""
    def mock_llm(message, context, pending_conf):
        return SupervisorDecision(
            operation_intent="read",
            target_entity="employee",
            routed_agent="read_agent",
            structured_arguments={"employee_id": 104, "single_lookup": True},
        )

    cfg = AppConfig(
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_api_key="sk-mocktestkey1234567890",
        database_path=seeded_db_path,
        log_file_path=tmp_path / "mock_llm.log",
    )
    wf = EmployeeAgentWorkflow(config=cfg, llm_client=mock_llm)
    state = wf.handle_message("Can you pull up Ananya's record?", conversation_id="mock-llm-conv")
    assert state["status"] == "completed"
    assert "Ananya Iyer" in state["final_response"]
