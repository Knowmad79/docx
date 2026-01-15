# DocBoxRX System Specifications

This document is a contractual technical specification for the DocBoxRX platform in the
`D:\dbrx\docboxrx-final\docboxrx` workspace. It is written for auditors, clients, and
investors who require a complete, traceable description of the system.

Status: **Production-grade prototype** (local dev + hosted demo).  
Scope: Backend, frontend, AI vectorization, ingestion, routing, triage grid, and audit trail.

---

## 1) Executive Summary

DocBoxRX is a clinical inbox intelligence system that ingests inbound messages,
classifies risk and intent via LLMs and deterministic rules, and routes each message to
the correct clinical or administrative owner. It creates a **state vector** for each
message, supports triage workflows, and maintains an audit trail for compliance and review.

Key outcomes:
- Faster and safer response to high‑risk patient messages.
- Clear ownership and workflow routing.
- Structured, queryable state vectors for analytics and automation.
- Audit trail for escalation and changes.

---

## 2) System Goals and Non‑Goals

**Goals**
- Reliable ingestion of inbound messages from user input, Nylas sync, and webhooks.
- Automated vectorization into a consistent (A, O, D, R, C, L) schema.
- Deterministic triage zones (STAT/TODAY/THIS_WEEK/LATER).
- Manual escalation with immutable audit trail.
- Full local dev support via SQLite, and production-ready via PostgreSQL.

**Non‑Goals**
- Formal HIPAA certification (not claimed). The system is designed to be HIPAA‑ready,
  but legal/compliance certification is out of scope for this codebase.
- Full provider EHR integration (future scope).

---

## 3) System Architecture

**Frontend**
- React + Vite SPA (`docboxrx-frontend`)
- UI components via ShadCN UI (`src/components/ui`)

**Backend**
- FastAPI (`docboxrx-backend/app/main.py`)
- JWT auth
- Database abstraction with SQLite/Postgres (`app/db.py`)

**AI Layer**
- Cerebras Cloud LLM (`llama-3.3-70b`)
- Vectorizer prompt and parsing pipeline

**Email Integration**
- Nylas OAuth + sync API
- Optional inbound webhooks (CloudMailin, tokenized inbound)

---

## 4) Data Model (Core Tables)

### 4.1 `message_state_vectors`
State vectors are the authoritative data structure used for triage and routing.

Fields:
- `id` (TEXT, PK)
- `nylas_message_id` (TEXT)
- `grant_id` (TEXT)
- `intent_label` (TEXT) — primary intent
- `risk_score` (REAL) — 0.0–1.0
- `context_blob` (TEXT/JSON) — serialized JSON
- `summary` (TEXT)
- `current_owner_role` (TEXT)
- `deadline_at` (TEXT, ISO)
- `lifecycle_state` (TEXT)
- `is_overdue` (INTEGER)
- `created_at`, `updated_at` (TEXT)

### 4.2 `message_events`
Immutable audit log for escalations and other events.

Fields:
- `id` (TEXT, PK)
- `vector_id` (TEXT, FK)
- `event_type` (TEXT) — e.g. `ESCALATED`
- `description` (TEXT)
- `created_at` (TEXT)

### 4.3 `messages` (UI Inbox Model)
Legacy message store for the UI and action center features.

Fields (major):
- `id` (TEXT, PK)
- `user_id` (TEXT)
- `sender`, `sender_domain` (TEXT)
- `subject`, `snippet` (TEXT)
- `zone` (TEXT) — STAT/TODAY/THIS_WEEK/LATER
- `confidence`, `reason`, `jone5_message` (TEXT/REAL)
- `summary`, `recommended_action`, `action_type`, `draft_reply` (TEXT)
- `status`, `snoozed_until`, `needs_reply`, `replied_at` (TEXT/INTEGER)

### 4.4 `users`, `sources`, `nylas_grants`, `corrections`, `rule_overrides`
Supporting tables for authentication, sources, corrections, and Nylas grants.

---

## 5) Vectorization & State Vector Schema

**(A, O, D, R, C, L)**
- **A / intent:** primary action (refill, appointment, lab_result, billing, etc.)
- **O / owner:** role or department (triage_nurse, front_desk, billing_dept, lead_doctor)
- **D / deadline:** ISO timestamp (estimated if not provided)
- **R / risk:** low/medium/high/critical → mapped to score
- **C / context:** concise relevance summary
- **L / lifecycle:** new/triaged/pending_action/resolved

**Prompt Source**
- `app/services/prompts.py` defines `VECTORIZER_SYSTEM_PROMPT`.

**Vectorizer Behavior**
- `vectorize_message(raw_content)` calls Cerebras and returns:
  - `vector` (raw JSON)
  - `risk_score` (normalized 0–1)
  - `summary` (truncated context)
