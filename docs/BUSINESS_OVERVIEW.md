# ASHOS — Business Overview

**AI Smart Hotel Operating System**
Document version 1.0 · August 2026 · Owner: ai@dbl-digital.com

This document explains what ASHOS is, who it is for, and what it does for a hotel
that runs it. For architecture and implementation see
[TECHNICAL_ARCHITECTURE.md](TECHNICAL_ARCHITECTURE.md); for the binding scope and
phase contract see [`goal.txt`](../goal.txt).

---

## 1. The problem

A small or mid-sized hotel runs its front desk on three things: a paper register, a
spreadsheet, and one person who remembers everything. That arrangement fails in
predictable ways.

| Failure | What it costs |
|---|---|
| The desk is unattended at 02:00 | A walk-in guest leaves for the hotel next door |
| Two staff sell the same room | An arriving guest stands in a corridor at midnight |
| Rates live in someone's head | The same room is quoted three different prices in a week |
| Nobody speaks the guest's language | A foreign visitor cannot ask about breakfast |
| The register is the only ledger | No occupancy trend, no revenue report, no audit trail |
| Property management software is priced per room, in dollars | A 30-room hotel cannot justify it |

The gap is not "hotels need software". Property management systems exist. The gap is
that the software a small hotel can afford does not answer the guest, and the
software that answers the guest is priced for a chain.

## 2. What ASHOS is

An AI receptionist that can actually take a booking, with a complete property
management system behind it.

The distinction matters commercially. A chatbot bolted onto a booking form is a
deflection tool: it answers questions and hands the work back to a human. ASHOS's
assistant holds a conversation, checks real availability, quotes the real price from
the same pricing engine the front desk uses, and writes a real reservation against a
database constraint that makes double-booking impossible.

Three surfaces, one system:

- **Lobby terminal** — a full-screen kiosk with an avatar receptionist. The guest
  walks up and talks; it greets, listens and answers from the first second. No login,
  no app, no queue.
- **Public booking page** — the same assistant on the hotel's own website, taking
  bookings from guests at home with no staff watching the conversation.
- **Staff console** — the property management system: rooms, reservations, guests,
  folios, invoices, payments, roles, AI control plane, dashboard.

The assistant speaks **Bangla and English**, switches mid-conversation when the guest
does, and reads its answers aloud in a female voice by default.

## 3. Who it is for

| Buyer | Why they buy |
|---|---|
| **Independent hotels, 20–150 rooms** (initial market: Bangladesh) | Front-desk cover 24/7 without 24/7 staffing; bookings taken in Bangla |
| **Small groups, 2–10 properties** | One system, per-property configuration, tenant isolation from day one |
| **Guesthouses and resorts in tourist areas** | Foreign guests get answered in English; the property keeps its own voice and branding |

Not for: chains needing channel-manager and revenue-management integrations, or
properties whose requirement is accounting software. Both are explicitly out of scope
(`goal.txt` §2.2).

## 4. What a hotel gets, in operational terms

### 4.1 The guest side

- **Answers at any hour.** Room availability, prices, check-in and check-out times,
  payment terms, address, contact details — all read from the live database, never
  invented. Every answer the assistant gives is sourced from the hotel's own record;
  if the record does not contain it, the assistant says so rather than guessing.
- **A booking taken end to end.** Dates, nights, room type, name, phone, confirmation
  — with the reference number issued by the same code path the front desk uses.
- **No dead ends.** If the guest goes quiet mid-booking, the assistant asks the next
  question it still needs. On the website there is no "please wait for staff", because
  no staff are watching that page; the assistant finishes the job or gives the desk's
  telephone number.
- **A guest who prefers a human gets one** — in the lobby, one tap raises the desk's
  handoff queue.

### 4.2 The desk side

- **Rooms and inventory** — room types, individual rooms, rate plans, photographs,
  seasonal and day-of-week pricing.
- **Reservations** — availability by type and date, allocation, status, source
  attribution (kiosk vs website vs desk), with a database exclusion constraint as the
  final arbiter of "is this room free".
- **Guests** — one profile per person across stays, documents, and an explicit consent
  ledger for anything biometric.
- **Billing** — folio per stay, itemised lines, invoices, payments, and a business date
  rolled by the night audit, because a hotel day does not end at midnight.
- **Roles and permissions** — five seeded roles from superadmin to AI reception; module
  visibility and URL access use the same permission, so a role that cannot see a module
  cannot reach it by typing the URL either.
- **AI Center** — the control plane. Model configuration, prompt versions with
  rollback, safety policy, spend caps, and a kill switch. AI behaviour changes without
  a code deployment.
- **Dashboard** — occupancy, arrivals, departures, revenue and AI usage.

### 4.3 What it costs the operator to run

- Runs on one Docker host. Postgres, Redis, object storage, web, worker and Nginx are
  all in one compose file.
- **AI spend is capped per property, per day**, and metered per conversation. A daily
  cost cap and a per-session token cap are configuration, not code.
- **The AI can be switched off entirely** and the hotel keeps selling rooms: the
  booking page keeps a deterministic form, and the assistant keeps a keyword answerer
  that reads from the same database. A hotel whose website stops selling rooms when an
  API token expires has bought a liability, so that case is designed for rather than
  hoped against.

