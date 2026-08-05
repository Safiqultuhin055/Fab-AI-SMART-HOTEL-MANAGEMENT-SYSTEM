# ASHOS — Technical Architecture

**AI Smart Hotel Operating System**
Document version 1.0 · August 2026 · Owner: ai@dbl-digital.com

Companion to [BUSINESS_OVERVIEW.md](BUSINESS_OVERVIEW.md). The binding scope, locked
technical decisions (referenced below as `D01`, `R6`, …) and phase plan live in
[`goal.txt`](../goal.txt); this document describes what is built and why it is shaped
the way it is. Setup instructions are in the [README](../README.md).

---

## 1. Shape of the system

```
                    ┌─────────────────────────────────────────────┐
   lobby terminal   │  templates + vanilla ES6 (no build step)    │
   /reception/kiosk │  kiosk.js · kiosk-devices.js · kiosk-enrol  │
                    └───────────────┬─────────────────────────────┘
   booking page     ┌───────────────▼─────────────────────────────┐
   /book/           │  Django views (server-rendered pages)       │
                    │  DRF API  /api/v1/…  (JWT for staff,        │
   staff console    │            session-scoped for the kiosk)    │
   /reception/ etc. └───────────────┬─────────────────────────────┘
                                    │
                    ┌───────────────▼─────────────────────────────┐
                    │  services/   — all business rules           │
                    │  reception · booking · billing · rooms ·    │
                    │  ai · vision · analytics                    │
                    └───────────────┬─────────────────────────────┘
                                    │
                    ┌───────────────▼─────────────────────────────┐
                    │  apps/       — models, admin, migrations    │
                    │  PostgreSQL 16 + pgvector · Redis · MinIO   │
                    └─────────────────────────────────────────────┘
                                    │
                    ┌───────────────▼─────────────────────────────┐
                    │  Celery worker + beat (default·ai·vision·   │
                    │  periodic queues)                           │
                    └─────────────────────────────────────────────┘
```

**The rule that keeps it honest:** a view parses, delegates and serialises. Any
conversation rule, price, availability check or write found in a view is in the wrong
place. That is why the orchestrator, the booking agent and the pricing engine are all
importable from a test, an HTTP endpoint and a future WebSocket consumer without
change.

### 1.1 Layout

| Path | Contains |
|---|---|
| `apps/` | Django apps: models, admin, migrations, views, app-local API |
| `api/v1/` | Versioned DRF surface: URLs, serialisers, shared views |
| `services/` | Business logic. No HTTP, no request objects, no templates |
| `config/settings/` | `base` · `dev` · `prod` · `test` |
| `templates/` | Server-rendered pages and the kiosk widget partials |
| `static/js`, `static/css` | Vanilla ES6 and CSS. No bundler, no framework (`D`: goal.txt §2.1) |
| `tests/` | 29 modules, 778 tests: integration-first, plus an `ai_eval` marker |
| `deploy/` | Docker Compose, Nginx, entrypoints |

### 1.2 Stack

| Layer | Choice | Why this one |
|---|---|---|
| Python | 3.12 | Pinned; see `goal.txt` D01 |
| Web | Django 5.2 · DRF · Channels (ASGI, Daphne) | Server-rendered pages plus a versioned API from one codebase |
| Data | PostgreSQL 16 + pgvector (HNSW) | Vectors live in the same ACID database as the bookings they describe |
| Cache / broker | Redis | Channels layer and Celery broker |
| Async | Celery + Beat, queues `default` `ai` `vision` `periodic` | An AI call must never block a request-response cycle |
| Storage | MinIO / S3-compatible | Room photos, documents, uploads |
| Frontend | Django templates · Bootstrap 5 · vanilla ES6 | A lobby terminal must boot with no build pipeline to break |
| AI | Any OpenAI-compatible LLM · Whisper · TTS · Sentence Transformers · CLIP · InsightFace · PaddleOCR | Provider-agnostic behind an adapter |

## 2. Multi-tenancy

Every domain table carries `tenant_id` referencing `tenants.Hotel`. Managers are
tenant-scoped by default (`objects`), with an explicit escape hatch (`all_objects`)
used only where a query legitimately crosses properties.

