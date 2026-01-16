from fastapi import FastAPI, Depends, HTTPException, status, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Literal, List
from datetime import datetime, timedelta
import jwt
import bcrypt
import uuid
import re
import random
import os
import json
import hashlib
import email
import asyncio
import logging
from email import policy
from email.parser import BytesParser
from cerebras.cloud.sdk import Cerebras
from nylas import Client as NylasClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import database module
from app import db
from app.routers.grid import router as grid_router
from app.services.ingestion import ingest_message

# Nylas configuration
NYLAS_API_KEY = os.environ.get("NYLAS_API_KEY", "")
NYLAS_CLIENT_ID = os.environ.get("NYLAS_CLIENT_ID", "")
NYLAS_API_URI = os.environ.get("NYLAS_API_URI", "https://api.us.nylas.com")
NYLAS_CALLBACK_URI = os.environ.get("NYLAS_CALLBACK_URI", "http://45.61.59.218/api/nylas/callback")

nylas_client = NylasClient(api_key=NYLAS_API_KEY, api_uri=NYLAS_API_URI) if NYLAS_API_KEY else None

# Cerebras API for LLM fallback
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
cerebras_client = Cerebras(api_key=CEREBRAS_API_KEY) if CEREBRAS_API_KEY else None
LLM_CONFIDENCE_THRESHOLD = 0.70

app = FastAPI(title="DocBoxRX API", description="Sovereign Email Triage System")
app.include_router(grid_router)

