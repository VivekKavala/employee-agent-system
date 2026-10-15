"""Evaluation runner executing evals/dataset.json against isolated SQLite databases
and reporting routing accuracy, argument correctness, task success rate, and guardrail pass rate.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import AppConfig
from app.database.connection import initialize_database, seed_demo_data
from app.graph import EmployeeAgentWorkflow
from app.state import SupervisorDecision


def check_db_assertions(workflow: EmployeeAgentWorkflow, assertions: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if "employee_count" in assertions:
        actual = len(workflow.service.list_employees())
        expected = int(assertions["employee_count"])
        if actual != expected:
            errors.append(f"DB employee_count expected {expected}, got {actual}")

    if "total_departments" in assertions:
        actual = len(workflow.service.list_departments())
        expected = int(assertions["total_departments"])
        if actual != expected:
            errors.append(f"DB total_departments expected {expected}, got {actual}")

    if "department_exists" in assertions:
        dept_name = str(assertions["department_exists"])
        try:
            workflow.service.get_department(name=dept_name)
        except Exception as exc:
            errors.append(f"DB expected department '{dept_name}' to exist: {exc}")

    if "employee_102_designation" in assertions:
        emp = workflow.service.get_employee(102)
        if emp["designation"] != assertions["employee_102_designation"]:
            errors.append(
                f"DB employee 102 designation expected '{assertions['employee_102_designation']}', got '{emp['designation']}'"
            )

    if "employee_102_email" in assertions:
        emp = workflow.service.get_employee(102)
        if emp["email"] != assertions["employee_102_email"]:
            errors.append(
                f"DB employee 102 email expected '{assertions['employee_102_email']}', got '{emp['email']}'"
            )

    return errors


def evaluate_case(case: Dict[str, Any], provider: str = "deterministic") -> Dict[str, Any]:
    case_id = case["id"]
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        db_path = tmp_path / f"{case_id}.db"
        initialize_database(db_path)
        seed_demo_data(db_path)

        cfg = AppConfig(
            llm_provider=provider,
            llm_model="gpt-4o-mini",
            database_path=db_path,
            log_file_path=tmp_path / "eval.log",
        )

        llm_client = None
        if "simulated_compromised_decision" in case:
            comp = case["simulated_compromised_decision"]
            llm_client = lambda msg, ctx, pconf: SupervisorDecision(**comp)

        workflow = EmployeeAgentWorkflow(config=cfg, llm_client=llm_client)
        conv_id = f"eval-{case_id}"

        setup_turns = case.get("setup_turns") or case.get("setup_ turns") or []
        for s_msg in setup_turns:
            workflow.handle_message(s_msg, conversation_id=conv_id)

        state = workflow.handle_message(case["input"], conversation_id=conv_id)

        failures: List[str] = []

        # 1. Routing & Intent check
        actual_intent = state.get("operation_intent")
        actual_entity = state.get("target_entity")
        actual_agent = state.get("routed_agent")

        routing_ok = True
        if actual_intent != case["expected_intent"]:
            routing_ok = False
            failures.append(f"Intent mismatch: expected '{case['expected_intent']}', got '{actual_intent}'")
        if actual_entity != case["expected_entity"]:
            routing_ok = False
            failures.append(f"Entity mismatch: expected '{case['expected_entity']}', got '{actual_entity}'")
        if actual_agent != case["expected_agent"]:
            routing_ok = False
            failures.append(f"Routed agent mismatch: expected '{case['expected_agent']}', got '{actual_agent}'")

        # 2. Argument extraction check
        actual_args = state.get("structured_arguments") or {}
        expected_args = case.get("expected_args_subset") or {}
        args_ok = True
        for k, v in expected_args.items():
            if actual_args.get(k) != v:
                args_ok = False
                failures.append(f"Argument '{k}' mismatch: expected {v!r}, got {actual_args.get(k)!r}")

        # 3. Task status, response grounding, and DB outcome check
        task_ok = True
        actual_status = state.get("status")
        if actual_status != case["expected_status"]:
            task_ok = False
            failures.append(f"Status mismatch: expected '{case['expected_status']}', got '{actual_status}'")

        final_resp = state.get("final_response") or ""
        for sub in case.get("expected_response_substrings", []):
            if sub.lower() not in final_resp.lower():
                task_ok = False
                failures.append(f"Missing expected substring {sub!r} in final_response: {final_resp!r}")

        if "db_assertion" in case:
            db_errs = check_db_assertions(workflow, case["db_assertion"])
            if db_errs:
                task_ok = False
                failures.extend(db_errs)

        passed = routing_ok and args_ok and task_ok
        return {
            "id": case_id,
            "category": case.get("category", "general"),
            "passed": passed,
            "routing_ok": routing_ok,
            "args_ok": args_ok,
            "task_ok": task_ok,
            "is_guardrail_case": bool(case.get("is_guardrail_case", False)),
            "failures": failures,
        }


def run_evaluation_suite(dataset_path: Path, provider: str = "deterministic") -> int:
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    results: List[Dict[str, Any]] = []

    for case in cases:
        res = evaluate_case(case, provider=provider)
        results.append(res)

    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    failed_count = total - passed_count
    routing_acc = (sum(1 for r in results if r["routing_ok"]) / total * 100.0) if total else 0.0
    args_acc = (sum(1 for r in results if r["args_ok"]) / total * 100.0) if total else 0.0
    task_acc = (sum(1 for r in results if r["task_ok"]) / total * 100.0) if total else 0.0

    guardrail_cases = [r for r in results if r["is_guardrail_case"]]
    guardrail_pass_rate = (
        sum(1 for r in guardrail_cases if r["passed"]) / len(guardrail_cases) * 100.0
        if guardrail_cases
        else 100.0
    )

    print("====================================================================")
    print(" Employee Information Multi-Agent System — Evaluation Report")
    print("====================================================================")
    print(f" Dataset Path        : {dataset_path}")
    print(f" Total Cases         : {total}")
    print(f" Passed Cases        : {passed_count}")
    print(f" Failed Cases        : {failed_count}")
    print(f" Routing Accuracy    : {routing_acc:.1f}% ({sum(1 for r in results if r['routing_ok'])}/{total})")
    print(f" Argument Correctness: {args_acc:.1f}% ({sum(1 for r in results if r['args_ok'])}/{total})")
    print(f" Task Success Rate   : {task_acc:.1f}% ({sum(1 for r in results if r['task_ok'])}/{total})")
    print(
        f" Guardrail Pass Rate : {guardrail_pass_rate:.1f}% "
        f"({sum(1 for r in guardrail_cases if r['passed'])}/{len(guardrail_cases)})"
    )
    print("====================================================================")

    if failed_count > 0:
        print("\nFailure Details:")
        for r in results:
            if not r["passed"]:
                print(f" - [{r['id']}] ({r['category']}):")
                for f in r["failures"]:
                    print(f"     * {f}")
    else:
        print("\nAll evaluation cases passed with 0 failures.")

    return 0 if failed_count == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run evaluation suite for Employee Information Multi-Agent System.")
    parser.add_argument(
        "--dataset",
        type=str,
        default=str(Path(__file__).resolve().parent / "dataset.json"),
        help="Path to evaluation dataset JSON.",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="deterministic",
        help="LLM provider mode (default: deterministic).",
    )
    args = parser.parse_args()
    return run_evaluation_suite(Path(args.dataset), provider=args.provider)


if __name__ == "__main__":
    sys.exit(main())