## 5. Commercial positioning

**Deployment model.** Single-tenant install per property or per group, self-hosted or
hosted for the customer. The schema carries `tenant_id` on every table from day one,
so a multi-property SaaS deployment is a configuration and operations exercise rather
than a rewrite.

**Pricing model.** Not fixed in this document. The cost structure that pricing has to
cover is: hosting (one modest VM per property or shared), AI provider spend (capped
and metered per property), and support. AI spend is the only variable that scales with
guest volume, and it is bounded by configuration rather than by usage.

**Differentiation.** Three things are unusual, and all three are deliberate:

1. **The assistant transacts.** It does not "help you find the booking page"; it holds
   the room. Availability and price come from the same services the desk uses, so a
   kiosk quote and a folio charge cannot diverge.
2. **The AI is never load-bearing for revenue.** Every guest-facing path has a
   deterministic fallback, and the AI is one switch away from off.
3. **It is bilingual as a first-class property, not a translation layer.** Every
   guest-facing string is served in the guest's language, the speech recogniser is
   retuned when the guest switches, and the language chip exists precisely because
   speech recognition listens in one language at a time.

**Honest limits.** A guest cannot pay online yet — payment is settled at the desk, or
by an advance to the property's mobile wallet where the property has configured one.
Face recognition, document OCR and object detection are specified and deferred
(Phase 2–3, `goal.txt` §5); nothing in the product pretends they are live. No door
locks, no payroll, no full accounting.

## 6. Compliance and trust posture

For a product that puts a camera and a microphone in a hotel lobby, the trust
decisions are product decisions:

- **Nothing is charged by the AI.** The assistant can create a held booking with an
  open folio; it never moves money. Payment is a human action at the desk, or a
  gateway webhook when one is integrated.
- **No camera before consent.** The lobby terminal opens no camera at page load. Face
  capture is reachable only after a confirmed booking, only if the property has
  switched it on, and only after an explicit yes on a consent screen. A refusal is
  recorded, so the terminal does not ask twice.
- **An always-open microphone is stated, not hidden.** Hands-free listening is a
  property switch, it runs inside an active conversation, and closing the tab closes
  the microphone.
- **Biometrics stay off until legal sign-off is recorded** — the field exists, the
  default is off, and the admin says why.
- **Secrets never enter version control**, and the guest-facing surfaces never show
  internal identifiers, reference numbers or technical metadata.

## 7. Delivery status

Measured, not estimated. As of this document:

| | |
|---|---|
| Application code | ~28,500 lines of Python (excluding migrations), ~5,400 lines of JS/CSS |
| Automated tests | **778 passing** across 29 test modules |
| Database schema | 30 migrations across 10 modelled apps |
| Live modules | AI Reception (kiosk · website · console), Rooms & Inventory, Reservations, Guests, Billing, Accounts & RBAC, Tenants, AI Center, Dashboard |
| Specified and deferred | Housekeeping, Restaurant & POS, RAG knowledge base, Vector search, Notifications, Vision (face · OCR) |

Deferred modules are not blank menu items: each one renders its own roadmap in the
product, so a manager clicking Housekeeping learns what it will do and in which phase,
rather than finding a dead link and concluding the software is broken.

The phase plan, exit criteria and definition of done are in [`goal.txt`](../goal.txt)
§4–§5. The success test stated there is deliberately operational rather than
feature-based:

> A pilot hotel runs ASHOS in parallel with their paper register for one month and
> then throws the register away.

## 8. Risks, stated plainly

| Risk | Mitigation in the product today |
|---|---|
| AI provider outage, expired key, budget exhausted | Deterministic offline answerer, direct booking form, kill switch, per-property caps |
| A model inventing a price or a policy | The model never sees a price it can echo as fact; prices are recomputed server-side and stock is re-checked at the moment of writing |
| A model promising something the hotel cannot do | Outbound guardrails; on the website, any promise of a human is replaced before it reaches the guest |
| Double-booking under concurrency | Database exclusion constraint is the final arbiter, inside a transaction |
| Guest privacy complaint | Consent-gated capture, recorded refusals, no camera before a word is exchanged |
| Runaway AI cost | Daily cost cap, per-session token cap, per-turn metering |
| Speech recognition failing for a guest | Typing always works; the language chip exists for exactly the case where the guest cannot be heard asking to switch |

---

## Appendix — glossary for non-technical readers

| Term | Meaning here |
|---|---|
| **PMS** | Property Management System — the hotel's operational software: rooms, bookings, folios |
| **Folio** | The running bill for one stay |
| **Business date** | The hotel's own day, rolled by the night audit; charges at 02:00 belong to the previous day |
| **RAG** | Retrieval-augmented generation — the assistant answers from retrieved hotel records rather than from model memory |
| **Handoff** | The AI passing a conversation to a member of staff, with a queue the desk can see |
| **Tenant** | One hotel property in the data model |
| **Kiosk / lobby terminal** | The full-screen assistant screen in the hotel lobby |