- Fallback `vectorize_email()` uses heuristics.

**Lifecycle Mapping**
- `new → NEEDS_REPLY`
- `triaged/pending_action → WAITING`
- `resolved → RESOLVED`

---

## 6) Ingestion Pipeline (Shadow Worker)

**Primary Entry Point**
- `POST /api/messages/ingest`

**Additional Entry Points**
- `POST /api/inbound/cloudmailin`
- `POST /api/inbound/{token}`
- `POST /api/nylas/sync/{grant_id}`

**Processing Steps**
1. Parse and normalize subject/body/sender
2. Vectorize via Cerebras
3. Route owner based on risk/intent rules
4. Persist to `message_state_vectors`
5. Optional event emission on escalation

---

## 7) Triage Grid (Decision Deck)

**Service**: `app/services/grid.py`
- Filters `lifecycle_state` in `NEEDS_REPLY`, `WAITING`, `OVERDUE`
- Uses `zone_for_message()` rules:
  - `risk_score >= 0.8` → STAT
  - deadline <= 24h → TODAY
  - deadline <= 72h → THIS_WEEK
  - else → LATER
- Aggregates counts + top 8 items per zone

**Endpoint**
- `GET /api/state/grid?owner=<role_or_grant>`

---

## 8) Escalation

**Endpoint**
- `POST /api/messages/{id}/escalate`

**Behavior**
- Sets `lifecycle_state = OVERDUE`
- Sets `current_owner_role = lead_doctor`
- Inserts `ESCALATED` event into `message_events`

---

## 9) API Surface (Core)

**Auth**
- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`

**Messages**
- `POST /api/messages/ingest`
- `GET /api/messages`
- `GET /api/messages/by-zone`
- `POST /api/messages/correct`
- `POST /api/messages/{id}/status`
- `POST /api/messages/{id}/replied`
- `POST /api/messages/{id}/escalate`

**Grid**
- `GET /api/state/grid`

**Nylas**
- `GET /api/nylas/auth-url`
- `GET /api/nylas/callback`
- `GET /api/nylas/grants`
- `DELETE /api/nylas/grants/{grant_id}`
- `POST /api/nylas/sync/{grant_id}`

**Inbound**
- `POST /api/inbound/cloudmailin`
- `POST /api/inbound/{token}`

---

## 10) Security Model

- JWT bearer tokens required for user‑scoped endpoints.
- CORS enabled for local dev in `app/main.py`.
- No PHI is intentionally logged; inbound webhook endpoints do not log request bodies.
- Data at rest: SQLite in local dev; Postgres supported for production.

**Compliance note:** This system is **not** formally certified. It is designed with
PHI‑sensitive practices in mind but requires legal/compliance review before production use.

---

## 11) Configuration / Environment

Required environment variables:
- `DATABASE_URL` (Postgres)
- `CEREBRAS_API_KEY`
- `NYLAS_API_KEY`, `NYLAS_CLIENT_ID`, `NYLAS_API_URI`, `NYLAS_CALLBACK_URI`

SQLite dev DB path is pinned to:
`docboxrx-backend/docboxrx.db`

---

## 12) Deployment & Operations

**Local Dev**
- Backend: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- Frontend: `npm run dev` on port 5173/5174

**Production**
- Use Postgres via `DATABASE_URL`
- Configure secrets via environment manager
- Run behind HTTPS / WAF / reverse proxy

---

## 13) Observability

Current logging:
- Standard stdout logging in FastAPI
- Vectorizer logs failures via Python logging

Recommended upgrades:
- Structured logs + trace IDs
- Metrics: latency, ingestion volume, escalation rate

---

## 14) Validation & Test Flows

**Vectorization Test**
- Send an email via `/api/messages/ingest`
- Confirm row in `message_state_vectors`

**Grid Test**
- `GET /api/state/grid?owner=lead_doctor`

**Escalation Test**
- `POST /api/messages/{id}/escalate`
- Confirm `message_events` insert

---

## 15) Risks & Mitigations

- **LLM variability:** use `response_format=json_object` + strict parsing.
- **PHI handling:** avoid logging bodies, use access control in production.
- **Latency:** async ingestion, limit tokens, pre‑cache vectors.
- **Vendor dependency:** fallback to heuristic vectorization.

---

## 16) Roadmap (Proposed)

1. HIPAA compliance review + audit logging enhancements
2. Role‑based access control for multi‑user clinics
3. Escalation automation (SMS/email)
4. Vector quality monitoring + drift detection
5. EHR integrations (FHIR/HL7)

---

## 17) Acceptance Criteria (Current Contract)

- Incoming emails are vectorized into (A,O,D,R,C,L).
- Decision Deck shows accurate counts and previews.
- Manual escalation updates lifecycle and emits audit event.
- All endpoints function locally in dev environment.
