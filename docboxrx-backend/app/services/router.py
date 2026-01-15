from typing import Any, Dict


def route_state_vector(vector: Dict[str, Any]) -> Dict[str, Any]:
    intent = str(vector.get("intent_label", "OTHER")).upper()
    try:
        risk_score = float(vector.get("risk_score", 0.0))
    except (TypeError, ValueError):
        risk_score = 0.0

    if intent == "CLINICAL" and risk_score >= 0.8:
        return {"current_owner_role": "lead_doctor", "routing_reason": "High-risk clinical"}
    if intent == "CLINICAL":
        return {"current_owner_role": "nurse", "routing_reason": "Clinical, non-urgent"}
    if intent == "BILLING":
        return {"current_owner_role": "billing", "routing_reason": "Billing-related"}
    if intent == "ADMIN":
        return {"current_owner_role": "front_desk", "routing_reason": "Administrative"}
    return {"current_owner_role": "front_desk", "routing_reason": "Default routing"}
