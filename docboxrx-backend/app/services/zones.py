from datetime import datetime, timezone
from typing import Any, Dict


def _parse_deadline(deadline_at: str | None) -> datetime | None:
    if not deadline_at:
        return None
    try:
        if deadline_at.endswith("Z"):
            return datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
        return datetime.fromisoformat(deadline_at)
    except ValueError:
        return None


def zone_for_message(msg: Dict[str, Any]) -> str:
    try:
        risk_score = float(msg.get("risk_score", 0.0))
    except (TypeError, ValueError):
        risk_score = 0.0

    if risk_score >= 0.8:
        return "STAT"

    deadline_at = _parse_deadline(msg.get("deadline_at"))
    if deadline_at:
        now = datetime.now(timezone.utc)
        if deadline_at.tzinfo is None:
            deadline_at = deadline_at.replace(tzinfo=timezone.utc)
        hours_until_deadline = (deadline_at - now).total_seconds() / 3600
        if hours_until_deadline <= 24:
            return "TODAY"
        if hours_until_deadline <= 72:
            return "THIS_WEEK"
        return "LATER"

    return "LATER"