The tenant is resolved by middleware from, in order: the authenticated user's default
membership, an `X-Hotel-Code` header, or `?hotel=<CODE>` on public pages. Public
surfaces — the lobby kiosk and the booking page — carry no login at all, so the query
string and the header are the only ways in, and the body is never trusted for it.

The UI operates one property at a time. The schema does not, which is what makes a
SaaS deployment an operations exercise rather than a migration.

## 3. The AI reception pipeline

One turn, end to end (`services/reception/orchestrator.py`):

```
guest text (or speech)
  ├─ language detection / pinned language        services/reception/language.py
  ├─ inbound guardrails                          guardrails.check_inbound()
  │     empty · too long · asked for a human · blocked topic · turn cap · token cap
  ├─ booking mode?                               booking_agent.run_turn()
  │     structured JSON draft, validated and re-priced server-side
  ├─ context assembly                            context.retrieve()
  │     hotel facts + live room availability + payment terms, numbered [1]…[n]
  ├─ prompt                                      AI Center version, or the inline fallback
  ├─ model call                                  services/ai/gateway.chat()  (metered, capped)
  ├─ outbound guardrails                         non-answer detection · confidence · promise-of-human
  ├─ persist                                     Message + token/cost accounting
  └─ escalate, or self-serve                     handoff queue, or answer + next question
```

### 3.1 What the model is and is not allowed to decide

| The model owns | The server owns |
|---|---|
| Language, intent, what the guest meant, what to ask next | Dates, availability, occupancy limits, prices, the write |

The booking agent returns a *reply* plus the **complete current draft** every turn. The
server merges that draft into what it already held (a model that forgets a field cannot
erase it), then re-validates every part of it: a date in the past is dropped, a room
type that does not exist is dropped, a party that exceeds occupancy is dropped, stock is
re-counted **for the dates the guest just named** rather than for the dates the snapshot
was built for, and the price is recomputed through `services/rooms/pricing`.

`ready_to_confirm` from the model is a request, never authority. The decision is made in
`booking_agent.confirm()`, inside a transaction, where a Postgres exclusion constraint on
`ReservationRoom` has the last word. The worst concurrent case is a clean `Conflict` and
the assistant offering another room — never two guests holding one room.

### 3.2 Grounding and citations

The context block is numbered, and the prompt requires `[1]`, `[2]` markers. Those
markers are how the server knows whether an answer was sourced at all:
`context.citations()` records which facts were used and `guardrails.confidence_of()`
scores the answer from them.

They are **stripped before the guest sees them** (`services/reception/redact.py`,
applied once at the HTTP boundary). The record keeps them — "which fact did that answer
come from" is a question an operator must be able to answer later — while the guest
reads a sentence a receptionist could have said. The same redaction removes internal
identifiers, chunk/vector ids and bare UUIDs; room numbers, telephone numbers and email
addresses are deliberately preserved.

### 3.3 Guardrails

| Check | Trigger | Result |
|---|---|---|
| `HUMAN_REQUEST` | Guest asks for a person, in either language | Handoff (staffed channels) or an honest self-serve answer |
| Blocked topics | Per-property `SafetyPolicy` | Not answered by the AI |
| Turn / token caps | Per-conversation | Bounded spend, bounded conversation |
| `NON_ANSWER` | The model dodged the question | Escalate; confidence floored |
| `PROMISES_HUMAN` | The model claims somebody is coming | On a self-serve channel the wording is discarded before it is recorded |
| Repeated question | The guest asked the same thing twice | Escalate rather than repeat a useless answer |

### 3.4 Channels, and what changes between them

| Channel | Surface | Camera | Standing mic | Staff reachable in-chat |
|---|---|---|---|---|
| `kiosk` | Lobby terminal | Consent-gated, post-booking only | Yes (property switch) | Yes — handoff queue |
| `website` | `/book/` | Never | Yes (property switch) | **No** — nobody watches it |
| `web` | Staff console widget | Never | No | Yes |

The `website` channel is *self-serve*: every escalation path answers instead of
queueing, gives the reception telephone number, and asks the next question needed to
finish the booking. A queue full of items nobody can claim would teach the desk to
ignore the queue, including the lobby items that are real.

### 3.5 Voice

Two engines per direction, in preference order — a configured provider first, the
browser's own Web Speech engines when there is no key. Requiring a paid key before a
guest can talk to a lobby terminal was the wrong trade.

