"""Unit tests for conversation memory persistence and pending confirmation safeguards."""
from __future__ import annotations

import pytest

from app.memory.conversation_memory import ConversationMemoryStore
from app.services.validation import ConfirmationRequiredError


def test_pending_confirmation_lifecycle(memory_store: ConversationMemoryStore) -> None:
    conv_id = "conv-del-test"
    assert memory_store.get_pending_confirmation(conv_id) is None

    pending = memory_store.create_pending_confirmation(
        conversation_id=conv_id,
        operation="delete_employee",
        target_entity="employee",
        target_id=105,
        target_summary="Vikram Singh (ID 105, Financial Analyst in Finance)",
        payload={"employee_id": 105},
    )
    assert pending.confirmation_id.startswith("conf-emp-105-")
    fetched = memory_store.get_pending_confirmation(conv_id)
    assert fetched is not None
    assert fetched.target_id == 105

    # Mismatched target_id must fail without consuming the pending confirmation
    with pytest.raises(ConfirmationRequiredError, match="bound to employee ID 105, not ID 102"):
        memory_store.consume_pending_confirmation(
            conv_id,
            expected_target_entity="employee",
            expected_target_id=102,
        )

    # Mismatched confirmation_id must fail
    with pytest.raises(ConfirmationRequiredError, match="Confirmation ID mismatch"):
        memory_store.consume_pending_confirmation(
            conv_id,
            expected_confirmation_id="conf-emp-105-stale000",
        )

    # Valid confirmation consumes and clears the pending operation
    consumed = memory_store.consume_pending_confirmation(
        conv_id,
        expected_confirmation_id=pending.confirmation_id,
        expected_target_entity="employee",
        expected_target_id=105,
    )
    assert consumed.status == "confirmed"
    assert memory_store.get_pending_confirmation(conv_id) is None

    # Re-using a consumed (stale) confirmation must fail
    with pytest.raises(ConfirmationRequiredError, match="No pending deletion operation"):
        memory_store.consume_pending_confirmation(conv_id)


def test_cancel_pending_confirmation(memory_store: ConversationMemoryStore) -> None:
    conv_id = "conv-cancel-test"
    memory_store.create_pending_confirmation(
        conversation_id=conv_id,
        operation="delete_department",
        target_entity="department",
        target_id=4,
        target_summary="Design (ID 4)",
    )
    cancelled = memory_store.cancel_pending_confirmation(conv_id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"
    assert memory_store.get_pending_confirmation(conv_id) is None


def test_conversation_turns_and_context_persistence_scrubs_secrets(memory_store: ConversationMemoryStore) -> None:
    conv_id = "conv-context-test"
    memory_store.append_turn(
        conv_id,
        "user",
        "My secret key is sk-1234567890abcdef12345678 and find employee 102",
        metadata={"api_key": "should-not-be-saved", "intent": "read"},
    )
    turns = memory_store.get_recent_turns(conv_id)
    assert len(turns) == 1
    assert "sk-1234567890abcdef12345678" not in turns[0]["content"]
    assert "[REDACTED_SECRET]" in turns[0]["content"]
    assert "api_key" not in turns[0]["metadata"]
    assert turns[0]["metadata"]["intent"] == "read"

    ctx = memory_store.update_context(
        conv_id,
        last_employee_id=102,
        last_employee_name="Priya Nair",
        last_department_id=1,
        last_department_name="Engineering",
    )
    assert ctx.last_employee_id == 102
    assert ctx.last_employee_name == "Priya Nair"
