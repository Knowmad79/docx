from fastapi import FastAPI, Depends, HTTPException, status, Request, Form, BackgroundTasks
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional, Literal, List
from datetime import datetime, timedelta
import jwt
import bcrypt
import uuid
import re
import random
import logging
import os
import json
import hashlib
import email
from email import policy
from email.parser import BytesParser
from cerebras.cloud.sdk import Cerebras
from nylas import Client as NylasClient

# Import database module
from app import db
from app.routers.grid import router as grid_router
from app.services.ingestion import ingest_message

# Nylas configuration
NYLAS_API_KEY = os.environ.get("NYLAS_API_KEY", "nyk_v0_lPt52DfSYzutwat78WlItFejHHj2MyyZQPm1pHYQcmHO5gDWb6pIAwTanwZpHhkM")
NYLAS_CLIENT_ID = os.environ.get("NYLAS_CLIENT_ID", "ec54cf83-8648-4e04-b547-3de100de9b48")
NYLAS_API_URI = os.environ.get("NYLAS_API_URI", "https://api.us.nylas.com")
NYLAS_CALLBACK_URI = os.environ.get("NYLAS_CALLBACK_URI", "http://104.238.214.91:8000/api/nylas/callback")

nylas_client = NylasClient(api_key=NYLAS_API_KEY, api_uri=NYLAS_API_URI) if NYLAS_API_KEY else None

# Cerebras API for LLM fallback
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
cerebras_client = Cerebras(api_key=CEREBRAS_API_KEY) if CEREBRAS_API_KEY else None
LLM_CONFIDENCE_THRESHOLD = 0.70  # Use LLM if rules confidence is below this

app = FastAPI(title="DocBoxRX API", description="Sovereign Email Triage System")
app.include_router(grid_router)
logger = logging.getLogger(__name__)

# Disable CORS. Do not remove this for full-stack development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security
SECRET_KEY = "docboxrx-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

security = HTTPBearer()

ZoneType = Literal["STAT", "TODAY", "THIS_WEEK", "LATER"]

class EmailSource(BaseModel):
    id: str
    name: str  # e.g., "Gmail Personal", "Work Outlook"
    inbound_token: str  # unique token for this source
    inbound_address: str  # full inbound email address
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

class RequestCodeRequest(BaseModel):
    email: EmailStr

class VerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str

class EmailIngest(BaseModel):
    sender: str
    sender_domain: Optional[str] = None
    subject: str
    snippet: Optional[str] = None
    source_id: Optional[str] = None  # Which source this email came from
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
    # Agent outputs - what makes jonE5 an actual AI agent
    summary: Optional[str] = None  # 1-2 sentence summary
    recommended_action: Optional[str] = None  # What to do
    action_type: Optional[str] = None  # reply, forward, call, archive, delegate
    draft_reply: Optional[str] = None  # Auto-generated reply

class ZoneCorrection(BaseModel):
    message_id: str
    new_zone: ZoneType
    reason: Optional[str] = None

