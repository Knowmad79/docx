# DocBoxRX Architecture Roadmap

## Quick Fix Needed (60 seconds)

The app is working. The ONLY blocker is Nylas needs this redirect URI registered:

```
https://app-nkizyevt.fly.dev/api/nylas/callback
```

**Add it via Nylas API (copy-paste this):**
```bash
curl --request POST \
  --url 'https://api.us.nylas.com/v3/applications/redirect-uris' \
  --header 'Authorization: Bearer nyk_v0_lPt52DfSYzutwat78WlItFejHHj2MyyZQPm1pHYQcmHO5gDWb6pIAwTanwZpHhkM' \
  --header 'Content-Type: application/json' \
  --data '{
    "platform": "web",
    "url": "https://app-nkizyevt.fly.dev/api/nylas/callback"
  }'
```

---

## System Architecture

### URLs
- **Frontend:** https://full-stack-apps-ah1tro24.devinapps.com
- **Backend API:** https://app-nkizyevt.fly.dev
- **Database:** Neon Postgres (persistent)

### Tech Stack
- **Frontend:** React + TypeScript + Vite + Tailwind CSS + shadcn/ui
- **Backend:** FastAPI (Python) on Fly.io
- **Database:** PostgreSQL (Neon)
- **AI Classifier:** Cerebras llama-3.3-70b
- **Email Integration:** Nylas API (OAuth)

---

## Environment Variables (Backend)

```
DATABASE_URL=postgresql://neondb_owner:npg_Z60uvbwqlBzk@ep-mute-hill-adb7l32q-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require
CEREBRAS_API_KEY=csk-kcphx6mm8pnfy56rn6fe3wcmhkw6wxc56jthekfvpk3fcmwt
NYLAS_API_KEY=nyk_v0_lPt52DfSYzutwat78WlItFejHHj2MyyZQPm1pHYQcmHO5gDWb6pIAwTanwZpHhkM
NYLAS_CLIENT_ID=6fbc70bb-1527-4df7-8801-fe240c1d5aab
NYLAS_CALLBACK_URI=https://app-nkizyevt.fly.dev/api/nylas/callback
JWT_SECRET=docboxrx-secret-key-change-in-production
```

---

## Database Tables

### users
- id (UUID, PK)
- email (unique)
- password_hash
- name
- practice_name
- created_at

### messages
- id (UUID, PK)
- user_id (FK -> users)
- sender, sender_domain, subject, snippet
- zone (STAT/TODAY/THIS_WEEK/LATER)
- confidence, reason, jone5_message
- received_at, classified_at
- corrected (boolean)

### nylas_grants
- id (UUID, PK)
- user_id (FK -> users)
- grant_id (Nylas grant ID)
- email
- provider
- created_at, last_sync_at

---

## API Endpoints

### Auth
- `POST /api/auth/register` - Create account
- `POST /api/auth/login` - Login, returns JWT

### Messages
- `GET /api/messages/by-zone` - Get all messages grouped by zone
- `POST /api/messages/ingest` - Classify email (manual paste)
- `POST /api/messages/correct` - Move message to different zone
- `DELETE /api/messages/{id}` - Delete message

### Nylas (Email Integration)
- `GET /api/nylas/auth-url?provider=google` - Get OAuth URL (provider: google/microsoft)
- `GET /api/nylas/callback` - OAuth callback (Nylas redirects here)
- `GET /api/nylas/grants` - List connected email accounts
- `POST /api/nylas/sync/{grant_id}` - Sync emails from connected account
- `DELETE /api/nylas/grants/{grant_id}` - Disconnect email account

### Demo
- `POST /api/demo/seed` - Load demo data

---

## OAuth Flow (Nylas)

```
1. User clicks "Connect Gmail" in frontend
2. Frontend calls GET /api/nylas/auth-url?provider=google
3. Backend generates Nylas auth URL with provider=google
4. User redirected to Nylas -> Google OAuth
5. After auth, Nylas redirects to /api/nylas/callback
6. Backend exchanges code for grant, stores in nylas_grants table
7. User redirected back to frontend with success
8. User clicks "Sync" to pull emails
9. Backend fetches emails via Nylas API, classifies with jonE5
10. Classified emails appear in triage zones
```

---

## jonE5 Classifier Logic

1. **Rules-first** - Check keywords/domains for high-confidence classification
   - STAT: "urgent", "critical", "stat", "emergency", lab domains
   - TODAY: "today", "asap", "appointment", scheduling domains
   - THIS_WEEK: "follow-up", "review", insurance domains
   - LATER: "newsletter", "marketing", promotional domains

2. **LLM fallback** - If rules confidence < 0.70, use Cerebras llama-3.3-70b

3. **Learning** - User corrections stored to improve future classifications

---

## File Structure

```
/home/ubuntu/docboxrx/
├── docboxrx-backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app, all endpoints
│   │   ├── database.py      # SQLAlchemy models
│   │   └── db.py            # Database connection
│   ├── .env                 # Environment variables
│   ├── fly.toml             # Fly.io config
│   └── pyproject.toml       # Python dependencies
│
└── docboxrx-frontend/
    ├── src/
    │   ├── App.tsx          # Main React component
    │   ├── App.css          # Styles
    │   └── components/ui/   # shadcn/ui components
    ├── .env                 # VITE_API_URL
    └── dist/                # Built frontend (deployed)
```

---

## Troubleshooting

### "redirect_uri is not allowed" error
- Add exact URI to Nylas: `https://app-nkizyevt.fly.dev/api/nylas/callback`

### Login times out / slow
- Fly.io cold start (5-15 sec). Wait and retry.

### Data lost after restart
- Check DATABASE_URL is set correctly in Fly.io secrets

### IMAP screen instead of Google OAuth
- Ensure `provider=google` is passed to auth URL (already fixed)

---

## Plan B: Gmail API Direct (if Nylas doesn't work)

If Nylas continues to have issues, alternative is direct Gmail API:
1. Create Google Cloud project
2. Enable Gmail API
3. Create OAuth 2.0 credentials
4. Implement token refresh in backend
5. Use gmail.users.messages.list/get endpoints

This is more work but removes Nylas dependency.
