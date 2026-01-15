import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict

from app import db
from app.services.router import route_state_vector
from app.services.vectorizer import vectorize_email, vectorize_message

logger = logging.getLogger(__name__)

def _map_lifecycle(lifecycle: str | None) -> str:
    if not lifecycle:
        return "NEEDS_REPLY"
    lifecycle_lower = lifecycle.lower()
    if lifecycle_lower == "new":
        return "NEEDS_REPLY"
    if lifecycle_lower in {"triaged", "pending_action"}:
        return "WAITING"
    if lifecycle_lower == "resolved":
        return "RESOLVED"
    return "NEEDS_REPLY"


async def ingest_message(nylas_message: Dict[str, Any]) -> Dict[str, Any]:
    message_id = nylas_message.get("id") or str(uuid.uuid4())
    grant_id = nylas_message.get("grant_id") or "manual"
    subject = nylas_message.get("subject") or ""
    body = nylas_message.get("body") or nylas_message.get("snippet") or ""
    sender = nylas_message.get("from") or ""

    raw_text = f"{subject}\n{body}".strip()
    analysis = None
    for attempt in range(3):
        try:
            analysis = await vectorize_message(raw_text)
            if analysis:
                break
        except Exception as exc:
            logger.error("Vectorize attempt %s failed: %s", attempt + 1, exc)
            await asyncio.sleep(0.5 * (attempt + 1))
    if analysis and analysis.get("vector"):
        vector_raw = analysis["vector"]
        intent_label = str(vector_raw.get("intent") or "OTHER").upper()
        current_owner_role = vector_raw.get("owner")
        deadline_at = vector_raw.get("deadline")
        lifecycle_state = _map_lifecycle(vector_raw.get("lifecycle"))
        summary = analysis.get("summary")
        risk_score = float(analysis.get("risk_score") or 0.2)
        context_blob = {
            "vector": vector_raw,
            "subject": subject,
            "snippet": body,
            "sender": sender,
        }
        vector = {
            "intent_label": intent_label,
            "risk_score": risk_score,
            "summary": summary,
            "context_blob": context_blob,
            "current_owner_role": current_owner_role,
            "deadline_at": deadline_at,
            "lifecycle_state": lifecycle_state,
        }
    else:
        vector = vectorize_email(raw_text)

    routing = route_state_vector(vector)

    now = datetime.utcnow().isoformat()
    context_blob = vector.get("context_blob") or {}
    if not isinstance(context_blob, dict):
        context_blob = {"raw_context": context_blob}
    context_blob.setdefault("subject", subject)
    context_blob.setdefault("snippet", body)
    context_blob.setdefault("sender", sender)
    if vector.get("raw_vector"):
        context_blob.setdefault("vector", vector.get("raw_vector"))

    record_id = str(uuid.uuid4())
    record = {
        "id": record_id,
        "nylas_message_id": message_id,
        "grant_id": grant_id,
        "intent_label": vector.get("intent_label"),
        "risk_score": float(vector.get("risk_score", 0.0)),
        "context_blob": context_blob,
        "summary": vector.get("summary"),
        "current_owner_role": vector.get("current_owner_role") or routing.get("current_owner_role"),
        "deadline_at": vector.get("deadline_at"),
        "lifecycle_state": vector.get("lifecycle_state", "NEEDS_REPLY"),
        "is_overdue": 0,
        "created_at": now,
        "updated_at": now,
        "source_sender": sender,
        "source_subject": subject,
    }

    insert_error = None
    for attempt in range(3):
        try:
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                db.p(
                    """
                    INSERT INTO message_state_vectors (
                        id, nylas_message_id, grant_id, intent_label, risk_score, context_blob, summary,
                        current_owner_role, deadline_at, lifecycle_state, is_overdue, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                ),
                (
                    record["id"],
                    record["nylas_message_id"],
                    record["grant_id"],
                    record["intent_label"],
                    record["risk_score"],
                    json.dumps(record["context_blob"]),
                    record["summary"],
                    record["current_owner_role"],
                    record["deadline_at"],
                    record["lifecycle_state"],
                    record["is_overdue"],
                    record["created_at"],
                    record["updated_at"],
                ),
            )
            conn.commit()
            db.release_connection(conn)
            insert_error = None
            break
        except Exception as exc:
            insert_error = exc
            logger.error("State vector insert attempt %s failed: %s", attempt + 1, exc)
            await asyncio.sleep(0.5 * (attempt + 1))
        finally:
            try:
                db.release_connection(conn)
            except Exception:
                pass
    if insert_error:
        raise insert_error

    print("State Vector Created")
    return record


def ingest_message_sync(nylas_message: Dict[str, Any]) -> Dict[str, Any]:
    return asyncio.run(ingest_message(nylas_message))
