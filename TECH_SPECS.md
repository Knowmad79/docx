# DocBoxRX Technical Specifications & Research Guide

## Current Architecture

### Frontend (React + TypeScript)
- **Framework:** Vite + React 18 + TypeScript
- **UI Library:** shadcn/ui + Tailwind CSS
- **Icons:** Lucide React
- **Deployed URL:** https://full-stack-apps-ah1tro24.devinapps.com
- **Source:** `/home/ubuntu/docboxrx/docboxrx-frontend/`

### Backend (FastAPI + Python)
- **Framework:** FastAPI (Python 3.12)
- **Auth:** JWT tokens (PyJWT) + bcrypt password hashing
- **AI:** Cerebras Cloud SDK (llama-3.3-70b)
- **Deployed URL:** https://app-nkizyevt.fly.dev
- **Source:** `/home/ubuntu/docboxrx/docboxrx-backend/`

### Database (Neon Postgres)
- **Provider:** Neon (serverless Postgres)
- **Connection:** postgresql://neondb_owner:npg_Z60uvbwqlBzk@ep-mute-hill-adb7l32q-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require
- **Tables:** users, messages, sources, corrections, rule_overrides, cloudmailin_messages

### Current Email Ingestion (CloudMailin)
- **Webhook URL:** https://app-nkizyevt.fly.dev/api/inbound/cloudmailin
- **Inbound Address:** e008bbad5683d73d4ace@cloudmailin.net
- **Status:** Working (bug fixed), but requires forwarding setup

---

## Tech Tree: Email Ingestion Options

### Option 1: Email Forwarding (Current)
```
User's Gmail/Yahoo/Outlook
        ↓ (forwarding rule)
CloudMailin/Mailgun/Postmark
        ↓ (webhook POST)
DocBoxRX Backend
        ↓
jonE5 Classification
```
**Pros:** Simple, no credentials stored, works with any provider
**Cons:** User must set up forwarding, verification emails confusing
**Cost:** Free (CloudMailin 200/mo) or ~$0.80/1000 emails (Mailgun)

### Option 2: OAuth Integration (Gmail/Outlook)
```
User clicks "Connect Gmail"
        ↓ (OAuth flow)
Google/Microsoft grants access token
        ↓
DocBoxRX reads inbox via API
        ↓
jonE5 Classification
```
**Pros:** One-click connect, no forwarding setup
**Cons:** Only works with Gmail/Outlook, requires OAuth app setup
**Cost:** Free (API usage)
**Implementation:** ~2-4 hours

### Option 3: IMAP (Universal)
```
User enters email + password
        ↓
DocBoxRX connects via IMAP
        ↓
Fetches recent emails
        ↓
jonE5 Classification
```
**Pros:** Works with ANY email provider
**Cons:** User needs app passwords (Gmail/Yahoo), credential storage risk
**Cost:** Free
**Implementation:** ~1-2 hours

### Option 4: Email Aggregator (Nylas)
```
User clicks "Connect Email"
        ↓
Nylas handles OAuth/IMAP for all providers
        ↓
Nylas sends webhooks to DocBoxRX
        ↓
jonE5 Classification
```
**Pros:** Universal, professional UX, handles all edge cases
**Cons:** Paid service ($4-10/user/month)
**Cost:** Free tier for dev, paid for production
**Implementation:** ~1-2 hours
**Website:** https://www.nylas.com/

### Option 5: Custom Domain + Mailgun Inbound
```
inbox@docboxrx.com (your domain)
        ↓
Mailgun receives email
        ↓
Webhook to DocBoxRX
        ↓
jonE5 Classification
```
**Pros:** Professional look, reliable
**Cons:** Requires domain ownership + DNS setup
**Cost:** ~$0.80/1000 emails
**Implementation:** ~30 min (if you own domain)

---

## jonE5 AI Classifier

### Rules Engine (Primary)
Located in: `/home/ubuntu/docboxrx/docboxrx-backend/app/main.py`

**STAT triggers (0.90+ confidence):**
- Keywords: stat, urgent, critical, emergency, abnormal, positive
- Domains: labcorp.com, quest, pathology

**TODAY triggers (0.85 confidence):**
- Keywords: refill, prior auth, pharmacy, prescription
- Domains: pharmacy, cvs, walgreens

**THIS_WEEK triggers (0.75 confidence):**
- Keywords: appointment, schedule, follow-up, results

**LATER triggers (0.60 confidence):**
- Keywords: newsletter, marketing, unsubscribe, promo

### LLM Fallback (Cerebras)
- **Model:** llama-3.3-70b
- **API Key:** csk-kcphx6mm8pnfy56rn6fe3wcmhkw6wxc56jthekfvpk3fcmwt
- **Trigger:** When rules confidence < 0.70
- **Prompt:** Classifies email into STAT/TODAY/THIS_WEEK/LATER with reasoning

---

## Hosting & Infrastructure

### Frontend Hosting
- **Current:** Devin Apps (https://full-stack-apps-ah1tro24.devinapps.com)
- **Alternative:** Netlify, Vercel (free tier)

### Backend Hosting
- **Current:** Fly.io (https://app-nkizyevt.fly.dev)
- **Issue:** Auto-suspends after inactivity (cold starts)
- **Fix Options:**
  1. Pay for always-on ($5-10/mo)
  2. Keep-warm ping service (free)
  3. Move to Railway/Render

### Database
- **Current:** Neon Postgres (free tier)
- **Alternative:** Supabase, PlanetScale, Railway Postgres

---

## API Endpoints

### Auth
- POST /api/auth/register - Create account
- POST /api/auth/login - Get JWT token

### Messages
- GET /api/messages/by-zone - Get classified emails
- POST /api/messages - Add email manually
- PUT /api/messages/{id}/zone - Move to different zone (correction)
- DELETE /api/messages/{id} - Delete email

### Sources
- GET /api/sources - List email sources
- POST /api/sources - Add new source
- DELETE /api/sources/{id} - Remove source

### Inbound Email
- POST /api/inbound/cloudmailin - CloudMailin webhook
- GET /api/cloudmailin/messages - View all received emails

---

## Security Considerations

### Current
- JWT auth with 24-hour expiry
- bcrypt password hashing
- HTTPS everywhere
- No PHI stored (metadata only: sender, subject, snippet)

### For Production
- Move secrets to environment variables (not hardcoded)
- Add rate limiting
- Implement proper session management
- HIPAA compliance review if handling PHI

---

## Next Steps (Prioritized)

1. **Decide email ingestion approach** - Forwarding vs OAuth vs IMAP vs Nylas
2. **Fix cold start issue** - Keep-warm ping or always-on hosting
3. **Improve onboarding UX** - Step-by-step wizard for email setup
4. **Add voice module** - ElevenLabs/OpenAI TTS for jonE5 (future)
5. **Photo/video media manager** - Future feature

---

## Useful Links

- **Nylas (Email API):** https://www.nylas.com/
- **Mailgun Inbound:** https://www.mailgun.com/inbound-routing/
- **Postmark Inbound:** https://postmarkapp.com/inbound
- **Cerebras:** https://www.cerebras.net/
- **Neon Postgres:** https://neon.tech/
- **Fly.io:** https://fly.io/

---

## Codebase Download

The complete codebase was provided as: `docboxrx-codebase.zip`

To run locally:
```bash
# Backend
cd docboxrx-backend
poetry install
poetry run fastapi dev app/main.py

# Frontend
cd docboxrx-frontend
npm install
npm run dev
```

