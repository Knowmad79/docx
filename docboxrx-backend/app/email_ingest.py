"""Production-ready email ingestion module for DocBoxRX."""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime
import asyncio
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# Pydantic model for incoming email webhook payload
class EmailPayload(BaseModel):
    from_email: EmailStr = Field(..., description="Sender email address")
    to_email: EmailStr = Field(..., description="Recipient email address")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body content")
    received_at: Optional[datetime] = Field(default=None, description="Email received timestamp")

# Import db module for database operations
from app import db

# Async vectorizer function using Cerebras
async def vectorize_email(subject: str, body: str) -> dict:
    """Vectorize email content using AI."""
    logger.info("Vectorizing email content...")
    
    # Try to use Cerebras for vectorization
    try:
        from cerebras.cloud.sdk import Cerebras
        import os
        import json
        
        api_key = os.environ.get("CEREBRAS_API_KEY", "")
        if api_key:
            client = Cerebras(api_key=api_key)
            
            prompt = f"""Analyze this email and return JSON:
Subject: {subject}
Body: {body[:1000]}

Return JSON with these fields:
- intent: (patient_inquiry, refill_request, lab_result, billing, marketing, other)
- risk: (critical, high, medium, low)
- deadline: (immediate, today, this_week, none)
- owner: suggested handler role
- context: brief context summary
- lifecycle: (new, needs_reply, waiting, resolved)
"""
            response = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b",
                max_tokens=300,
                temperature=0.2
            )
            
            result_text = response.choices[0].message.content.strip()
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()
            
            return json.loads(result_text)
    except Exception as e:
        logger.warning(f"AI vectorization failed: {e}")
    
    # Fallback to basic vectorization
    return {
        "intent": "other",
        "risk": "medium",
        "deadline": "this_week",
        "owner": "doctor",
        "context": subject[:100],
        "lifecycle": "new"
    }

@router.post("/api/messages/ingest/webhook", status_code=status.HTTP_201_CREATED)
async def ingest_email_webhook(payload: EmailPayload):
    """Ingest email from webhook (CloudMailin, SendGrid, etc.)"""
    try:
        logger.info(f"Received email from {payload.from_email} to {payload.to_email}")
        
        # Vectorize the email
        vector_data = await vectorize_email(payload.subject, payload.body)
        
        # Extract domain from sender
        sender_domain = payload.from_email.split("@")[1] if "@" in payload.from_email else "unknown"
        
        # Create message using existing db function
        import uuid
        message_id = str(uuid.uuid4())
        now = datetime.utcnow()
        
        message = {
            "id": message_id,
            "user_id": "webhook-user",
            "sender": payload.from_email,
            "sender_domain": sender_domain,
            "subject": payload.subject,
            "snippet": payload.body,
            "zone": "TODAY" if vector_data.get("risk") in ["critical", "high"] else "THIS_WEEK",
            "confidence": 0.85,
            "reason": f"AI Analysis: {vector_data.get('intent', 'unknown')}",
            "jone5_message": "Email received and processed!",
            "received_at": (payload.received_at or now).isoformat(),
            "classified_at": now.isoformat(),
            "corrected": False,
            "summary": vector_data.get("context"),
            "recommended_action": f"Handle as {vector_data.get('intent', 'general inquiry')}",
            "action_type": "review",
            "draft_reply": None
        }
        
        # Store in database
        db.create_cloudmailin_message(message)
        
        logger.info(f"Saved email with ID {message_id}")
        return {
            "status": "success", 
            "id": message_id,
            "vector": vector_data
        }
        
    except Exception as e:
        logger.error(f"Error ingesting email: {e}")
        raise HTTPException(status_code=500, detail=str(e))