class JonE5Response(BaseModel):
    zone: ZoneType
    confidence: float
    reason: str
    personality_message: str
    summary: Optional[str] = None  # 1-2 sentence summary of the email
    recommended_action: Optional[str] = None  # What to do: "Call patient", "Forward to billing", etc.
    draft_reply: Optional[str] = None  # Auto-generated reply draft
    action_type: Optional[str] = None  # "reply", "forward", "call", "archive", "delegate"

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
    
    CORRECTION_THANKS = ["Thank you! Correction received! Updating my circuits!", "Oh! I love learning from you! Adjustment logged!", "Correction accepted! My triage pathways are sharper already!"]
    
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
        """Use Cerebras LLM for full agent analysis - classification, summary, action, and draft reply."""
        if not cerebras_client:
            return None
        
        try:
            prompt = f"""You are jonE5, an AI medical office assistant. Analyze this email and provide actionable intelligence.

Email:
- From: {sender} ({sender_domain})
- Subject: {subject}
- Content: {snippet or 'No content available'}

Provide a complete analysis as JSON:
{{
  "zone": "STAT|TODAY|THIS_WEEK|LATER",
  "confidence": 0.0-1.0,
  "reason": "why this priority",
  "summary": "1-2 sentence summary of what this email is about and what they want",
  "recommended_action": "specific action like 'Call patient back about lab results' or 'Forward to billing department' or 'Archive - no action needed'",
  "action_type": "reply|forward|call|archive|delegate|review",
  "draft_reply": "If action_type is reply, write a professional 2-3 sentence response. Otherwise null."
}}

Zones:
- STAT: Urgent (critical labs, emergencies) - needs immediate action
- TODAY: Same-day (refills, prior auths, referrals) - needs response today  
- THIS_WEEK: Standard (billing, records) - can wait a few days
- LATER: FYI only (newsletters, marketing) - archive or ignore

Be specific and actionable. The doctor is overwhelmed with emails - help them know exactly what to do."""

            response = cerebras_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b",
                max_tokens=500,
                temperature=0.2
            )
            
            result_text = response.choices[0].message.content.strip()
            # Try to extract JSON from response
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()
            
            result = json.loads(result_text)
            zone = result.get("zone", "THIS_WEEK")
            if zone not in ["STAT", "TODAY", "THIS_WEEK", "LATER"]:
                zone = "THIS_WEEK"
            confidence = min(max(float(result.get("confidence", 0.75)), 0.0), 1.0)
            
            return JonE5Response(
                zone=zone,
                confidence=confidence,
                reason=result.get("reason", "AI analysis"),
                personality_message=random.choice(self.PERSONALITY_MESSAGES[zone]),
                summary=result.get("summary"),
                recommended_action=result.get("recommended_action"),
                action_type=result.get("action_type"),
                draft_reply=result.get("draft_reply") if result.get("draft_reply") != "null" else None
            )
        except Exception as e:
            print(f"LLM classification error: {e}")
            return None
    
    def classify(self, sender: str, sender_domain: str, subject: str, snippet: Optional[str] = None) -> JonE5Response:
        """Classify email AND generate agent outputs (summary, action, draft reply)."""
        # ALWAYS use LLM for full agent analysis - this is what makes jonE5 an AI agent, not just a sorter
        if cerebras_client:
            llm_result = self._llm_classify(sender, sender_domain, subject, snippet)
            if llm_result:
                return llm_result
        
        # Fallback to rules-only if LLM is unavailable (no agent outputs)
        combined_text = f"{subject} {snippet or ''}"
        
        sender_key = f"sender:{sender.lower()}"
        override = db.get_rule_override(sender_key)
        if override:
            zone = override
            return JonE5Response(zone=zone, confidence=0.95, reason="Learned pattern from previous correction", personality_message=random.choice(self.PERSONALITY_MESSAGES[zone]),
                summary=f"Email from {sender} about: {subject[:50]}...", recommended_action="Review and take appropriate action", action_type="review")
        
        found, keyword = self._check_keywords(combined_text, self.STAT_KEYWORDS)
        if found:
            return JonE5Response(zone="STAT", confidence=0.92, reason=f"Urgent keyword: '{keyword}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["STAT"]),
                summary=f"URGENT: Contains '{keyword}' - requires immediate attention", recommended_action="Review immediately and respond", action_type="review")
        
        found, domain = self._check_domain(sender_domain, self.STAT_DOMAINS)
        if found:
            return JonE5Response(zone="STAT", confidence=0.88, reason=f"High-priority domain: '{domain}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["STAT"]),
                summary=f"From {domain} - likely urgent medical matter", recommended_action="Review lab/medical results immediately", action_type="review")
        
        found, keyword = self._check_keywords(combined_text, self.TODAY_KEYWORDS)
        if found:
            return JonE5Response(zone="TODAY", confidence=0.85, reason=f"Same-day keyword: '{keyword}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["TODAY"]),
                summary=f"Action needed today: {keyword}", recommended_action=f"Process {keyword} request today", action_type="reply")
        
        found, domain = self._check_domain(sender_domain, self.TODAY_DOMAINS)
        if found:
            return JonE5Response(zone="TODAY", confidence=0.82, reason=f"Action-required sender: '{domain}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["TODAY"]),
                summary=f"From {domain} - likely needs same-day response", recommended_action="Respond to request today", action_type="reply")
        
        found, keyword = self._check_keywords(combined_text, self.THIS_WEEK_KEYWORDS)
        if found:
            return JonE5Response(zone="THIS_WEEK", confidence=0.80, reason=f"Administrative keyword: '{keyword}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["THIS_WEEK"]),
                summary=f"Administrative matter: {keyword}", recommended_action=f"Handle {keyword} within the week", action_type="delegate")
        
        found, keyword = self._check_keywords(combined_text, self.LATER_KEYWORDS)
        if found:
            return JonE5Response(zone="LATER", confidence=0.90, reason=f"Low-priority keyword: '{keyword}'", personality_message=random.choice(self.PERSONALITY_MESSAGES["LATER"]),
                summary=f"FYI only: {keyword}", recommended_action="Archive - no action needed", action_type="archive")
        
        return JonE5Response(zone="THIS_WEEK", confidence=0.60, reason="No strong signals - defaulting to THIS_WEEK", personality_message="Hmm... I'm not sure about this one. Putting it in THIS_WEEK for your review!",
            summary=f"Email from {sender}: {subject[:50]}...", recommended_action="Review and categorize manually", action_type="review")
    
    def get_correction_message(self) -> str:
        return random.choice(self.CORRECTION_THANKS)

jone5 = JonE5Classifier()

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "DocBoxRX API", "sentinel": "jonE5 online"}

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

class EmailSendRequest(BaseModel):
    to_email: EmailStr
    subject: str
    body: str

# 1. PASTE EMAIL
@app.post("/api/emails/paste")
async def paste_email(data: EmailIngest, current_user: dict = Depends(get_current_user)):
    """Accepts raw email content and runs it through jonE5 triage."""
    result = await ingest_message({
        "sender": data.sender,
        "subject": data.subject,
        "body_plain": data.body_plain,
        "user_id": current_user["id"]
    })
    return {"success": True, "message": "Email triaged and saved", "id": result.get("id")}

