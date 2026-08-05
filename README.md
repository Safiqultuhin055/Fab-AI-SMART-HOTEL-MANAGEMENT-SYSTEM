# ASHOS — AI Smart Hotel OS

AI-native hotel operating system. The AI receptionist is the primary interface;
a complete PMS core (rooms, reservations, folio, housekeeping, restaurant) sits
behind it and keeps working when the AI is switched off.

**Read [`goal.txt`](goal.txt) first.** It is the execution contract: scope,
locked technical decisions, phase plan, exit criteria and the definition of
done. This README only tells you how to run the thing.

| Document | For |
|---|---|
| [docs/BUSINESS_OVERVIEW.md](docs/BUSINESS_OVERVIEW.md) | What ASHOS does for a hotel, who it is for, what is built vs deferred, risks |
| [docs/TECHNICAL_ARCHITECTURE.md](docs/TECHNICAL_ARCHITECTURE.md) | Architecture, the AI turn pipeline, data model, API, security, deployment |
| [`goal.txt`](goal.txt) | The binding scope and phase contract |

Status: **Phase 0 — Foundation.** See [Current state](#current-state).

---

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 (see goal.txt D01 for why not 3.13+) |
| Web | Django 5.2 · DRF · Channels (ASGI) |
| Data | PostgreSQL 16 + pgvector (HNSW) · Redis |
| Async | Celery + Celery Beat, queues: `default` · `ai` · `vision` · `periodic` |
| Storage | MinIO / S3-compatible |
| Frontend | Django templates · Bootstrap 5 · vanilla ES6 · Chart.js |
| AI | Any OpenAI-compatible LLM · Sentence Transformers · CLIP · Whisper · TTS · InsightFace · PaddleOCR |
| Deploy | Docker Compose · Nginx · TLS |

---

## Quick start

### With Docker (recommended)

```bash
cp .env.example .env          # then set DJANGO_SECRET_KEY and AI_LLM_API_KEY
docker compose -f deploy/compose.yml --env-file .env up -d --build
docker compose -f deploy/compose.yml exec web python manage.py createsuperuser
docker compose -f deploy/compose.yml exec web python manage.py bootstrap_hotel
```

Open <http://localhost:8000/> · API docs at `/api/docs/` · admin at `/admin/`.

### Without Docker

Requires a PostgreSQL 16 with the `vector` extension available, plus Redis.

```bash
make setup            # venv + dev dependencies
make env              # .env from template
make migrate seed     # schema + system roles
python manage.py createsuperuser
python manage.py bootstrap_hotel
make run
```

`make help` lists every command.

---

## Layout

```
apps/            Django apps — thin. Models, admin, urls, views.
  core/          Base models, tenancy context, soft delete, crypto, navigation
  accounts/      Custom user, roles, per-hotel RBAC, audit log
  tenants/       Hotel (the tenant root), memberships
  ai_center/     Model config, versioned prompts, usage log, safety policy
  guests/ rooms/ booking/ housekeeping/ restaurant/ billing/ reception/
  vision/ rag/ vector_search/ notifications/ dashboard/
api/             DRF routers, serializers, pagination, RFC 7807 errors
  v1/            The only public API surface
services/        ⭐ ALL business logic
  ai/            The single AI gateway — nothing else may call an AI SDK
config/          settings/{base,dev,test,prod}.py · asgi · celery · routing
templates/       Server-rendered staff UI
static/          Design system (css/ashos.css) and shell JS
deploy/          Dockerfile, compose.yml, nginx, postgres init
tests/           unit/ · integration/ · ai_eval/ (prompt regression)
```

Two rules that keep this honest:

1. **Business logic lives in `services/`.** Views, serializers, tasks and model
   methods are delivery mechanisms. If a hotelier would call it a policy, it
   belongs one layer down.
2. **All AI goes through `services/ai/gateway.py`.** That is where the kill
   switch, budget cap, retries, fallback and metering are enforced — for every
   call, including the ones written by whoever joins next.

---

## Current state

### Working now (Phase 0)

- Settings split, ASGI + Channels wiring, Celery app with four queues and the
  scheduled retention/audit/cost jobs
- UUIDv7 primary keys with a monotonic counter, soft delete, tenant-scoped
  managers, request-context middleware
- Custom user (email login), roles as data, **per-hotel** permission backend,
  account lockout, append-only audit log with secret scrubbing
- Five roles — Super Admin · Admin · Manager · Staff · AI Reception — gating
  both the sidebar and the URL through the same `core.access_*` permission, so
  a hidden menu is genuinely blocked rather than merely invisible
- Every one of the eleven menu items navigates. Dashboard, AI Reception, AI
  Center and Settings render real data; the rest open a page naming their phase
  and what they will contain
- **AI Reception kiosk** — persisted conversations, answers grounded in the
  hotel record with source citations, guardrails (blocked topics, repeat
  detection, turn/token caps), always-available human handoff with a staff
  queue, and a deterministic offline answerer so the lobby terminal still works
  with no LLM key, no budget, or no internet
- **The whole kiosk is in the guest's language** — not just the conversation.
  Buttons, titles, the booking card's labels, the vision rail's paragraphs, the
  device bar, screen-reader names, the tiles' prompts and the numerals all follow
  the property's `kiosk_language`, and follow the guest when they tap the language
  chip or simply start speaking the other language — no reload. Every word lives
  in [`apps/reception/copy.py`](apps/reception/copy.py); a key added to one
  language and not the other fails the test suite rather than a lobby screen.
  There is no gettext catalog on purpose: the language belongs to the
  conversation, not to the HTTP request
- **Public online booking** — `/book/?hotel=GLH-001`, no login. It is the lobby
  terminal on the web: the same assistant panel, the same booking agent, taking
  the booking in conversation, with the bill and the printable slip filling in
  beside it as the validated draft progresses. Channel `website`, so those
  reservations are attributed to the website rather than to a kiosk. No camera and
  no always-open microphone — neither belongs on a page opened at home. Underneath
  it, a plain search-and-book form using the same services, which is the route
  that still works with no LLM key, no budget or no internet; it opens itself when
  the assistant cannot answer. No card is asked for and no payment recorded — the
  slip says payment is taken at the desk (goal.txt D11). Staff reach it from the
  sidebar's quick actions
- **Room photos in the kiosk** — while a booking is being taken the scene shows
  the rooms on offer, then the chosen one alone, and the chosen room's photograph
  rides inside the answer that talks about it. Cards are built from the server's
  priced snapshot, so the picture cannot drift from the room being sold. A type
  with no photo shows its facts on a tile rather than stock photography:

  ```bash
  # the property's own, named DLX-1.jpg / SEA-2.jpg by room type code
  python manage.py import_room_photos --hotel GLH-001 --dir <folder> --replace
  # or a demo set, for a machine nobody has photographed yet
  python manage.py import_room_photos --hotel GLH-001 --demo-set --replace
  ```

  One photograph at a time goes in admin → Room types → (a type) → photos
- **Room board with photographs and a sellable state** (`/rooms/`) — every room
  carries its type's picture, and four states a receptionist can act on: in house
  tonight · booked ahead · free to sell · out of service. Booked-ahead is the one
  that matters: a room standing empty tonight and sold from Friday used to look
  exactly like a room nobody wants. Physical status (clean / dirty / OOO) stays on
  its own line, because a spotless room can be sold and a dirty one can be free
- Encrypted-at-rest fields for provider keys
- AI gateway: provider-neutral types, OpenAI-compatible provider (chat,
  streaming, sync + async, embeddings, STT, TTS), deterministic fake provider,
  retry + fallback, cost metering, dimension guard, kill switch, budget cap
- AI Center models: model config, versioned prompts, usage log, safety policy
- JWT auth API, `/api/v1/health/`, `/api/v1/ai/health/`, OpenAPI at `/api/docs/`
- RFC 7807 errors, cursor pagination, scoped throttles
- Dark glassmorphism UI shell matching Prototype.png, with the locked left nav
- Docker stack (pgvector, Redis, MinIO, web, worker, beat, nginx) and CI
- `seed_demo` — deterministic, idempotent demo data: 3 hotels, 30 staff across
  every role, priced AI configs, ~3.5k shaped AI usage records and an audit
  trail. `--flush` resets only what it created, never a real property.

### Deliberately not built yet

Every nav item tagged `P1`–`P4` in the sidebar. The dashboard shows `—` and
names the phase instead of inventing numbers; a fake KPI is how a stakeholder
comes to believe a module exists.

---

## Tests

```bash
make test         # excludes ai_eval
make cov          # with coverage
pytest -m ai_eval # hits a real provider, costs money, runs nightly not per PR
```

`config/settings/test.py` forces every AI capability to the `fake` provider. A
unit test that reaches the network is a bug, not a flake.

Targets (goal.txt §6): >70% overall, >85% in `services/`.

---

## Configuration

Everything comes from `.env`; see `.env.example` for the full annotated list.

The AI settings there are **bootstrap defaults**. Once AI Center has rows, the
database wins — that is what lets an operator change model, temperature or
prompt at 2am without a deploy.

Non-obvious ones:

| Variable | Why it matters |
|---|---|
| `AI_EMBEDDING_DIM` | Must match the vector column width. Changing the model means re-embedding everything (goal.txt D08). |
| `AI_KILL_SWITCH` | Instantly routes every AI surface to manual staff mode. |
| `AI_DAILY_COST_CAP_USD` | At 100% the hourly rollup trips the hotel's kill switch. |
| `BIOMETRIC_ENABLED` | Ships **off**. Requires documented legal sign-off (goal.txt R1). |
| `FIELD_ENCRYPTION_KEY` | Set explicitly in production. Otherwise it derives from `SECRET_KEY`, and rotating that makes stored keys unreadable. |

---

## Two things that are not negotiable

**Biometrics.** Face data is sensitive personal data, not a feature flag. It is
opt-in with explicit consent, embedding-only (the raw image is discarded),
encrypted, expiring, deletable on request, never for minors, and always with a
non-biometric path to the same service. Legal sign-off gates Phase 3
(goal.txt D10, R1).

**Model licensing.** Some InsightFace pretrained weights are non-commercial.
Every weight file must be licence-audited before commercial deployment. This is
a blocking item, not a footnote (goal.txt R2).

---

## Contributing

`make fmt lint typecheck test` before every push. The full definition of done is
goal.txt §7 — ten checkboxes, all of them cheap, all of them things that hurt
later if skipped.