# CORS - allow all for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
SECRET_KEY = os.environ.get("SECRET_KEY", "docboxrx-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

security = HTTPBearer()

ZoneType = Literal["STAT", "TODAY", "THIS_WEEK", "LATER"]

# ============== MODELS ==============

class EmailSource(BaseModel):
    id: str
    name: str
    inbound_token: str
    inbound_address: str
    created_at: str
    email_count: int = 0

class SourceCreate(BaseModel):
    name: str

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str
    practice_name: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str
    user: dict

# Production-ready email payload with proper validation
class EmailPayload(BaseModel):
    from_email: Optional[EmailStr] = Field(None, description="Sender email address")
    sender: Optional[str] = Field(None, description="Sender (legacy)")
    to_email: Optional[EmailStr] = Field(None, description="Recipient email address")
    subject: str = Field(..., description="Email subject")
    body: Optional[str] = Field(None, description="Email body content")
    body_plain: Optional[str] = Field(None, description="Email body plain text")
    snippet: Optional[str] = Field(None, description="Email snippet")
    received_at: Optional[datetime] = Field(None, description="Email received timestamp")
    sender_domain: Optional[str] = None
    source_id: Optional[str] = None
    grant_id: Optional[str] = None
    message_id: Optional[str] = None

class EmailIngest(BaseModel):
    sender: str
    sender_domain: Optional[str] = None
    subject: str
    snippet: Optional[str] = None
    source_id: Optional[str] = None
    grant_id: Optional[str] = None
    message_id: Optional[str] = None
    body_plain: Optional[str] = None

class MessageResponse(BaseModel):
    id: str
    sender: str
    sender_domain: str
    subject: str
    snippet: Optional[str]
    zone: ZoneType
    confidence: float
    reason: str
    jone5_message: str
    received_at: datetime
    classified_at: datetime
    corrected: bool = False
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    summary: Optional[str] = None
    recommended_action: Optional[str] = None
    action_type: Optional[str] = None
    draft_reply: Optional[str] = None

class ZoneCorrection(BaseModel):
    message_id: str
    new_zone: ZoneType
    reason: Optional[str] = None

class JonE5Response(BaseModel):
    zone: ZoneType
    confidence: float
    reason: str
    personality_message: str
    summary: Optional[str] = None
    recommended_action: Optional[str] = None
    draft_reply: Optional[str] = None
    action_type: Optional[str] = None

class MessageStatusUpdate(BaseModel):
    status: str
    snoozed_until: Optional[str] = None

# ============== AUTH HELPERS ==============

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid authentication")
        user = db.get_user_by_id(user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid authentication")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ============== AI VECTORIZER ==============

async def vectorize_email(subject: str, body: str) -> dict:
    """Async AI vectorization - uses Cerebras if available, otherwise rule-based."""
    logger.info("Vectorizing email content asynchronously...")
    
    if cerebras_client:
        try:
            prompt = f"""Analyze this email and return JSON:
Subject: {subject}
Body: {body[:1000]}

Return: {{"intent": "string", "risk": "low|medium|high|critical", "deadline": "ASAP|today|this_week|none", "context": "string", "lifecycle": "new|pending|resolved"}}"""
            
            response = cerebras_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b",
                max_tokens=200,
                temperature=0.1
            )
            result_text = response.choices[0].message.content.strip()
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()
            return json.loads(result_text)
        except Exception as e:
            logger.error(f"AI vectorization error: {e}")
    
    # Fallback rule-based vectorization
    risk = "low"
    deadline = "none"
    if any(w in subject.lower() for w in ["urgent", "critical", "stat", "emergency", "abnormal"]):
        risk = "critical"
        deadline = "ASAP"
    elif any(w in subject.lower() for w in ["refill", "prior auth", "callback"]):
        risk = "medium"
        deadline = "today"
    
    return {
        "intent": "general_inquiry",
        "risk": risk,
        "deadline": deadline,
        "context": subject[:100],
        "lifecycle": "new"
    }

# ============== JONE5 CLASSIFIER ==============

class JonE5Classifier:
    STAT_KEYWORDS = ["critical", "urgent", "stat", "emergency", "abnormal", "positive", "elevated", "low", "high", "alert", "immediate", "asap"]
    STAT_DOMAINS = ["labcorp", "quest", "hospital", "er", "emergency", "lab", "pathology", "radiology"]
    TODAY_KEYWORDS = ["refill", "prescription", "prior auth", "authorization", "referral", "appointment", "callback", "pharmacy", "medication"]
    TODAY_DOMAINS = ["pharmacy", "cvs", "walgreens", "insurance", "medicaid", "medicare", "aetna", "cigna", "united", "bcbs"]
    THIS_WEEK_KEYWORDS = ["billing", "invoice", "payment", "claim", "denial", "records request", "compliance", "audit"]
    LATER_KEYWORDS = ["newsletter", "cme", "conference", "webinar", "marketing", "promotion", "sale", "discount", "survey"]
    
    PERSONALITY_MESSAGES = {
        "STAT": ["Doctor, I've detected a potentially urgent item. This one needs your attention.", "Sentinel alert: High-priority message detected. Please review promptly."],
        "TODAY": ["Zzzzip! Sorted! This one needs attention today!", "Input received! Routing to TODAY - action needed soon!"],
        "THIS_WEEK": ["Scanning... analyzing... okay! This can wait a few days.", "Sorted! This one goes to THIS WEEK - no rush!"],
        "LATER": ["Zoom zoom! Low priority detected! Filing to LATER!", "Input processed! This one can definitely wait!"]
    }
    
    CORRECTION_THANKS = ["Thank you! Correction received!", "Got it! Learning from you!", "Correction logged!"]
    
    def _check_keywords(self, text: str, keywords: list) -> tuple:
        text_lower = text.lower()
        for keyword in keywords:
            if keyword.lower() in text_lower:
                return True, keyword
        return False, ""
    
    def _check_domain(self, domain: str, domains: list) -> tuple:
        domain_lower = domain.lower()
        for d in domains:
            if d.lower() in domain_lower:
                return True, d
        return False, ""
    
    def _llm_classify(self, sender: str, sender_domain: str, subject: str, snippet: Optional[str] = None) -> Optional[JonE5Response]:
        if not cerebras_client:
            return None
        try:
            prompt = f"""You are jonE5, an AI medical office assistant. Analyze this email:
From: {sender} ({sender_domain})
Subject: {subject}
Content: {snippet or 'No content'}

Return JSON: {{"zone": "STAT|TODAY|THIS_WEEK|LATER", "confidence": 0.0-1.0, "reason": "why", "summary": "1-2 sentences", "recommended_action": "what to do", "action_type": "reply|forward|call|archive|delegate|review", "draft_reply": "if reply needed, else null"}}"""

            response = cerebras_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b",
                max_tokens=500,
                temperature=0.2
            )
            result_text = response.choices[0].message.content.strip()
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()
            result = json.loads(result_text)
            zone = result.get("zone", "THIS_WEEK")
            if zone not in ["STAT", "TODAY", "THIS_WEEK", "LATER"]:
                zone = "THIS_WEEK"
            return JonE5Response(
                zone=zone,
                confidence=min(max(float(result.get("confidence", 0.75)), 0.0), 1.0),
                reason=result.get("reason", "AI analysis"),
                personality_message=random.choice(self.PERSONALITY_MESSAGES[zone]),
                summary=result.get("summary"),
                recommended_action=result.get("recommended_action"),
                action_type=result.get("action_type"),
                draft_reply=result.get("draft_reply") if result.get("draft_reply") != "null" else None
            )
        except Exception as e:
            logger.error(f"LLM classification error: {e}")
            return None
    
    def classify(self, sender: str, sender_domain: str, subject: str, snippet: Optional[str] = None) -> JonE5Response:
        if cerebras_client:
            llm_result = self._llm_classify(sender, sender_domain, subject, snippet)
            if llm_result:
                return llm_result
        
        combined_text = f"{subject} {snippet or ''}"
        
        found, keyword = self._check_keywords(combined_text, self.STAT_KEYWORDS)
        if found:
            return JonE5Response(zone="STAT", confidence=0.92, reason=f"Urgent keyword: '{keyword}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["STAT"]),
                summary=f"URGENT: Contains '{keyword}'", recommended_action="Review immediately", action_type="review")
        
        found, domain = self._check_domain(sender_domain, self.STAT_DOMAINS)
        if found:
            return JonE5Response(zone="STAT", confidence=0.88, reason=f"High-priority domain: '{domain}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["STAT"]),
                summary=f"From {domain}", recommended_action="Review immediately", action_type="review")
        
        found, keyword = self._check_keywords(combined_text, self.TODAY_KEYWORDS)
        if found:
            return JonE5Response(zone="TODAY", confidence=0.85, reason=f"Same-day keyword: '{keyword}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["TODAY"]),
                summary=f"Action needed: {keyword}", recommended_action=f"Process {keyword} today", action_type="reply")
        
        found, domain = self._check_domain(sender_domain, self.TODAY_DOMAINS)
        if found:
            return JonE5Response(zone="TODAY", confidence=0.82, reason=f"Action-required sender: '{domain}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["TODAY"]),
                summary=f"From {domain}", recommended_action="Respond today", action_type="reply")
        
        found, keyword = self._check_keywords(combined_text, self.THIS_WEEK_KEYWORDS)
        if found:
            return JonE5Response(zone="THIS_WEEK", confidence=0.80, reason=f"Administrative: '{keyword}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["THIS_WEEK"]),
                summary=f"Administrative: {keyword}", recommended_action=f"Handle {keyword} this week", action_type="delegate")
        
        found, keyword = self._check_keywords(combined_text, self.LATER_KEYWORDS)
        if found:
            return JonE5Response(zone="LATER", confidence=0.90, reason=f"Low-priority: '{keyword}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["LATER"]),
                summary=f"FYI: {keyword}", recommended_action="Archive", action_type="archive")
        
        return JonE5Response(zone="THIS_WEEK", confidence=0.60, reason="No strong signals", personality_message="Hmm... putting in THIS_WEEK for review.",
            summary=f"Email from {sender}", recommended_action="Review manually", action_type="review")
    
    def get_correction_message(self) -> str:
        return random.choice(self.CORRECTION_THANKS)