# 2. SEND EMAIL
@app.post("/api/emails/send")
async def send_email(data: EmailSendRequest, current_user: dict = Depends(get_current_user)):
    """Sends a real email using SMTP configuration."""
    smtp_server = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")

    if not smtp_user or not smtp_pass:
        raise HTTPException(status_code=500, detail="SMTP not configured on server")

    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = data.to_email
        msg['Subject'] = data.subject
        msg.attach(MIMEText(data.body, 'plain'))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        
        return {"success": True, "message": "Email sent successfully"}
    except Exception as e:
        logger.error(f"SMTP Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 3. GET FULL CONTENT
@app.get("/api/messages/{message_id}")
async def get_message_detail(message_id: str, current_user: dict = Depends(get_current_user)):
    """Returns the full email body."""
    msg = db.get_message_by_id(message_id, current_user["id"])
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    return msg

@app.post("/api/auth/request-code")
async def request_access_code(data: RequestCodeRequest):
    """
    Generate a 6-digit access code and send it to the user.
    Auto-provisions the user and an inbox if they don't exist.
    """
    email = data.email.lower()
    
    # Generate 6-digit code
    code = f"{random.randint(100000, 999999)}"
    expires_at = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
    
    # Save code to DB
    db.upsert_login_code(email, code, expires_at)
    
    # SHADOW PROVISIONING: Create user and inbox if they don't exist
    user = db.get_user_by_email(email)
    if not user:
        user_id = str(uuid.uuid4())
        # Use a random password since they won't use it (code-only login)
        random_password = hashlib.sha256(os.urandom(32)).hexdigest()
        hashed_password = get_password_hash(random_password)
        
        user = db.create_user(
            user_id=user_id,
            email=email,
            name=email.split('@')[0].capitalize(),
            practice_name="Main Clinic",
            hashed_password=hashed_password
        )
        
        # AUTO-CREATE INBOX (Source)
        source_id = str(uuid.uuid4())
        token = hashlib.md5(f"{user_id}-{email}".encode()).hexdigest()[:12]
        inbound_address = f"inbox-{token}@inbound.docboxrx.com"
        
        db.create_source({
            "id": source_id,
            "user_id": user_id,
            "name": "Main Clinic Inbox",
            "inbound_token": token,
            "inbound_address": inbound_address,
            "created_at": datetime.utcnow().isoformat(),
            "email_count": 0
        })
        
        logger.info(f"Shadow provisioned user and inbox for {email}")

    # TODO: Send real email. For now, we log it and return it in the response for testing.
    logger.info(f"ACCESS CODE FOR {email}: {code}")
    
    # For demo/dev convenience, we return the code. Remove in production!
    return {"success": True, "message": "Access code sent.", "dev_code": code}

@app.post("/api/auth/verify", response_model=Token)
async def verify_access_code(data: VerifyCodeRequest):
    """
    Verify the 6-digit access code and return a JWT.
    """
    email = data.email.lower()
    code = data.code
    
    code_record = db.get_login_code(email)
    if not code_record:
        raise HTTPException(status_code=401, detail="No code requested for this email")
    
    if code_record["code"] != code:
        raise HTTPException(status_code=401, detail="Invalid access code")
    
    expires_at = datetime.fromisoformat(code_record["expires_at"])
    if datetime.utcnow() > expires_at:
        db.delete_login_code(email)
        raise HTTPException(status_code=401, detail="Access code expired")
    
    # Code is valid, get user and delete code
    user = db.get_user_by_email(email)
    db.delete_login_code(email)
    
    if not user:
        # Should not happen if request-code was called
        raise HTTPException(status_code=500, detail="User not found after verification")
    
    # Create JWT
    access_token = create_access_token(data={"sub": user["id"]})
    
    return Token(
        access_token=access_token, 
        token_type="bearer", 
        user={k: v for k, v in user.items() if k != "hashed_password"}
    )

@app.post("/api/auth/register", response_model=Token)
async def register(user: UserCreate):
    # #region agent log
    with open(r'd:\dbrx\.cursor\debug.log', 'a') as f:
        import json, time
        f.write(json.dumps({"location":"main.py:register", "message":"Register hit", "data":{"email":user.email}, "timestamp":int(time.time()*1000), "sessionId":"debug-session", "hypothesisId":"A"}) + "\n")
    # #endregion
    if db.email_exists(user.email):
        raise HTTPException(status_code=400, detail="Email already registered")
    user_id = str(uuid.uuid4())
    new_user = db.create_user(user_id, user.email, user.name, user.practice_name, get_password_hash(user.password))
    access_token = create_access_token(data={"sub": user_id})
    return Token(access_token=access_token, token_type="bearer", user={k: v for k, v in new_user.items() if k != "hashed_password"})

@app.post("/api/auth/login", response_model=Token)
async def login(credentials: UserLogin):
    # #region agent log
    with open(r'd:\dbrx\.cursor\debug.log', 'a') as f:
        import json, time
        f.write(json.dumps({"location":"main.py:login", "message":"Login hit", "data":{"email":credentials.email}, "timestamp":int(time.time()*1000), "sessionId":"debug-session", "hypothesisId":"A"}) + "\n")
    # #endregion
    user = db.get_user_by_email(credentials.email)
    if not user or not verify_password(credentials.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    access_token = create_access_token(data={"sub": user["id"]})
    return Token(access_token=access_token, token_type="bearer", user={k: v for k, v in user.items() if k != "hashed_password"})

@app.get("/api/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {k: v for k, v in current_user.items() if k != "hashed_password"}

@app.post("/api/messages/ingest", response_model=MessageResponse)
async def ingest_email(email: EmailIngest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    body_plain = email.body_plain or email.snippet
    sender_domain = email.sender_domain or (re.search(r'@([\w.-]+)', email.sender).group(1) if re.search(r'@([\w.-]+)', email.sender) else "unknown")
    classification = jone5.classify(sender=email.sender, sender_domain=sender_domain, subject=email.subject, snippet=body_plain)
    message_id = email.message_id or str(uuid.uuid4())
    now = datetime.utcnow()
    message = {
        "id": message_id, "user_id": user_id, "sender": email.sender, "sender_domain": sender_domain,
        "subject": email.subject, "snippet": body_plain, "zone": classification.zone,
        "confidence": classification.confidence, "reason": classification.reason,
        "jone5_message": classification.personality_message, "received_at": now.isoformat(),
        "classified_at": now.isoformat(), "corrected": False,
        # Agent outputs - what makes jonE5 an AI agent
        "summary": classification.summary,
        "recommended_action": classification.recommended_action,
        "action_type": classification.action_type,
        "draft_reply": classification.draft_reply
    }
    db.create_message(message)
    try:
        await ingest_message(
            {
                "id": message_id,
                "grant_id": email.grant_id or email.source_id or "manual",
                "subject": email.subject,
                "body": body_plain,
                "from": email.sender,
            }
        )
    except Exception as e:
        print(f"Ingestion error: {e}")
        raise HTTPException(status_code=500, detail="State vector ingestion failed")
    return MessageResponse(**{**message, "received_at": now, "classified_at": now})

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
    message["corrected_at"] = corrected_at
    return {"success": True, "message": message, "jone5_response": jone5.get_correction_message(), "learning": f"jonE5 will now route emails from '{message['sender']}' to {correction.new_zone}"}

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

# ============== ACTION CENTER API ==============
# One-click actions for email workflow management

class MessageStatusUpdate(BaseModel):
    status: str  # 'done', 'archived', 'snoozed', 'active'
    snoozed_until: Optional[str] = None  # ISO datetime for snooze

@app.get("/api/action-center")
async def get_action_center(current_user: dict = Depends(get_current_user)):
    """Get the Action Center / Daily Brief data."""
    user_id = current_user["id"]
    action_items = db.get_action_items(user_id)
    return {
        "urgent_count": len(action_items["urgent_items"]),
        "needs_reply_count": len(action_items["needs_reply"]),
        "snoozed_due_count": len(action_items["snoozed_due"]),
        "done_today": action_items["done_today"],
        "total_action_items": action_items["total_action_items"],
        "urgent_items": action_items["urgent_items"][:5],  # Top 5 urgent items
        "needs_reply": action_items["needs_reply"][:5],  # Top 5 needing reply
        "snoozed_due": action_items["snoozed_due"]
    }

@app.post("/api/messages/{message_id}/status")
async def update_message_status(message_id: str, update: MessageStatusUpdate, current_user: dict = Depends(get_current_user)):
    """Update message status (done, archived, snoozed, active)."""
    user_id = current_user["id"]
    if db.update_message_status(message_id, user_id, update.status, update.snoozed_until):
        return {"success": True, "status": update.status}
    raise HTTPException(status_code=404, detail="Message not found")

@app.post("/api/messages/{message_id}/replied")
async def mark_message_replied(message_id: str, current_user: dict = Depends(get_current_user)):
    """Mark a message as replied (clears needs_reply flag)."""
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
    db.create_message_event(
        {
            "id": str(uuid.uuid4()),
            "vector_id": message_id,
            "event_type": "ESCALATED",
            "description": "Manual escalation to lead_doctor",
            "created_at": datetime.utcnow().isoformat(),
        }
    )
    return updated

@app.post("/api/demo/seed")
async def seed_demo_data(current_user: dict = Depends(get_current_user)):
    demo_emails = [
        {"sender": "results@labcorp.com", "subject": "CRITICAL: Abnormal CBC Results for Patient", "snippet": """Dear Dr. Smith,

CRITICAL LAB ALERT - Immediate Action Required

Patient: John Doe (DOB: 03/15/1965)
Account #: 789456123
Collection Date: January 5, 2026

ABNORMAL RESULTS:
- Hemoglobin: 6.2 g/dL (CRITICAL LOW - Reference: 13.5-17.5 g/dL)
- Hematocrit: 18.5% (CRITICAL LOW - Reference: 38.8-50.0%)
- RBC Count: 2.1 M/uL (LOW - Reference: 4.5-5.5 M/uL)

Clinical Interpretation: Severe anemia requiring immediate evaluation. Consider transfusion if symptomatic.

Please contact the patient immediately and consider emergency department referral if patient is symptomatic (shortness of breath, chest pain, dizziness).

This result has been called to your office at 2:45 PM.

LabCorp Clinical Services
1-800-845-6167"""},
        {"sender": "alerts@questdiagnostics.com", "subject": "STAT: Potassium Level Alert", "snippet": """STAT LABORATORY ALERT

Patient: Mary Johnson
DOB: 08/22/1978
Specimen ID: QD-2026-445566

CRITICAL VALUE NOTIFICATION:
Potassium: 6.8 mEq/L (CRITICAL HIGH)
Reference Range: 3.5-5.0 mEq/L

This potassium level is critically elevated and may indicate:
- Acute kidney injury
- Medication effect (ACE inhibitors, potassium-sparing diuretics)
- Hemolysis (verify specimen integrity)

RECOMMENDED ACTIONS:
1. Obtain STAT EKG to evaluate for cardiac effects
2. Consider repeat specimen to rule out hemolysis
3. Review current medications
4. Consider emergency treatment if EKG changes present

This critical value was verbally reported to your office at 3:15 PM on January 5, 2026.

Quest Diagnostics Critical Values Team
Available 24/7: 1-866-697-8378"""},
        {"sender": "pharmacy@cvs.com", "subject": "Refill Request - Metformin 500mg", "snippet": """CVS Pharmacy - Prescription Refill Request

Dear Provider,

We have received a refill request from your patient:

Patient: Robert Williams
DOB: 11/30/1958
Phone: (555) 234-5678

Medication: Metformin 500mg tablets
Directions: Take 1 tablet twice daily with meals
Quantity: 180 tablets (90-day supply)
Last Fill Date: October 5, 2025
Refills Remaining: 0

The patient has requested a new prescription as they are out of refills. Their last A1C on file was 7.2% (from September 2025).

To authorize this refill:
- Call: 1-800-746-7287
- Fax: 1-800-378-0443
- E-prescribe to: CVS #4521, 123 Main Street

CVS Pharmacy #4521
Store Manager: Jennifer Adams
Phone: (555) 123-4567"""},
        {"sender": "priorauth@aetna.com", "subject": "Prior Authorization Required", "snippet": """PRIOR AUTHORIZATION REQUEST

Reference #: PA-2026-789012
Date: January 5, 2026

Patient Information:
Name: Susan Chen
Member ID: W123456789
DOB: 04/12/1982

Requested Service:
MRI Lumbar Spine without contrast (CPT: 72148)

Authorization Status: PENDING - Additional Information Required

To complete this prior authorization, please provide:
1. Clinical notes documenting conservative treatment failure (minimum 6 weeks PT or chiropractic)
2. Documentation of radicular symptoms
3. Neurological examination findings
4. Any previous imaging results

Submit documentation via:
- Fax: 1-800-555-0199
- Provider Portal: aetna.com/provider

Deadline for submission: January 12, 2026

If documentation is not received by the deadline, this request will be administratively denied.

Aetna Prior Authorization Department
Hours: M-F 8am-6pm EST
Phone: 1-800-555-0123"""},
        {"sender": "billing@medicaid.gov", "subject": "Claim Denial Notice", "snippet": """EXPLANATION OF BENEFITS - CLAIM DENIAL

Claim Number: MC-2026-334455
Date of Service: December 15, 2025
Patient: James Thompson
Medicaid ID: 123456789A

Billed Amount: $450.00
Allowed Amount: $0.00
Patient Responsibility: $0.00
Payment: $0.00

DENIAL REASON: Missing or Invalid Documentation

Specific Issue: Prior authorization was not obtained for this service before the date of service. Procedure code 99215 requires prior authorization for patients with this diagnosis code combination.

APPEAL RIGHTS:
You have 60 days from the date of this notice to file an appeal. To appeal:
1. Submit a written request explaining why you believe this claim should be paid
2. Include any supporting documentation
3. Reference claim number MC-2026-334455

Mail appeals to:
Medicaid Appeals Department
PO Box 12345
State Capital, ST 12345

Questions? Call Provider Services: 1-800-555-6789"""},
        {"sender": "records@hospital.org", "subject": "Medical Records Request", "snippet": """MEDICAL RECORDS REQUEST

Request ID: MRR-2026-001234
Date: January 5, 2026

Requesting Facility: City General Hospital
Contact: Medical Records Department
Phone: (555) 987-6543
Fax: (555) 987-6544

Patient Information:
Name: Patricia Davis
DOB: 07/08/1970
SSN (last 4): XXX-XX-5678

Records Requested:
- Complete medical history
- All office visit notes from 2024-2025
- Laboratory results
- Imaging reports
- Current medication list
- Immunization records

Purpose: Patient transfer of care - Patient is relocating and establishing care at City General Hospital.

Authorization: Signed patient authorization form attached (HIPAA compliant release dated January 3, 2026)

Please send records via:
Secure fax: (555) 987-6544
Or mail to: City General Hospital, Medical Records, 500 Hospital Drive, City, ST 12345

Thank you for your prompt attention to this request."""},
        {"sender": "newsletter@medscape.com", "subject": "Weekly CME Update", "snippet": """MEDSCAPE CME WEEKLY DIGEST

Dear Healthcare Professional,

This week's featured continuing medical education opportunities:

1. NEW COURSE: "Managing Type 2 Diabetes in 2026: Latest Guidelines"
   - 2.0 CME Credits
   - Expert faculty from ADA
   - Free for Medscape members

2. UPDATED: "Antibiotic Stewardship in Primary Care"
   - 1.5 CME Credits
   - Includes case studies
   - Certificate available immediately

3. LIVE WEBINAR: "Advances in Heart Failure Management"
   - Date: January 10, 2026 at 7:00 PM EST
   - 1.0 CME Credit
   - Register now - limited spots

Your CME Status:
- Credits earned this year: 12.5
- Credits needed for renewal: 37.5
- Renewal deadline: December 31, 2026

Access all courses at: medscape.com/cme

Medscape Education
This is an automated message. Please do not reply."""},
        {"sender": "marketing@dentalequip.com", "subject": "50% Off Dental Supplies!", "snippet": """DENTAL EQUIPMENT SUPPLY CO.
JANUARY CLEARANCE SALE - 50% OFF!

Dear Valued Customer,

Start 2026 with huge savings on dental supplies!

THIS WEEK ONLY - 50% OFF:
- Disposable prophy angles (box of 500): $45 (reg $90)
- Nitrile exam gloves (case of 10 boxes): $65 (reg $130)
- Dental bibs (case of 500): $25 (reg $50)
- Sterilization pouches (box of 200): $18 (reg $36)

PLUS FREE SHIPPING on orders over $200!

Use code: JANUARY50 at checkout

Shop now: www.dentalequipsupply.com
Or call: 1-800-555-DENT

Sale ends January 12, 2026

Unsubscribe: Click here to stop receiving promotional emails
Dental Equipment Supply Co.
123 Commerce Blvd, Suite 100
Business City, ST 54321"""},
    ]
    results = []
    for email_data in demo_emails:
        email = EmailIngest(**email_data)
        result = await ingest_email(email, current_user)
        results.append({"subject": email_data["subject"], "zone": result.zone})
    return {"seeded": len(results), "results": results}

# ============== SOURCES API ==============
# Manage email sources (Gmail, Yahoo, Outlook accounts)

INBOUND_DOMAIN = os.environ.get("INBOUND_DOMAIN", "inbound.docboxrx.com")

def generate_inbound_token() -> str:
    """Generate a unique token for inbound email routing."""
    return hashlib.sha256(f"{uuid.uuid4()}{datetime.utcnow().isoformat()}".encode()).hexdigest()[:16]

@app.post("/api/sources", response_model=EmailSource)
async def create_source(source: SourceCreate, current_user: dict = Depends(get_current_user)):
    """Create a new email source (e.g., Gmail Personal, Work Outlook)."""
    user_id = current_user["id"]
    source_id = str(uuid.uuid4())
    token = generate_inbound_token()
    
    new_source = {
        "id": source_id,
        "user_id": user_id,
        "name": source.name,
        "inbound_token": token,
        "inbound_address": f"inbox-{token}@{INBOUND_DOMAIN}",
        "created_at": datetime.utcnow().isoformat(),
        "email_count": 0
    }
    
    db.create_source(new_source)
    return EmailSource(**new_source)

@app.get("/api/sources")
async def get_sources(current_user: dict = Depends(get_current_user)):
    """Get all email sources for the current user."""
    user_id = current_user["id"]
    sources = db.get_sources_by_user(user_id)
    return {"sources": sources, "total": len(sources)}

@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: str, current_user: dict = Depends(get_current_user)):
    """Delete an email source."""
    user_id = current_user["id"]
    if db.delete_source(source_id, user_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="Source not found")

# ============== INBOUND EMAIL WEBHOOK ==============
# Receives forwarded emails from email services (Mailgun, SendGrid, CloudMailin, etc.)

# Default user for CloudMailin emails (created on first email if needed)
CLOUDMAILIN_USER_ID = "cloudmailin-default-user"

@app.post("/api/inbound/cloudmailin")
async def cloudmailin_webhook(request: Request):
    """
    Dedicated CloudMailin webhook endpoint.
    Emails received here are stored for the default CloudMailin user.
    
    IMPORTANT: This endpoint does NOT log request bodies to protect PHI.
    """
    # Parse the incoming email based on content type
    content_type = request.headers.get("content-type", "")
    
    sender = None
    subject = None
    snippet = None
    
    if "application/json" in content_type:
        # JSON payload (CloudMailin normalized format)
        try:
            data = await request.json()
            # CloudMailin JSON format has headers object and envelope
            headers = data.get("headers", {})
            envelope = data.get("envelope", {})
            
            # Try multiple paths for sender
            sender = headers.get("from") or headers.get("From") or envelope.get("from") or data.get("from")
            # Try multiple paths for subject
            subject = headers.get("subject") or headers.get("Subject") or data.get("subject")
            # Get FULL body - no truncation so users can read entire email
            snippet = data.get("plain") or data.get("text") or data.get("html", "")
            # Don't truncate - store full email body
        except Exception as e:
            print(f"CloudMailin JSON parse error: {e}")
    
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        # Multipart form data (CloudMailin multipart format)
        try:
            form = await request.form()
            sender = form.get("from") or form.get("sender")
            subject = form.get("subject")
            # Get FULL body - no truncation so users can read entire email
            snippet = form.get("plain") or form.get("text") or form.get("html", "")
            # Don't truncate - store full email body
        except Exception as e:
            print(f"CloudMailin form parse error: {e}")
    
    if not sender or not subject:
        # Return 200 anyway to prevent CloudMailin from retrying
        return {"success": False, "error": "Could not parse email - missing sender or subject", "received": True}
    
    # Extract domain from sender
    sender_domain = "unknown"
    domain_match = re.search(r'@([\w.-]+)', sender)
    if domain_match:
        sender_domain = domain_match.group(1)
    
    # Classify with jonE5
    classification = jone5.classify(sender=sender, sender_domain=sender_domain, subject=subject, snippet=snippet)
    
    # Store message for CloudMailin user
    message_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    message = {
        "id": message_id,
        "user_id": CLOUDMAILIN_USER_ID,
        "sender": sender,
        "sender_domain": sender_domain,
        "subject": subject,
        "snippet": snippet,
        "zone": classification.zone,
        "confidence": classification.confidence,
        "reason": classification.reason,
        "jone5_message": classification.personality_message,
        "received_at": now.isoformat(),
        "classified_at": now.isoformat(),
        "corrected": False,
        "source_id": "cloudmailin",
        "source_name": "CloudMailin"
    }
    
    db.create_cloudmailin_message(message)
    try:
        await ingest_message(
            {
                "id": message_id,
                "grant_id": "cloudmailin",
                "subject": subject,
                "body": snippet,
                "from": sender,
            }
        )
    except Exception as e:
        print(f"State vector ingest failed: {e}")
    
    return {
        "success": True,
        "message_id": message_id,
        "zone": classification.zone,
        "jone5_says": classification.personality_message
    }

@app.get("/api/cloudmailin/messages")
async def get_cloudmailin_messages():
    """Get all messages received via CloudMailin (no auth required for demo)."""
    messages = db.get_cloudmailin_messages()
    zones = {"STAT": [], "TODAY": [], "THIS_WEEK": [], "LATER": []}
    for msg in messages:
        zones[msg["zone"]].append(msg)
    return {"zones": zones, "counts": {zone: len(msgs) for zone, msgs in zones.items()}, "total": len(messages)}

def parse_forwarded_email(raw_email: str) -> dict:
    """Parse a forwarded email to extract original sender, subject, and snippet."""
    try:
        msg = email.message_from_string(raw_email, policy=policy.default)
        
        # Get basic headers
        sender = msg.get("From", "unknown@unknown.com")
        subject = msg.get("Subject", "No Subject")
        
        # Try to get body snippet (first 500 chars of text content)
        snippet = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        snippet = part.get_content()[:500]
                    except:
                        pass
                    break
        else:
            try:
                snippet = msg.get_content()[:500] if hasattr(msg, 'get_content') else ""
            except:
                pass
        
        return {
            "sender": sender,
            "subject": subject,
            "snippet": snippet.strip() if snippet else None
        }
    except Exception as e:
        print(f"Email parsing error: {e}")
        return None

@app.post("/api/inbound/{token}")
async def inbound_email_webhook(token: str, request: Request):
    """
    Webhook endpoint for receiving forwarded emails.
    Compatible with Mailgun, SendGrid, CloudMailin, etc.
    
    IMPORTANT: This endpoint does NOT log request bodies to protect PHI.
    """
    source = db.get_source_by_token(token)
    if not source:
        raise HTTPException(status_code=404, detail="Invalid inbound token")
    
    user_id = source["user_id"]
    source_id = source["id"]
    source_name = source["name"]
    
    # Parse the incoming email based on content type
    content_type = request.headers.get("content-type", "")
    
    sender = None
    subject = None
    snippet = None
    
    if "application/json" in content_type:
        # JSON payload (CloudMailin, custom integrations)
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
        # Form data (Mailgun, SendGrid)
        try:
            form = await request.form()
            sender = form.get("from") or form.get("sender")
            subject = form.get("subject")
            snippet = form.get("stripped-text") or form.get("text") or form.get("body-plain")
            if snippet:
                snippet = snippet[:500]
            
            # If raw email is provided, parse it
            if form.get("email"):
                parsed = parse_forwarded_email(form.get("email"))
                if parsed:
                    sender = sender or parsed["sender"]
                    subject = subject or parsed["subject"]
                    snippet = snippet or parsed["snippet"]
        except:
            pass
    
    else:
        # Raw email (some providers send raw MIME)
        try:
            body = await request.body()
            parsed = parse_forwarded_email(body.decode("utf-8", errors="ignore"))
            if parsed:
                sender = parsed["sender"]
                subject = parsed["subject"]
                snippet = parsed["snippet"]
        except:
            pass
    
    if not sender or not subject:
        raise HTTPException(status_code=400, detail="Could not parse email - missing sender or subject")
    
    # Extract domain from sender
    sender_domain = "unknown"
    domain_match = re.search(r'@([\w.-]+)', sender)
    if domain_match:
        sender_domain = domain_match.group(1)
    
    # Classify with jonE5
    classification = jone5.classify(sender=sender, sender_domain=sender_domain, subject=subject, snippet=snippet)
    
    # Store message
    message_id = str(uuid.uuid4())
    now = datetime.utcnow()
    
    message = {
        "id": message_id,
        "user_id": user_id,
        "sender": sender,
        "sender_domain": sender_domain,
        "subject": subject,
        "snippet": snippet,
        "zone": classification.zone,
        "confidence": classification.confidence,
        "reason": classification.reason,
        "jone5_message": classification.personality_message,
        "received_at": now.isoformat(),
        "classified_at": now.isoformat(),
        "corrected": False,
        "source_id": source_id,
        "source_name": source_name
    }
    
    db.create_message(message)
    db.increment_source_email_count(source_id)
    try:
        await ingest_message(
            {
                "id": message_id,
                "grant_id": source_id,
                "subject": subject,
                "body": snippet,
                "from": sender,
            }
        )
    except Exception as e:
        print(f"State vector ingest failed: {e}")
    
    return {
        "success": True,
        "message_id": message_id,
        "zone": classification.zone,
        "jone5_says": classification.personality_message
    }

@app.get("/api/messages/by-source/{source_id}")
async def get_messages_by_source(source_id: str, current_user: dict = Depends(get_current_user)):
    """Get all messages from a specific source."""
    user_id = current_user["id"]
    messages = db.get_messages_by_user(user_id)
    filtered = [m for m in messages if m.get("source_id") == source_id]
    return {"messages": filtered, "total": len(filtered)}

# ============== NYLAS EMAIL INTEGRATION ==============
# Universal email connection via Nylas (Gmail, Outlook, Yahoo, etc.)

@app.get("/api/nylas/auth-url")
async def get_nylas_auth_url(provider: str = "google", current_user: dict = Depends(get_current_user)):
    """Generate Nylas OAuth URL for connecting an email account."""
    if not nylas_client or not NYLAS_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Nylas not configured")
    
    # Include user_id in state so we can link the grant to the user after callback
    state = current_user["id"]
    
    # Build auth URL with provider to force Google/Microsoft OAuth instead of IMAP
    auth_url = nylas_client.auth.url_for_oauth2({
        "client_id": NYLAS_CLIENT_ID,
        "redirect_uri": NYLAS_CALLBACK_URI,
        "state": state,
        "provider": provider,  # Force specific provider (google, microsoft, imap)
    })
    
    return {"auth_url": auth_url, "provider": provider}

@app.get("/api/nylas/callback")
async def nylas_oauth_callback(code: str, state: str = None, background_tasks: BackgroundTasks = None):
    """Handle Nylas OAuth callback and exchange code for grant."""
    from fastapi.responses import RedirectResponse
    
    frontend_url = "https://docboxr.netlify.app"
    
    if not nylas_client:
        return RedirectResponse(url=f"{frontend_url}?nylas_error=Nylas+not+configured")
    
    try:
        # Exchange code for token/grant
        response = nylas_client.auth.exchange_code_for_token({
            "client_id": NYLAS_CLIENT_ID,
            "client_secret": NYLAS_API_KEY,
            "code": code,
            "redirect_uri": NYLAS_CALLBACK_URI,
        })
        
        grant_id = response.grant_id
        email = response.email if hasattr(response, 'email') else "unknown@email.com"
        access_token = getattr(response, "access_token", None)
        refresh_token = getattr(response, "refresh_token", None)
        expires_at = getattr(response, "expires_at", None)
        
        # If we have a user_id in state, save the grant
        if state:
            user_id = state
            # Check if grant already exists for this user
            existing_grants = db.get_nylas_grants_by_user(user_id)
            grant_exists = any(g['grant_id'] == grant_id for g in existing_grants)
            
            if not grant_exists:
                grant_record = {
                    "id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "grant_id": grant_id,
                    "email": email,
                    "provider": response.provider if hasattr(response, 'provider') else None,
                    "created_at": datetime.utcnow().isoformat(),
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at": expires_at,
                    "updated_at": datetime.utcnow().isoformat(),
                }
                db.create_nylas_grant(grant_record)
            else:
                if access_token or refresh_token or expires_at:
                    db.update_nylas_grant_tokens(grant_id, access_token, refresh_token, expires_at)

            if background_tasks:
                background_tasks.add_task(sync_nylas_emails_for_grant, grant_id, user_id, 50)
        
        # Redirect back to frontend with success
        return RedirectResponse(url=f"{frontend_url}?nylas_success=true&email={email}")
    except Exception as e:
        # Redirect back to frontend with error
        error_msg = str(e).replace(" ", "+")
        return RedirectResponse(url=f"{frontend_url}?nylas_error={error_msg}")

@app.get("/api/nylas/grants")
async def get_nylas_grants(current_user: dict = Depends(get_current_user)):
    """Get all connected email accounts for the current user."""
    user_id = current_user["id"]
    grants = db.get_nylas_grants_by_user(user_id)
    return {"grants": grants, "total": len(grants)}

@app.delete("/api/nylas/grants/{grant_id}")
async def delete_nylas_grant(grant_id: str, current_user: dict = Depends(get_current_user)):
    """Disconnect an email account."""
    user_id = current_user["id"]
    if db.delete_nylas_grant(grant_id, user_id):
        return {"success": True}
    raise HTTPException(status_code=404, detail="Grant not found")

async def sync_nylas_emails_for_grant(grant_id: str, user_id: str, limit: int = 50):
    if not nylas_client:
        raise HTTPException(status_code=500, detail="Nylas not configured")

    grants = db.get_nylas_grants_by_user(user_id)
    grant = next((g for g in grants if g["grant_id"] == grant_id), None)
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found")

    await maybe_refresh_grant_tokens(grant)

    messages_response = nylas_client.messages.list(
        grant_id,
        query_params={"limit": limit, "in": ["INBOX"]},
    )

    classified_count = 0
    results = []

    for msg in messages_response.data:
        from_list = msg.from_ if hasattr(msg, "from_") else msg.get("from", [])
        if from_list:
            first_from = from_list[0]
            if hasattr(first_from, "email"):
                sender = first_from.email
                sender_name = first_from.name if hasattr(first_from, "name") and first_from.name else sender
            elif isinstance(first_from, dict):
                sender = first_from.get("email", "unknown@unknown.com")
                sender_name = first_from.get("name", sender)
            else:
                sender = "unknown@unknown.com"
                sender_name = sender
        else:
            sender = "unknown@unknown.com"
            sender_name = sender

        subject = msg.subject if hasattr(msg, "subject") else msg.get("subject", "No Subject") or "No Subject"
        body_raw = msg.body if hasattr(msg, "body") else msg.get("body") if isinstance(msg, dict) else None
        snippet_raw = msg.snippet if hasattr(msg, "snippet") else msg.get("snippet", None)
        snippet = body_raw if body_raw else snippet_raw

        sender_domain = "unknown"
        domain_match = re.search(r"@([\w.-]+)", sender)
        if domain_match:
            sender_domain = domain_match.group(1)

        classification = jone5.classify(
            sender=f"{sender_name} <{sender}>",
            sender_domain=sender_domain,
            subject=subject,
            snippet=snippet,
        )

        message_id = str(uuid.uuid4())
        now = datetime.utcnow()

        message = {
            "id": message_id,
            "user_id": user_id,
            "sender": f"{sender_name} <{sender}>",
            "sender_domain": sender_domain,
            "subject": subject,
            "snippet": snippet,
            "zone": classification.zone,
            "confidence": classification.confidence,
            "reason": classification.reason,
            "jone5_message": classification.personality_message,
            "received_at": now.isoformat(),
            "classified_at": now.isoformat(),
            "corrected": False,
            "source_id": f"nylas-{grant_id}",
            "source_name": f"Nylas: {grant['email']}",
        }

        db.create_message(message)
        try:
            await ingest_message(
                {
                    "id": message_id,
                    "grant_id": grant_id,
                    "subject": subject,
                    "body": snippet,
                    "from": sender,
                }
            )
        except Exception as e:
            logger.error("State vector ingest failed: %s", e)
        classified_count += 1
        results.append({"subject": subject, "zone": classification.zone})

    db.update_nylas_grant_sync_time(grant_id, datetime.utcnow().isoformat())

    return {
        "success": True,
        "synced": classified_count,
        "results": results,
        "jone5_says": "Zoom zoom! Emails synced and classified!",
    }


async def maybe_refresh_grant_tokens(grant: dict):
    refresh_token = grant.get("refresh_token")
    expires_at = grant.get("expires_at")
    if not refresh_token or not expires_at:
        return
    try:
        expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except Exception:
        return
    if expires_dt > datetime.utcnow():
        return
    if hasattr(nylas_client.auth, "refresh_token"):
        response = nylas_client.auth.refresh_token(
            {
                "client_id": NYLAS_CLIENT_ID,
                "client_secret": NYLAS_API_KEY,
                "refresh_token": refresh_token,
            }
        )
        access_token = getattr(response, "access_token", None)
        refresh_token = getattr(response, "refresh_token", refresh_token)
        expires_at = getattr(response, "expires_at", None)
        db.update_nylas_grant_tokens(grant["grant_id"], access_token, refresh_token, expires_at)
    else:
        logger.warning("Nylas refresh token API not available in SDK.")


@app.post("/api/nylas/sync/{grant_id}")
async def sync_nylas_emails(grant_id: str, limit: int = 50, current_user: dict = Depends(get_current_user)):
    """Sync recent emails from a connected account and classify with jonE5."""
    return await sync_nylas_emails_for_grant(grant_id, current_user["id"], limit)


@app.post("/api/nylas/webhook")
async def nylas_webhook(request: Request):
    """Handle Nylas webhook events for real-time ingestion."""
    if not nylas_client:
        raise HTTPException(status_code=500, detail="Nylas not configured")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    events = payload.get("data") or payload.get("events") or []
    if isinstance(events, dict):
        events = [events]

    ingested = 0
    for event in events:
        event_type = event.get("type") or event.get("event")
        if event_type and "message" not in event_type:
            continue

        grant_id = event.get("grant_id") or event.get("data", {}).get("grant_id")
        message_id = event.get("object") or event.get("data", {}).get("object") or event.get("id")
        if not grant_id or not message_id:
            continue

        try:
            message_obj = None
            if hasattr(nylas_client.messages, "find"):
                message_obj = nylas_client.messages.find(grant_id, message_id)
            if message_obj:
                subject = message_obj.subject if hasattr(message_obj, "subject") else message_obj.get("subject")
                snippet = message_obj.snippet if hasattr(message_obj, "snippet") else message_obj.get("snippet")
                from_list = message_obj.from_ if hasattr(message_obj, "from_") else message_obj.get("from", [])
                sender = "unknown@unknown.com"
                if from_list:
                    first_from = from_list[0]
                    sender = first_from.email if hasattr(first_from, "email") else first_from.get("email", sender)
            else:
                subject = event.get("data", {}).get("subject") or "No Subject"
                snippet = event.get("data", {}).get("snippet") or ""
                sender = event.get("data", {}).get("from") or "unknown@unknown.com"

            await ingest_message(
                {
                    "id": message_id,
                    "grant_id": grant_id,
                    "subject": subject,
                    "body": snippet,
                    "from": sender,
                }
            )
            ingested += 1
        except Exception as exc:
            logger.error("Webhook ingest failed: %s", exc)

    return {"success": True, "ingested": ingested}


@app.get("/api/nylas/webhook")
async def nylas_webhook_challenge(challenge: str | None = None):
    """Respond to Nylas webhook verification challenge."""
    if not challenge:
        raise HTTPException(status_code=400, detail="Missing challenge")
    return PlainTextResponse(content=challenge)