One rule governs the microphone: **one mouth at a time.** Browser speech recognition
captures the same device the speaker feeds, so the microphone is closed before a word is
spoken and reopens the moment the speech ends. It is a mute, not a stand-down: the
hands-free loop stays armed, so nothing has to be tapped.

Hardening that took three separate fixes, all of them found by sampling the live page
over time rather than by reading the code:

- Transient recogniser errors (`network`, `audio-capture`) back off with a capped delay
  **forever**. They used to accumulate against a per-session cap of six and retire the
  microphone permanently.
- A dropped utterance cannot hang a turn: `speechSynthesis` is polled, because Chrome
  will drop an utterance mid-sentence without firing `onend` or `onerror`, and the
  microphone reopens in that promise's `.then()`.
- A failed request reopens the microphone in its `finally`, and a 3-second watchdog
  reopens it whenever it should be open and is not — respecting only the states where it
  is *meant* to be shut.

### 3.6 Cost control

`services/ai/gateway` is the only path to a provider. It meters every call (tokens,
latency, cost), enforces a per-property daily cost cap and a per-session token cap,
records provider failures against the configuration that caused them, and honours the
kill switch. AI Center renders all of it, and prompt versions are rollback-able without
a deployment.

## 4. Data model, by app

| App | Models | Notes |
|---|---|---|
| `tenants` | 5 | `Hotel` is the tenant root: branding, kiosk behaviour, finance and payment terms |
| `accounts` | 6 | Email-as-username, roles, permissions, memberships |
| `rooms` | 9 | Room types, rooms, rate plans, photos, seasonal and day-of-week pricing |
| `booking` | 5 | Reservations, allocations, sources; exclusion constraint on room-nights |
| `billing` | 9 | Folio, folio lines, invoices, payments, business date |
| `guests` | 7 | Profiles, documents, consent ledger |
| `reception` | 9 | Conversations, messages, handoffs, channels, modes |
| `ai_center` | 7 | Model configs, prompt templates and versions, safety policy, usage |
| `core` | 7 | Base models, tenancy, soft delete, module plan |
| `vision` | 2 | Enrolment records; capture pipeline is phased |

Cross-cutting conventions: soft delete (`is_deleted`) with tenant-scoped default
managers, UUIDv7 primary keys (time-ordered, so an index does not fragment the way
random UUIDs do), `created_at` / `updated_at` on everything, and money as `Decimal` —
never float.

## 5. API surface

`/api/v1/` — OpenAPI schema at `/api/schema/`, browsable docs at `/api/docs/`.

| Group | Endpoints |
|---|---|
| Auth | `auth/token/` · `refresh/` · `verify/` · `logout/` · `me/` |
| Health | `health/` · `ai/health/` · `ai/config/` |
| Reception | `reception/conversations/` · `chat/` · `voice/` · `speak/` · `nudge/` · `handoff/` · `queue/` |
| Domain | `rooms/` · `reservations/` · `reception/` (staff-side resources) |

Staff endpoints use JWT and a permission class per resource. The public kiosk endpoints
accept a **session** rather than a login, are throttled per scope (`ai_chat`,
`ai_voice`), and can only continue the conversation their own session started — a guest
cannot enumerate UUIDs and read somebody else's check-in chat.

## 6. Frontend

No build step, on purpose: a lobby terminal that needs a bundler to boot is a lobby
terminal that eventually will not.

- `static/js/kiosk.js` (~2,000 lines) — the whole assistant client: state machine,
  bubbles, hands-free loop, TTS, booking card, room gallery, silence timer.
- `static/js/kiosk-devices.js` — camera/microphone/speaker selection for a fixed lobby
  rig, persisted per property. Absent by design on the public booking page, where the
  guest has one microphone and has already chosen it.
- `static/js/kiosk-enrol.js` — the post-booking photo consent flow.
- Server state reaches the client as `data-*` attributes on one element; the script
  never guesses whether AI, voice, a camera or staff are available.
- Every guest-facing string is served by the server in the guest's language, both
  languages at once, so tapping the language chip relabels the screen inside the same
  second with no English flash.

## 7. Security

- **Secrets** — environment only, never in git (`goal.txt` D15). `.env.example`
  documents every variable; the real `.env` is ignored, as are the local run notes and
  the account dump.
