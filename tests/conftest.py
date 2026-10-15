"""Shared pytest fixtures using temporary SQLite databases (never mutating demo DB)."""
from __future__ import annotations

from pathlib import Path
import pytest

from app.database.connection import initialize_database, seed_demo_data
from app.memory.conversation_memory import ConversationMemoryStore
from app.services.employee_service import EmployeeInformationService


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Return an initialized, empty temporary SQLite database path."""
    db_file = tmp_path / "test_employee_system.db"
    initialize_database(db_file)
    return db_file


@pytest.fixture
def seeded_db_path(tmp_path: Path) -> Path:
    """Return a temporary SQLite database seeded with the standard demo dataset."""
    db_file = tmp_path / "seeded_employee_system.db"
    initialize_database(db_file)
    seed_demo_data(db_file)
    return db_file


@pytest.fixture
def employee_service(seeded_db_path: Path) -> EmployeeInformationService:
    """Return an EmployeeInformationService connected to a seeded temporary SQLite DB."""
    return EmployeeInformationService(seeded_db_path)


@pytest.fixture
def memory_store(seeded_db_path: Path) -> ConversationMemoryStore:
    """Return a ConversationMemoryStore connected to the temporary SQLite DB."""
    return ConversationMemoryStore(seeded_db_path)
