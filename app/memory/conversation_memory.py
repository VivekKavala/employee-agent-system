"""Persistent SQLite-backed conversation memory, entity reference tracking, and pending confirmation state."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.database.connection import get_db_connection, initialize_database, utc_now_iso
from app.logging.setup import scrub_sensitive_text
from app.services.validation import ConfirmationRequiredError


@dataclass
class PendingConfirmation:
    """Represents a pending destructive operation awaiting explicit user confirmation."""

    confirmation_id: str
    conversation_id: str
    operation: str
    target_entity: str
    target_id: int
    target_summary: str
    payload: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PendingConfirmation":
        return cls(
            confirmation_id=str(data["confirmation_id"]),
            conversation_id=str(data["conversation_id"]),
            operation=str(data["operation"]),
            target_entity=str(data["target_entity"]),
            target_id=int(data["target_id"]),
            target_summary=str(data["target_summary"]),
            payload=dict(data.get("payload") or {}),
            status=str(data.get("status", "pending")),
            created_at=str(data.get("created_at", "")),
        )


@dataclass
class ConversationContextSnapshot:
    """Lightweight conversational entity context for pronoun and follow-up resolution."""

    conversation_id: str
    last_employee_id: Optional[int] = None
    last_employee_name: Optional[str] = None
    last_department_id: Optional[int] = None
    last_department_name: Optional[str] = None
    pending_clarification: Optional[Dict[str, Any]] = None
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConversationMemoryStore:
    """SQLite-backed store for conversation turns, entity context, and destructive confirmation state."""

    def __init__(self, db_path: Union[str, Path]) -> None:
        self.db_path = Path(db_path)
        initialize_database(self.db_path)

    # -------------------------------------------------------------------------
    # Message Turn Persistence
    # -------------------------------------------------------------------------

    def append_turn(
        self,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist a single user or assistant message with secret scrubbing."""
        now = utc_now_iso()
        safe_content = scrub_sensitive_text(content)
        safe_meta = None
        if metadata:
            # Strip any accidental secret keys
            filtered = {
                k: v
                for k, v in metadata.items()
                if "key" not in k.lower() and "secret" not in k.lower() and "token" not in k.lower()
            }
            safe_meta = json.dumps(filtered, ensure_ascii=False)

        with get_db_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO conversation_turns (conversation_id, role, content, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, role, safe_content, safe_meta, now),
            )

    def get_recent_turns(self, conversation_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieve the most recent turns for `conversation_id` in chronological order."""
        with get_db_connection(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, role, content, metadata_json, created_at
                FROM conversation_turns
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
        turns: List[Dict[str, Any]] = []
        for r in reversed(rows):
            item = dict(r)
            item["metadata"] = json.loads(item["metadata_json"]) if item.get("metadata_json") else {}
            turns.append(item)
        return turns

    # -------------------------------------------------------------------------
    # Entity Context & Pending Clarification Tracking
    # -------------------------------------------------------------------------

    def get_context(self, conversation_id: str) -> ConversationContextSnapshot:
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM conversation_context WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if not row:
                return ConversationContextSnapshot(conversation_id=conversation_id)
            clarification = (
                json.loads(row["pending_clarification_json"])
                if row["pending_clarification_json"]
                else None
            )
            return ConversationContextSnapshot(
                conversation_id=row["conversation_id"],
                last_employee_id=row["last_employee_id"],
                last_employee_name=row["last_employee_name"],
                last_department_id=row["last_department_id"],
                last_department_name=row["last_department_name"],
                pending_clarification=clarification,
                updated_at=row["updated_at"],
            )

    def update_context(
        self,
        conversation_id: str,
        *,
        last_employee_id: Optional[int] = ...,  # type: ignore[assignment]
        last_employee_name: Optional[str] = ...,  # type: ignore[assignment]
        last_department_id: Optional[int] = ...,  # type: ignore[assignment]
        last_department_name: Optional[str] = ...,  # type: ignore[assignment]
        pending_clarification: Optional[Dict[str, Any]] = ...,  # type: ignore[assignment]
    ) -> ConversationContextSnapshot:
        current = self.get_context(conversation_id)
        emp_id = current.last_employee_id if last_employee_id is ... else last_employee_id
        emp_name = current.last_employee_name if last_employee_name is ... else last_employee_name
        dept_id = current.last_department_id if last_department_id is ... else last_department_id
        dept_name = current.last_department_name if last_department_name is ... else last_department_name
        clarif = current.pending_clarification if pending_clarification is ... else pending_clarification
        clarif_json = json.dumps(clarif, ensure_ascii=False) if clarif is not None else None
        now = utc_now_iso()

        with get_db_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO conversation_context (
                    conversation_id, last_employee_id, last_employee_name,
                    last_department_id, last_department_name, pending_clarification_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    last_employee_id = excluded.last_employee_id,
                    last_employee_name = excluded.last_employee_name,
                    last_department_id = excluded.last_department_id,
                    last_department_name = excluded.last_department_name,
                    pending_clarification_json = excluded.pending_clarification_json,
                    updated_at = excluded.updated_at
                """,
                (
                    conversation_id,
                    emp_id,
                    emp_name,
                    dept_id,
                    dept_name,
                    clarif_json,
                    now,
                ),
            )
        return self.get_context(conversation_id)

    def clear_employee_from_context_if_matches(self, conversation_id: str, employee_id: int) -> None:
        ctx = self.get_context(conversation_id)
        if ctx.last_employee_id == employee_id:
            self.update_context(conversation_id, last_employee_id=None, last_employee_name=None)

    # -------------------------------------------------------------------------
    # Destructive Operation Confirmation State
    # -------------------------------------------------------------------------

    def create_pending_confirmation(
        self,
        *,
        conversation_id: str,
        operation: str,
        target_entity: str,
        target_id: int,
        target_summary: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> PendingConfirmation:
        """Create or replace the active pending confirmation for `conversation_id`."""
        now = utc_now_iso()
        short_token = uuid.uuid4().hex[:8]
        conf_id = f"conf-{target_entity[:3]}-{target_id}-{short_token}"
        payload_dict = dict(payload or {})
        payload_json = json.dumps(payload_dict, ensure_ascii=False)

        with get_db_connection(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO pending_confirmations (
                    confirmation_id, conversation_id, operation, target_entity,
                    target_id, target_summary, payload_json, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    confirmation_id = excluded.confirmation_id,
                    operation = excluded.operation,
                    target_entity = excluded.target_entity,
                    target_id = excluded.target_id,
                    target_summary = excluded.target_summary,
                    payload_json = excluded.payload_json,
                    status = 'pending',
                    created_at = excluded.created_at
                """,
                (
                    conf_id,
                    conversation_id,
                    operation,
                    target_entity,
                    int(target_id),
                    target_summary,
                    payload_json,
                    now,
                ),
            )

        return PendingConfirmation(
            confirmation_id=conf_id,
            conversation_id=conversation_id,
            operation=operation,
            target_entity=target_entity,
            target_id=int(target_id),
            target_summary=target_summary,
            payload=payload_dict,
            status="pending",
            created_at=now,
        )

    def get_pending_confirmation(self, conversation_id: str) -> Optional[PendingConfirmation]:
        """Return the currently active pending confirmation for `conversation_id`, or None."""
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM pending_confirmations
                WHERE conversation_id = ? AND status = 'pending'
                """,
                (conversation_id,),
            ).fetchone()
            if not row:
                return None
            return PendingConfirmation(
                confirmation_id=row["confirmation_id"],
                conversation_id=row["conversation_id"],
                operation=row["operation"],
                target_entity=row["target_entity"],
                target_id=int(row["target_id"]),
                target_summary=row["target_summary"],
                payload=json.loads(row["payload_json"]) if row["payload_json"] else {},
                status=row["status"],
                created_at=row["created_at"],
            )

    def consume_pending_confirmation(
        self,
        conversation_id: str,
        *,
        expected_confirmation_id: Optional[str] = None,
        expected_target_entity: Optional[str] = None,
        expected_target_id: Optional[int] = None,
    ) -> PendingConfirmation:
        """Validate and consume a pending confirmation atomically.

        Raises ConfirmationRequiredError if no pending confirmation exists or if parameters mismatch.
        """
        pending = self.get_pending_confirmation(conversation_id)
        if not pending:
            raise ConfirmationRequiredError(
                "No pending deletion operation exists for this conversation. "
                "Please initiate a delete request before confirming."
            )

        if expected_confirmation_id and pending.confirmation_id != expected_confirmation_id:
            raise ConfirmationRequiredError(
                f"Confirmation ID mismatch: expected '{pending.confirmation_id}', got '{expected_confirmation_id}'. "
                "Stale or mismatched confirmations are not accepted."
            )

        if expected_target_entity and pending.target_entity != expected_target_entity:
            raise ConfirmationRequiredError(
                f"Pending confirmation targets {pending.target_entity} (ID {pending.target_id}), "
                f"not {expected_target_entity}. Please confirm or cancel the pending operation first."
            )

        if expected_target_id is not None and int(pending.target_id) != int(expected_target_id):
            raise ConfirmationRequiredError(
                f"Pending confirmation is bound to {pending.target_entity} ID {pending.target_id}, "
                f"not ID {expected_target_id}. Cannot authorize a different target record."
            )

        with get_db_connection(self.db_path) as conn:
            conn.execute(
                "DELETE FROM pending_confirmations WHERE conversation_id = ?",
                (conversation_id,),
            )

        pending.status = "confirmed"
        return pending

    def cancel_pending_confirmation(self, conversation_id: str) -> Optional[PendingConfirmation]:
        """Cancel and remove any active pending confirmation for `conversation_id`."""
        pending = self.get_pending_confirmation(conversation_id)
        if not pending:
            return None
        with get_db_connection(self.db_path) as conn:
            conn.execute(
                "DELETE FROM pending_confirmations WHERE conversation_id = ?",
                (conversation_id,),
            )
        pending.status = "cancelled"
        return pending
