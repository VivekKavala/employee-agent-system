"""Unit tests for SQLite initialization, foreign-key enforcement, seeding, and transactions."""
from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from app.config import AppConfig, ConfigurationError, load_config
from app.database.connection import (
    create_connection,
    get_db_connection,
    initialize_database,
    seed_demo_data,
)
from app.database.repositories import DepartmentRepository, EmployeeRepository


def test_foreign_keys_enabled_on_every_connection(temp_db_path: Path) -> None:
    conn = create_connection(temp_db_path)
    try:
        fk_status = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk_status == 1
    finally:
        conn.close()


def test_foreign_key_constraint_rejects_invalid_department_id(temp_db_path: Path) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        with get_db_connection(temp_db_path) as conn:
            conn.execute(
                """
                INSERT INTO employees (name, email, designation, department_id, created_at, updated_at)
                VALUES ('Ghost User', 'ghost@example.com', 'Engineer', 9999, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
                """
            )


def test_transaction_rollback_on_error(temp_db_path: Path) -> None:
    dept_repo = DepartmentRepository(temp_db_path)
    with pytest.raises(RuntimeError):
        with get_db_connection(temp_db_path) as conn:
            conn.execute(
                "INSERT INTO departments (name, description, created_at) VALUES (?, ?, ?)",
                ("RollbackDept", "Should be rolled back", "2026-01-01T00:00:00Z"),
            )
            raise RuntimeError("Simulated mid-transaction failure")

    assert dept_repo.get_by_name("RollbackDept") is None


def test_seed_demo_data_is_idempotent_and_never_overwrites(temp_db_path: Path) -> None:
    first_seeded = seed_demo_data(temp_db_path)
    assert first_seeded is True

    dept_repo = DepartmentRepository(temp_db_path)
    emp_repo = EmployeeRepository(temp_db_path)
    assert len(dept_repo.list_all()) == 4
    assert len(emp_repo.list_all()) == 5

    # Modify a record and verify a second seed call does NOT overwrite or duplicate
    emp_repo.update(102, designation="Principal Architect")
    second_seeded = seed_demo_data(temp_db_path)
    assert second_seeded is False
    updated = emp_repo.get_by_id(102)
    assert updated is not None
    assert updated.designation == "Principal Architect"


def test_config_validation_fails_with_clear_error_when_api_key_missing() -> None:
    cfg = AppConfig(
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_api_key=None,
    )
    with pytest.raises(ConfigurationError, match="Missing or placeholder LLM_API_KEY"):
        cfg.validate_for_live_llm()

    cfg_placeholder = AppConfig(
        llm_provider="groq",
        llm_model="llama-3.3-70b-versatile",
        llm_api_key="your-api-key-here",
    )
    with pytest.raises(ConfigurationError, match="Missing or placeholder LLM_API_KEY"):
        cfg_placeholder.validate_for_live_llm()

    cfg_bad_provider = AppConfig(
        llm_provider="unknown_vendor",
        llm_model="model-x",
        llm_api_key="sk-validlookingkey123456",
    )
    with pytest.raises(ConfigurationError, match="Unsupported LLM_PROVIDER"):
        cfg_bad_provider.validate_for_live_llm()
