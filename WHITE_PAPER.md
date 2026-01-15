# DocBoxRX White Paper (Draft)

Version: 0.1 (Draft)  
Audience: Contract stakeholders, investors, clinical partners  
Prepared for: DocBoxRX

---

## 1) Executive Summary

DocBoxRX is a clinical inbox intelligence system built to reduce risk, delay, and
administrative burden in medical practices. It ingests inbound messages, structures them
into machine‑readable state vectors, and routes them to the correct owner with a clear
triage priority. The platform enables faster response to high‑risk messages, reliable
audit trails for escalation, and measurable improvements in operational efficiency.

---

## 2) Problem Statement

Clinical practices face three chronic problems in message workflows:

1. **Urgency ambiguity** — critical messages are buried in high volume inboxes.
2. **Ownership uncertainty** — unclear routing leads to delays and dropped tasks.
3. **Lack of auditability** — escalations and decisions are often undocumented.

DocBoxRX addresses these issues by creating a structured “state vector” per message and
by enforcing deterministic routing and escalation logic.

---

## 3) Solution Overview

DocBoxRX combines:
- **AI vectorization** of free‑text messages into structured (A,O,D,R,C,L) outputs.
- **Deterministic triage grid** based on risk and deadlines.
- **Role‑based routing** to clinical or administrative owners.
- **Audit events** for escalation and lifecycle changes.

The system is designed to operate locally (SQLite) and in production (PostgreSQL).

---

## 4) Core Innovation: The State Vector

Each incoming message is transformed into a vector:

**(A, O, D, R, C, L)**
- **A / Intent** — primary action (refill, appointment, lab_result, billing).
- **O / Owner** — responsible role (triage_nurse, front_desk, billing_dept).
- **D / Deadline** — ISO timestamp for required response.
- **R / Risk** — low/medium/high/critical (normalized to numeric score).
- **C / Context** — concise summary of relevance.
- **L / Lifecycle** — new/triaged/pending_action/resolved.

The vector becomes the canonical record powering triage, routing, and analytics.

---

## 5) Product Capabilities

**Decision Deck (Triage Grid)**
- Four zones: STAT, TODAY, THIS_WEEK, LATER.
- Sorted by urgency, risk, and deadlines.
- Preview items with key fields.

**Manual Escalation**
- Single‑click escalation to lead doctor.
- Immutable audit event logged.

**Auditability**
- Each escalation and lifecycle change recorded.
- Enables compliance review and risk tracking.

---

## 6) Architecture Summary

**Frontend**
- React + Vite
- Decision Deck, inbox view, action center, modals

**Backend**
- FastAPI API services
- JWT auth
- Ingestion pipelines

**AI Layer**
- Cerebras Llama‑3.3‑70b
- Structured JSON output using prompt enforcement

**Integrations**
- Nylas OAuth and sync
- Webhook inbound endpoints

---

## 7) Competitive Differentiation

DocBoxRX does not just classify priority. It produces a structured, actionable vector and
routes ownership with auditability. This enables:

- Consistent triage behavior across teams
- Automated escalation pipelines
- Data‑driven staffing and performance analysis

---

## 8) Compliance & Risk Posture

Design principles:
- No logging of message bodies in webhook handlers
- Role‑based access control (JWT)
- Clear audit trail for escalations

Compliance certification (HIPAA, SOC2) is not claimed at this stage. The architecture is
designed to support such compliance after formal audit.

---

## 9) Roadmap

1. **Automation** — escalation alerts via SMS/email
2. **Role‑based workflow policies**
3. **Analytics dashboard** (response time, escalations, SLA adherence)
4. **EHR integration** (FHIR/HL7)
5. **Compliance hardening** (audit trails, retention policies)

---

## 10) Business Model (Example)

- **Per‑provider subscription**
- **Enterprise tiers** for multi‑clinic networks
- Add‑ons: automation, integrations, analytics

---

## 11) KPI Targets

Suggested metrics for pilots:
- Time‑to‑triage reduction (%)
- Escalation response time (minutes)
- Missed/late response incidents (count)
- Inbox volume handled per staff per day

---

## 12) Contract Acceptance Criteria

- State vectors created for every ingestion path.
- Decision Deck shows STAT/TODAY/THIS_WEEK/LATER in real time.
- Manual escalation updates lifecycle state and writes audit event.
- Local dev and production ready deployment paths validated.

---

## 13) Glossary

- **State Vector:** Structured representation of a message.
- **Decision Deck:** Triage grid UI for priority routing.
- **Lifecycle State:** Message workflow state (new, waiting, resolved).

---

## 14) Appendix: Technical Specs

See `SYSTEM_SPECS.md` for detailed implementation and API definitions.