- **RBAC** — sidebar visibility and URL access use the same `core.access_*` permission,
  so a role that cannot see a module gets a 403 rather than a blank page.
- **Tenant isolation** — enforced in managers, with tenant-crossing queries requiring an
  explicit manager. Covered by dedicated tests.
- **Prompt injection** — guest text and retrieved context are separately delimited, and
  the prompt states that both are data and never instructions (`goal.txt` §13.2).
- **Public endpoints** — throttled, session-scoped, and reachable only for their own
  conversation.
- **Payments** — no guest-facing surface moves money (`goal.txt` D11). `Payment` carries
  `provider`, `provider_ref` and `idempotency_key` so a gateway becomes an adapter plus a
  webhook, and a retried callback cannot take the money twice (`D16`).
- **Biometrics** — off by default, gated on recorded legal sign-off (`R1`), consent
  captured per guest with refusals recorded.

## 8. Testing

```bash
make test                     # or: .venv/Scripts/python -m pytest -q
pytest -m "not ai_eval"       # skip the marker that needs a live provider
```

**778 tests, 29 modules**, integration-first: they drive real URLs, a real database and
the real services rather than mocking the layer under test. Notable habits in this suite:

- Behaviour that cost real debugging time is pinned with the reason written into the
  test's docstring, so the next person changing it learns what it protects.
- Guest-facing copy is asserted in **both languages**, because a half-translated screen
  is a bug, not a cosmetic issue.
- Client-side guarantees are asserted against the JavaScript source, since there is no
  JS test runner in the stack.
- CI (`.github/workflows/ci.yml`) runs ruff, black, advisory mypy, and the suite against
  `pgvector/pgvector:pg16` — the production image, because testing against plain
  Postgres would let a broken vector migration reach `main`.

## 9. Deployment and operations

`deploy/compose.yml` brings up: `postgres` (pgvector), `redis`, `minio` +
bucket bootstrap, `web` (Daphne/ASGI), `worker`, `beat`, `nginx`. Volumes for database,
Redis, object storage, media, static and AI model files.

```bash
cp .env.example .env      # set DJANGO_SECRET_KEY and AI_LLM_API_KEY
docker compose -f deploy/compose.yml --env-file .env up -d --build
docker compose -f deploy/compose.yml exec web python manage.py createsuperuser
docker compose -f deploy/compose.yml exec web python manage.py bootstrap_hotel
```

Without Docker: `make setup && make env && make migrate seed && make run`. `manage.py`
re-executes itself under `.venv` when started with another interpreter, so
`py manage.py runserver` works without activating anything first.

**Operational notes worth knowing:**

- Health: `/api/v1/health/` for the stack, `/api/v1/ai/health/` for provider posture.
- The AI kill switch and per-property daily cost cap are the two levers to reach for
  first when spend or behaviour surprises you.
- Prompt versions are rollback-able from AI Center without a deployment.
- Template and static edits need a dev-server restart on Windows; Python edits reload.

## 10. Deliberately not built

Named here so nobody has to read the code to find out:

| Not built | Status |
|---|---|
| Online payment capture (cards, bKash, Nagad) | Terms and the `Payment` seam exist; a gateway is Phase 2+. A "Pay now" button that cannot charge is worse than none |
| Face recognition, document OCR, object detection | Specified, phased (P2–P3), consent framework already in place |
| Housekeeping, Restaurant & POS, RAG knowledge base, vector search, notifications | Phased; each renders its own roadmap in the product |
| Channel manager, revenue management, payroll, full accounting, door locks | Out of scope (`goal.txt` §2.2) |

---

## Appendix — where to look first

| Question | File |
|---|---|
| How does one AI turn work? | `services/reception/orchestrator.py` |
| How is a booking validated? | `services/reception/booking_agent.py` |
| What is the assistant allowed to say? | `services/reception/guardrails.py` |
| What does the guest never see? | `services/reception/redact.py` |
| What happens when there is no human to escalate to? | `services/reception/guidance.py` |
| What is a guest told about paying? | `services/billing/payment_policy.py` |
| Where do prices come from? | `services/rooms/pricing.py` |
| How is availability decided? | `services/booking/availability.py` |
| What does the kiosk client do? | `static/js/kiosk.js` |
| What is in scope, and when? | `goal.txt`, `apps/core/module_plan.py` |
