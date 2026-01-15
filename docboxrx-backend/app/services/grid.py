import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app import db
from app.services.zones import zone_for_message


def _parse_deadline(deadline_at: str | None) -> datetime | None:
    if not deadline_at:
        return None
    try:
        if deadline_at.endswith("Z"):
            return datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
        return datetime.fromisoformat(deadline_at)
    except ValueError:
        return None


def _coerce_context_blob(raw_blob: Any) -> Dict[str, Any]:
    if raw_blob is None:
        return {}
    if isinstance(raw_blob, dict):
        return raw_blob
    if isinstance(raw_blob, str):
        try:
            return json.loads(raw_blob)
        except json.JSONDecodeError:
            return {"raw": raw_blob}
    return {"raw": raw_blob}


def get_triage_grid(owner_id: Optional[str] = None, preview_limit: int = 8) -> Dict[str, Any]:
    conn = db.get_connection()
    cursor = conn.cursor()
    states = ("NEEDS_REPLY", "WAITING", "OVERDUE")

    base_query = """
        SELECT id, nylas_message_id, grant_id, intent_label, risk_score, context_blob, summary,
               current_owner_role, deadline_at, lifecycle_state, is_overdue, created_at, updated_at
        FROM message_state_vectors
        WHERE lifecycle_state IN (?, ?, ?)
    """
    params: List[Any] = [states[0], states[1], states[2]]

    if owner_id:
        base_query += " AND (current_owner_role = ? OR grant_id = ?)"
        params.extend([owner_id, owner_id])

    cursor.execute(db.p(base_query), params)
    rows = cursor.fetchall()
    db.release_connection(conn)

    now = datetime.now(timezone.utc)
    zones = {
        "STAT": {"zone": "STAT", "total_count": 0, "overdue_count": 0, "items": []},
        "TODAY": {"zone": "TODAY", "total_count": 0, "overdue_count": 0, "items": []},
        "THIS_WEEK": {"zone": "THIS_WEEK", "total_count": 0, "overdue_count": 0, "items": []},
        "LATER": {"zone": "LATER", "total_count": 0, "overdue_count": 0, "items": []},
    }

    for row in rows:
        msg = dict(row)
        zone = zone_for_message(msg)
        deadline_at = msg.get("deadline_at")
        deadline_dt = _parse_deadline(deadline_at)
        if deadline_dt and deadline_dt.tzinfo is None:
            deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)

        overdue = msg.get("lifecycle_state") == "OVERDUE"
        if deadline_dt and deadline_dt < now:
            overdue = True

        context_blob = _coerce_context_blob(msg.get("context_blob"))
        item = {
            "id": msg.get("id"),
            "subject": context_blob.get("subject") or msg.get("summary") or "No subject",
            "snippet": context_blob.get("snippet") or context_blob.get("body") or "",
            "risk_score": float(msg.get("risk_score") or 0.0),
            "lifecycle_state": msg.get("lifecycle_state"),
            "deadline_at": deadline_at,
            "patient_name": context_blob.get("patient_name"),
            "overdue": overdue,
        }

        zones[zone]["total_count"] += 1
        if overdue:
            zones[zone]["overdue_count"] += 1
        zones[zone]["items"].append(item)

    for zone_data in zones.values():
        zone_data["items"].sort(
            key=lambda i: (
                0 if i.get("overdue") else 1,
                -float(i.get("risk_score") or 0.0),
                _parse_deadline(i.get("deadline_at")) or datetime.max.replace(tzinfo=timezone.utc),
            )
        )
        zone_data["items"] = zone_data["items"][:preview_limit]
        for item in zone_data["items"]:
            item.pop("overdue", None)

    return {"zones": list(zones.values())}
