import json
import logging
import os
from typing import Any, Dict, List

from cerebras.cloud.sdk import Cerebras

from app.services.prompts import VECTORIZER_SYSTEM_PROMPT

CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
cerebras_client = Cerebras(api_key=CEREBRAS_API_KEY) if CEREBRAS_API_KEY else None
logger = logging.getLogger(__name__)

VALID_INTENTS = {"CLINICAL", "ADMIN", "BILLING", "OTHER"}
RISK_TO_SCORE = {
    "low": 0.2,
    "medium": 0.5,
    "high": 0.8,
    "critical": 0.95,
}
LIFECYCLE_MAP = {
    "new": "NEEDS_REPLY",
    "triaged": "WAITING",
    "pending_action": "WAITING",
    "resolved": "RESOLVED",
}


def _extract_json(text: str) -> str:
    if "```json" in text:
        return text.split("```json")[1].split("```")[0].strip()
    if "```" in text:
        return text.split("```")[1].split("```")[0].strip()
    return text.strip()


def _heuristic_vectorize(text: str) -> Dict[str, Any]:
    lower = text.lower()
    clinical_keywords = [
        "pain",
        "bleeding",
        "hurt",
        "tooth",
        "fever",
        "infection",
        "swelling",
        "urgent",
        "emergency",
        "critical",
    ]
    billing_keywords = ["billing", "invoice", "payment", "claim", "copay", "balance"]
    admin_keywords = ["appointment", "schedule", "refill", "referral", "prior auth"]

    matches: List[str] = []
    for keyword in clinical_keywords:
        if keyword in lower:
            matches.append(keyword)

    if matches:
        return {
            "intent_label": "CLINICAL",
            "risk_score": 0.9,
            "summary": "Likely clinical issue requiring attention.",
            "context_blob": {"heuristic": True, "matches": matches},
        }
    for keyword in billing_keywords:
        if keyword in lower:
            return {
                "intent_label": "BILLING",
                "risk_score": 0.3,
                "summary": "Likely billing-related request.",
                "context_blob": {"heuristic": True, "matches": [keyword]},
            }
    for keyword in admin_keywords:
        if keyword in lower:
            return {
                "intent_label": "ADMIN",
                "risk_score": 0.4,
                "summary": "Likely administrative request.",
                "context_blob": {"heuristic": True, "matches": [keyword]},
            }
    return {
        "intent_label": "OTHER",
        "risk_score": 0.2,
        "summary": "Unclear intent.",
        "context_blob": {"heuristic": True, "matches": []},
    }


def _coerce_vector_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    intent = payload.get("intent") or payload.get("intent_label") or "OTHER"
    owner = payload.get("owner") or payload.get("current_owner_role")
    deadline = payload.get("deadline") or payload.get("deadline_at")
    risk = payload.get("risk") or payload.get("risk_score") or "low"
    context = payload.get("context") or payload.get("summary")
    lifecycle = payload.get("lifecycle") or payload.get("lifecycle_state") or "new"

    intent = str(intent).upper()
    if intent not in VALID_INTENTS:
        intent = "OTHER"

    if isinstance(risk, str):
        risk_score = RISK_TO_SCORE.get(risk.lower().strip(), 0.2)
    else:
        risk_score = float(risk or 0.2)
    risk_score = max(0.0, min(1.0, risk_score))

    lifecycle_state = LIFECYCLE_MAP.get(str(lifecycle).lower().strip(), "NEEDS_REPLY")

    context_blob = payload.get("context_blob")
    if not isinstance(context_blob, dict):
        context_blob = {}

    return {
        "intent_label": intent,
        "risk_score": risk_score,
        "summary": context,
        "context_blob": context_blob,
        "current_owner_role": owner,
        "deadline_at": deadline,
        "lifecycle_state": lifecycle_state,
        "raw_vector": payload,
    }


def _risk_to_score(risk: str) -> float:
    return RISK_TO_SCORE.get(risk.lower(), 0.2)


async def vectorize_message(raw_content: str) -> Dict[str, Any] | None:
    if not raw_content or not cerebras_client:
        return None
    try:
        try:
            response = cerebras_client.chat.completions.create(
                model="llama-3.3-70b",
                messages=[
                    {
                        "role": "system",
                        "content": "Output ONLY valid JSON with keys: intent, owner, deadline, risk, context, lifecycle",
                    },
                    {"role": "user", "content": raw_content},
                ],
                response_format={"type": "json_object"},
            )
        except Exception:
            response = cerebras_client.chat.completions.create(
                model="llama-3.3-70b",
                messages=[
                    {
                        "role": "system",
                        "content": "Output ONLY valid JSON with keys: intent, owner, deadline, risk, context, lifecycle",
                    },
                    {"role": "user", "content": raw_content},
                ],
            )
        vector = json.loads(response.choices[0].message.content)
        return {
            "vector": vector,
            "risk_score": _risk_to_score(str(vector.get("risk", "low"))),
            "summary": str(vector.get("context", ""))[:280],
        }
    except Exception as exc:
        logger.error("Cerebras failure: %s", exc)
        return None


def vectorize_email(raw_text: str) -> Dict[str, Any]:
    if not raw_text:
        return _heuristic_vectorize("")

    if not cerebras_client:
        return _heuristic_vectorize(raw_text)

    try:
        try:
            response = cerebras_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": VECTORIZER_SYSTEM_PROMPT},
                    {"role": "user", "content": raw_text},
                ],
                model="llama-3.3-70b",
                max_tokens=300,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
        except Exception:
            response = cerebras_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": VECTORIZER_SYSTEM_PROMPT},
                    {"role": "user", "content": raw_text},
                ],
                model="llama-3.3-70b",
                max_tokens=300,
                temperature=0.2,
            )
        result_text = response.choices[0].message.content.strip()
        payload = json.loads(_extract_json(result_text))
        vector = _coerce_vector_payload(payload)
        high_risk_keywords = ["bleeding", "emergency", "severe pain", "swelling", "chest pain"]
        if vector["intent_label"] == "CLINICAL":
            lowered = raw_text.lower()
            if any(keyword in lowered for keyword in high_risk_keywords) and vector["risk_score"] < 0.85:
                vector["risk_score"] = 0.85
        return vector
    except Exception:
        return _heuristic_vectorize(raw_text)