jone5 = JonE5Classifier()

# ============== ROUTES ==============

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "DocBoxRX API", "sentinel": "jonE5 online"}

@app.post("/api/auth/register", response_model=Token)
async def register(user: UserCreate):
    if db.email_exists(user.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    user_id = str(uuid.uuid4())
    new_user = db.create_user(user_id, user.email, user.name, user.practice_name, get_password_hash(user.password))
    access_token = create_access_token(data={"sub": user_id})
    logger.info(f"New user registered: {user.email}")
    return Token(access_token=access_token, token_type="bearer", user={k: v for k, v in new_user.items() if k != "hashed_password"})

@app.post("/api/auth/login", response_model=Token)
async def login(credentials: UserLogin):
    user = db.get_user_by_email(credentials.email)
    if not user or not verify_password(credentials.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    access_token = create_access_token(data={"sub": user["id"]})
    logger.info(f"User logged in: {credentials.email}")
    return Token(access_token=access_token, token_type="bearer", user={k: v for k, v in user.items() if k != "hashed_password"})

@app.get("/api/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {k: v for k, v in current_user.items() if k != "hashed_password"}

# ============== EMAIL INGESTION (PRODUCTION READY) ==============

@app.post("/api/messages/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_email_endpoint(payload: EmailIngest, current_user: dict = Depends(get_current_user)):
    """Production-ready email ingestion with async AI vectorization."""
    try:
        user_id = current_user["id"]
        logger.info(f"Received email from {payload.sender} for user {user_id}")
        
        body_plain = payload.body_plain or payload.snippet
        sender_domain = payload.sender_domain or (re.search(r'@([\w.-]+)', payload.sender).group(1) if re.search(r'@([\w.-]+)', payload.sender) else "unknown")
        
        # Async vectorization
        vector_data = await vectorize_email(payload.subject, body_plain or "")
        logger.info(f"Vector data: {vector_data}")
        
        # jonE5 classification
        classification = jone5.classify(sender=payload.sender, sender_domain=sender_domain, subject=payload.subject, snippet=body_plain)
        
        message_id = payload.message_id or str(uuid.uuid4())
        now = datetime.utcnow()
        
        message = {
            "id": message_id, "user_id": user_id, "sender": payload.sender, "sender_domain": sender_domain,
            "subject": payload.subject, "snippet": body_plain, "zone": classification.zone,
            "confidence": classification.confidence, "reason": classification.reason,
            "jone5_message": classification.personality_message, "received_at": now.isoformat(),
            "classified_at": now.isoformat(), "corrected": False,
            "summary": classification.summary, "recommended_action": classification.recommended_action,
            "action_type": classification.action_type, "draft_reply": classification.draft_reply
        }
        
        db.create_message(message)
        logger.info(f"Saved email with ID {message_id}, zone: {classification.zone}")
        
        try:
            await ingest_message({
                "id": message_id, "grant_id": payload.grant_id or payload.source_id or "manual",
                "subject": payload.subject, "body": body_plain, "from": payload.sender,
            })
        except Exception as e:
            logger.error(f"State vector ingestion error: {e}")
        
        return MessageResponse(**{**message, "received_at": now, "classified_at": now})
    except Exception as e:
        logger.error(f"Error ingesting email: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/messages")
async def get_messages(zone: Optional[ZoneType] = None, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    messages = db.get_messages_by_user(user_id, zone)
    return {"messages": messages, "total": len(messages)}

@app.get("/api/messages/by-zone")
async def get_messages_by_zone(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    messages = db.get_messages_by_user(user_id)
    zones = {"STAT": [], "TODAY": [], "THIS_WEEK": [], "LATER": []}
    for msg in messages:
        zones[msg["zone"]].append(msg)
    return {"zones": zones, "counts": {zone: len(msgs) for zone, msgs in zones.items()}, "total": len(messages)}

@app.post("/api/messages/correct")
async def correct_message(correction: ZoneCorrection, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    message = db.get_message_by_id(correction.message_id, user_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    old_zone = message["zone"]
    corrected_at = datetime.utcnow().isoformat()
    db.update_message_zone(correction.message_id, correction.new_zone, corrected_at)
    db.set_rule_override(f"sender:{message['sender'].lower()}", correction.new_zone)
    db.create_correction({"id": str(uuid.uuid4()), "user_id": user_id, "old_zone": old_zone, "new_zone": correction.new_zone, "sender": message["sender"], "corrected_at": corrected_at})
    message["zone"] = correction.new_zone
    message["corrected"] = True
    return {"success": True, "message": message, "jone5_response": jone5.get_correction_message()}

@app.delete("/api/messages/{message_id}")
async def delete_message(message_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    if db.delete_message(message_id, user_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="Message not found")

@app.get("/api/stats")
async def get_stats(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    messages = db.get_messages_by_user(user_id)
    corrections = db.get_corrections_by_user(user_id)
    zone_counts = {"STAT": 0, "TODAY": 0, "THIS_WEEK": 0, "LATER": 0}
    for msg in messages:
        zone_counts[msg["zone"]] += 1
    return {"total_messages": len(messages), "total_corrections": len(corrections), "zone_counts": zone_counts}

@app.get("/api/action-center")
async def get_action_center(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    action_items = db.get_action_items(user_id)
    return {
        "urgent_count": len(action_items["urgent_items"]),
        "needs_reply_count": len(action_items["needs_reply"]),
        "snoozed_due_count": len(action_items["snoozed_due"]),
        "done_today": action_items["done_today"],
        "total_action_items": action_items["total_action_items"],
        "urgent_items": action_items["urgent_items"][:5],
        "needs_reply": action_items["needs_reply"][:5],
        "snoozed_due": action_items["snoozed_due"]
    }

@app.post("/api/messages/{message_id}/status")
async def update_message_status(message_id: str, update: MessageStatusUpdate, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    if db.update_message_status(message_id, user_id, update.status, update.snoozed_until):
        return {"success": True, "status": update.status}
    raise HTTPException(status_code=404, detail="Message not found")

@app.post("/api/messages/{message_id}/replied")
async def mark_message_replied(message_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    if db.mark_message_replied(message_id, user_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="Message not found")

@app.post("/api/messages/{message_id}/escalate")
async def escalate_state_vector(message_id: str):
    vector = db.get_state_vector_by_id(message_id)
    if not vector:
        raise HTTPException(status_code=404, detail="State vector not found")
    updated = db.update_state_vector_escalate(message_id)
    db.create_message_event({
        "id": str(uuid.uuid4()), "vector_id": message_id, "event_type": "ESCALATED",
        "description": "Manual escalation", "created_at": datetime.utcnow().isoformat(),
    })
    return updated

@app.post("/api/demo/seed")
async def seed_demo_data(current_user: dict = Depends(get_current_user)):
    demo_emails = [
        {"sender": "results@labcorp.com", "subject": "CRITICAL: Abnormal CBC Results", "snippet": "Hemoglobin: 6.2 g/dL (CRITICAL LOW). Patient requires immediate evaluation."},
        {"sender": "alerts@questdiagnostics.com", "subject": "STAT: Potassium Level Alert", "snippet": "Potassium: 6.8 mEq/L (CRITICAL HIGH). EKG recommended."},
        {"sender": "pharmacy@cvs.com", "subject": "Refill Request - Metformin 500mg", "snippet": "Patient Robert Williams requests refill. 0 refills remaining."},
        {"sender": "priorauth@aetna.com", "subject": "Prior Authorization Required", "snippet": "MRI Lumbar Spine - additional documentation required by Jan 12."},
        {"sender": "billing@medicaid.gov", "subject": "Claim Denial Notice", "snippet": "Claim MC-2026-334455 denied. Missing prior authorization."},
        {"sender": "records@hospital.org", "subject": "Medical Records Request", "snippet": "Records requested for patient Patricia Davis transfer of care."},
        {"sender": "newsletter@medscape.com", "subject": "Weekly CME Update", "snippet": "New courses available: Diabetes Management 2026."},
        {"sender": "marketing@dentalequip.com", "subject": "50% Off Dental Supplies!", "snippet": "January clearance sale - use code JANUARY50."},
    ]
    results = []
    for email_data in demo_emails:
        email_obj = EmailIngest(**email_data)
        result = await ingest_email_endpoint(email_obj, current_user)
        results.append({"subject": email_data["subject"], "zone": result.zone})
    return {"seeded": len(results), "results": results}

# ============== SOURCES ==============

INBOUND_DOMAIN = os.environ.get("INBOUND_DOMAIN", "inbound.docboxrx.com")

def generate_inbound_token() -> str:
    return hashlib.sha256(f"{uuid.uuid4()}{datetime.utcnow().isoformat()}".encode()).hexdigest()[:16]

@app.post("/api/sources", response_model=EmailSource)
async def create_source(source: SourceCreate, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    source_id = str(uuid.uuid4())
    token = generate_inbound_token()
    new_source = {
        "id": source_id, "user_id": user_id, "name": source.name, "inbound_token": token,
        "inbound_address": f"inbox-{token}@{INBOUND_DOMAIN}", "created_at": datetime.utcnow().isoformat(), "email_count": 0
    }
    db.create_source(new_source)
    return EmailSource(**new_source)

@app.get("/api/sources")
async def get_sources(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    sources = db.get_sources_by_user(user_id)
    return {"sources": sources, "total": len(sources)}

@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    if db.delete_source(source_id, user_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="Source not found")

# ============== CLOUDMAILIN WEBHOOK ==============

CLOUDMAILIN_USER_ID = "cloudmailin-default-user"

@app.post("/api/inbound/cloudmailin")
async def cloudmailin_webhook(request: Request):
    content_type = request.headers.get("content-type", "")
    sender = subject = snippet = None
    
    if "application/json" in content_type:
        try:
            data = await request.json()
            headers = data.get("headers", {})
            envelope = data.get("envelope", {})
            sender = headers.get("from") or headers.get("From") or envelope.get("from") or data.get("from")
            subject = headers.get("subject") or headers.get("Subject") or data.get("subject")
            snippet = data.get("plain") or data.get("text") or data.get("html", "")
        except Exception as e:
            logger.error(f"CloudMailin JSON parse error: {e}")
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        try:
            form = await request.form()
            sender = form.get("from") or form.get("sender")
            subject = form.get("subject")
            snippet = form.get("plain") or form.get("text") or form.get("html", "")
        except Exception as e:
            logger.error(f"CloudMailin form parse error: {e}")
    
    if not sender or not subject:
        return {"success": False, "error": "Missing sender or subject", "received": True}
    
    sender_domain = "unknown"
    domain_match = re.search(r'@([\w.-]+)', sender)
    if domain_match:
        sender_domain = domain_match.group(1)
    
    vector_data = await vectorize_email(subject, snippet or "")
    classification = jone5.classify(sender=sender, sender_domain=sender_domain, subject=subject, snippet=snippet)
    
    message_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    message = {
        "id": message_id, "user_id": CLOUDMAILIN_USER_ID, "sender": sender, "sender_domain": sender_domain,
        "subject": subject, "snippet": snippet, "zone": classification.zone, "confidence": classification.confidence,
        "reason": classification.reason, "jone5_message": classification.personality_message,
        "received_at": now.isoformat(), "classified_at": now.isoformat(), "corrected": False,
        "source_id": "cloudmailin", "source_name": "CloudMailin"
    }
    
    db.create_cloudmailin_message(message)
    logger.info(f"CloudMailin email saved: {message_id}, zone: {classification.zone}")
    
    return {"success": True, "message_id": message_id, "zone": classification.zone, "jone5_says": classification.personality_message}

@app.get("/api/cloudmailin/messages")
async def get_cloudmailin_messages():
    messages = db.get_cloudmailin_messages()
    zones = {"STAT": [], "TODAY": [], "THIS_WEEK": [], "LATER": []}
    for msg in messages:
        zones[msg["zone"]].append(msg)
    return {"zones": zones, "counts": {zone: len(msgs) for zone, msgs in zones.items()}, "total": len(messages)}

@app.post("/api/inbound/{token}")
async def inbound_email_webhook(token: str, request: Request):
    source = db.get_source_by_token(token)
    if not source:
        raise HTTPException(status_code=404, detail="Invalid inbound token")
    
    user_id = source["user_id"]
    source_id = source["id"]
    source_name = source["name"]
    content_type = request.headers.get("content-type", "")
    sender = subject = snippet = None
    
    if "application/json" in content_type:
        try:
            data = await request.json()
            sender = data.get("from") or data.get("sender") or data.get("envelope", {}).get("from")
            subject = data.get("subject") or data.get("headers", {}).get("subject")
            snippet = data.get("text") or data.get("plain") or data.get("body")
            if snippet:
                snippet = snippet[:500]
        except:
            pass
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        try:
            form = await request.form()
            sender = form.get("from") or form.get("sender")
            subject = form.get("subject")
            snippet = form.get("stripped-text") or form.get("text") or form.get("body-plain")
            if snippet:
                snippet = snippet[:500]
        except:
            pass
    
    if not sender or not subject:
        raise HTTPException(status_code=400, detail="Missing sender or subject")
    
    sender_domain = "unknown"
    domain_match = re.search(r'@([\w.-]+)', sender)
    if domain_match:
        sender_domain = domain_match.group(1)
    
    classification = jone5.classify(sender=sender, sender_domain=sender_domain, subject=subject, snippet=snippet)
    message_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    message = {
        "id": message_id, "user_id": user_id, "sender": sender, "sender_domain": sender_domain,
        "subject": subject, "snippet": snippet, "zone": classification.zone, "confidence": classification.confidence,
        "reason": classification.reason, "jone5_message": classification.personality_message,
        "received_at": now.isoformat(), "classified_at": now.isoformat(), "corrected": False,
        "source_id": source_id, "source_name": source_name
    }
    
    db.create_message(message)
    db.increment_source_email_count(source_id)
    
    return {"success": True, "message_id": message_id, "zone": classification.zone}

@app.get("/api/messages/by-source/{source_id}")
async def get_messages_by_source(source_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    messages = db.get_messages_by_user(user_id)
    filtered = [m for m in messages if m.get("source_id") == source_id]
    return {"messages": filtered, "total": len(filtered)}
