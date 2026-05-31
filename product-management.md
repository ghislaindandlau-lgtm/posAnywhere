# posAnywhere.io — Product Management: User Stories

**Status:** Derived 1:1 from the as-built system described in `architecture.md`.
**Scope note:** Every story below maps to capabilities and endpoints that **exist today** in `posAnywhere/backend`. Stories that depend on the future reference design (§12 — Scale-Out Target) are listed separately under *Backlog / Not Yet Built* so the difference is explicit.

**Story format:** `As a <role>, I want <capability>, so that <value>.` Each story includes acceptance criteria tied to the implemented API (§10) and lifecycle (§5).

---

## Personas (the "different kinds of users")

| Persona | Maps to (architecture) | Primary surface |
|---------|------------------------|-----------------|
| **Restaurant Staff / Order Taker** | "Restaurant Staff (POS console)" §2; `UserRole.staff` | `pos.html` |
| **Shift Manager / Dispatcher** | `UserRole.manager`; Dispatch module §4 | `pos.html` |
| **Tenant Administrator** | `UserRole.admin`; `USER` model §6 | Auth API |
| **Delivery Driver** | "Delivery Driver (GPS WS client)" §2; `DRIVER` model | GPS client in `pos.html` / WS |
| **End Customer** | "End Customer (tracking page)" §2 | `tracking.html` |
| **Platform Operator / DevOps** | Deployment view §7; Observability §9 | Container / logs |

---

## 1. Restaurant Staff / Order Taker

### US-1.1 — Log in to the POS
**As** restaurant staff, **I want** to log in with my email and password, **so that** I can access the POS console securely.
- **Acceptance:** `POST /api/auth/login` with valid email + password returns a JWT access token; invalid credentials return `401` and the failure is logged with the request id.
- **Refs:** `app/modules/auth.py`, `app/security.py` (§9 Security).

### US-1.2 — Take an order from any channel
**As** an order taker, **I want** to record an order from phone, online store, delivery portal, or dine-in through one screen, **so that** I don't need separate tools per channel.
- **Acceptance:** `POST /api/orders` accepts a `channel` of `dine_in | phone | portal | online_store` and persists the order with status `new`/`accepted`.
- **Refs:** `OrderChannel` enum §6; intake §5 steps 1–4.

### US-1.3 — See delivery fee + ETA at order time
**As** an order taker, **I want** the delivery fee and ETA computed automatically from the customer address, **so that** I can quote the customer immediately.
- **Acceptance:** On `POST /api/orders` the response includes `delivery_fee`, `zone_id`, and `eta_minutes`; addresses outside any zone are handled predictably (no zone → documented behavior).
- **Refs:** Zone Engine + ETA Calculator §4; `app/geo.py`.

### US-1.4 — Get a quote without committing an order
**As** an order taker, **I want** to request a fee + ETA quote before saving, **so that** I can answer "how much / how long?" on the phone.
- **Acceptance:** `POST /api/dispatch/quote` returns fee + ETA for an address with no order created.

### US-1.5 — Advance an order through its lifecycle
**As** restaurant staff, **I want** to move an order through preparing → on the way → delivered, **so that** the kitchen, driver, and customer stay in sync.
- **Acceptance:** `POST /api/orders/{id}/status?status=preparing|on_the_way` updates status and emits a `STATUS_EVENT`; `POST /api/orders/{id}/deliver` marks `delivered` and frees the driver. Each transition publishes a realtime update.
- **Refs:** lifecycle §5 steps 5–11; `domain.py` publish.

### US-1.6 — Browse and filter live orders
**As** restaurant staff, **I want** to list orders filtered by location and status, **so that** I can manage the current queue.
- **Acceptance:** `GET /api/orders?location_id=&status=` returns the filtered set; `GET /api/orders/{id}` returns one order with items and events.

---

## 2. Shift Manager / Dispatcher

### US-2.1 — Run dispatch to batch and assign
**As** a dispatcher, **I want** to batch pending orders into runs and assign them to available drivers, **so that** deliveries go out efficiently.
- **Acceptance:** `POST /api/dispatch/run` groups pending orders into `RUN`s, assigns `available` drivers (→ `on_run`), sets `Order.run_id`, and publishes `ASSIGNED` updates.
- **Refs:** Route Batcher + Driver Assigner §4; lifecycle §5 step 6.

### US-2.2 — Watch the fleet live on a map
**As** a dispatcher, **I want** a live map of all drivers and active orders, **so that** I can monitor operations in real time.
- **Acceptance:** Subscribing to `WS /api/dispatch/ws` streams fleet-wide updates (driver positions, order status changes) with no page refresh.
- **Refs:** in-process pub/sub `realtime.py`; POS map (Leaflet) §3.

### US-2.3 — Manage driver availability
**As** a dispatcher, **I want** to set a driver's availability, **so that** only ready drivers receive assignments.
- **Acceptance:** `POST /api/drivers/{id}/status?status=offline|available|on_run` updates state; `GET /api/drivers` lists drivers with last known positions.
- **Refs:** `DriverStatus` enum §6; `app/modules/drivers.py`.

### US-2.4 — Reconcile end-of-shift cash
**As** a shift manager, **I want** to generate a settlement per driver per shift, **so that** cash collected is reconciled against orders delivered.
- **Acceptance:** `POST /api/settlements/generate?driver_id=&shift_date=` produces a `SETTLEMENT` with `cash_total` and `orders_delivered`; `GET /api/settlements` lists them.
- **Refs:** `SETTLEMENT` model §6; `app/modules/settlement.py`.

### US-2.5 — Review an operational summary
**As** a manager, **I want** an ops summary report, **so that** I can see performance at a glance.
- **Acceptance:** `GET /api/reports/summary` returns aggregate operational metrics.

---

## 3. Tenant Administrator

### US-3.1 — Onboard staff accounts
**As** a tenant administrator, **I want** to create user accounts with roles (`admin`/`manager`/`staff`), **so that** the right people get the right access.
- **Acceptance:** `POST /api/auth/register` creates a `USER` with a unique email, bcrypt-hashed password, and a role; duplicate email is rejected.
- **Refs:** `USER` model + `UserRole` §6.

### US-3.2 — Confirm the active identity
**As** an administrator, **I want** to verify who is currently authenticated, **so that** I can confirm access and troubleshoot.
- **Acceptance:** `GET /api/auth/me` with a valid Bearer token returns the current user; an absent/invalid token returns `401`.

### US-3.3 — View locations and delivery zones
**As** an administrator, **I want** to see configured locations and their delivery zones, **so that** I can verify coverage and pricing.
- **Acceptance:** `GET /api/locations` returns locations with their zones (polygon + fee).
- **Refs:** `LOCATION`, `DELIVERY_ZONE` §6.

> **Note:** Role-based enforcement is *defined* (role field present) but **per-endpoint RBAC checks are deferred** (§9). Today any authenticated user can call protected endpoints; enforcing role gates is a backlog item.

---

## 4. Delivery Driver

### US-4.1 — Stream my live location
**As** a delivery driver, **I want** my GPS position streamed continuously, **so that** dispatch and customers see where I am.
- **Acceptance:** `WS /api/drivers/{id}/gps` accepts `{lat,lng}` pings that update `last_lat/last_lng` and publish realtime updates; an HTTP fallback `POST /api/drivers/{id}/location` exists for non-WS clients.
- **Refs:** lifecycle §5 step 7; `app/modules/drivers.py`.

### US-4.2 — Go on/off shift
**As** a driver, **I want** to set myself available or offline, **so that** I only get assigned when I'm working.
- **Acceptance:** `POST /api/drivers/{id}/status?status=available|offline` changes my state; going `on_run` happens automatically on assignment.

### US-4.3 — Be auto-freed when a run completes
**As** a driver, **I want** to return to `available` automatically when my run's orders are delivered, **so that** I can take the next batch without manual steps.
- **Acceptance:** When the last order in a `RUN` is delivered, `Run.completed_at` is set and the driver returns to `available`.
- **Refs:** lifecycle §5 step 11.

---

## 5. End Customer

### US-5.1 — Track my order without installing an app
**As** an end customer, **I want** to open a link and see my order status, **so that** I don't have to install anything.
- **Acceptance:** `GET /api/tracking/{token}` returns a snapshot for an unguessable per-order `tracking_token`; an invalid token does not leak other orders.
- **Refs:** `tracking_token` §9; `tracking.html`.

### US-5.2 — See live progress and driver location
**As** an end customer, **I want** the tracking page to update live, **so that** I know when the driver is on the way and arriving.
- **Acceptance:** `WS /api/tracking/{token}/ws` pushes status changes (`assigned`, `on_the_way`, `delivered`) and driver position updates to the page without refresh.
- **Refs:** lifecycle §5 steps 4–10; `realtime.py`.

---

## 6. Platform Operator / DevOps

### US-6.1 — Deploy as a single container
**As** a platform operator, **I want** to run the whole app as one stateless container reading `$PORT`/`$DATABASE_URL`, **so that** deployment is simple.
- **Acceptance:** `uvicorn app.main:app` boots against a PostgreSQL `DATABASE_URL`; Dockerfile/compose provided.
- **Refs:** Deployment view §7.

### US-6.2 — Monitor liveness
**As** an operator, **I want** a health endpoint, **so that** the platform/orchestrator can probe the app.
- **Acceptance:** `GET /api/health` returns a liveness response.

### US-6.3 — Trace any request and the acting user
**As** an operator, **I want** every request and failure logged with a correlation id and the authenticated user, **so that** I can debug incidents.
- **Acceptance:** Each request logs method/path/status/duration with `X-Request-ID`; failures are documented (HTTP errors → warning, validation → warning, unhandled → error with stack trace). Every log line carries `request_id` + `user` (`user:<id>` / `anonymous` / `invalid-token`); `LOG_FORMAT=json` emits one-line JSON for aggregators.
- **Refs:** Observability §9; `app/logging_config.py`.

### US-6.4 — Seed a demo environment
**As** an operator/evaluator, **I want** to seed demo data with tracking links, **so that** I can exercise the full flow quickly.
- **Acceptance:** `python -m app.seed` populates tenants, locations, zones, drivers, and sample orders with tracking tokens.
- **Refs:** `app/seed.py`; §7.

---

## Backlog / Not Yet Built (from §12 — Scale-Out Target)

These are valid future stories but are **explicitly not implemented** today; tracked here so scope stays honest.

- **As an administrator,** I want per-endpoint role enforcement (RBAC), refresh tokens, and TLS termination, so that production access is secure. *(Deferred — §9.)*
- **As a customer,** I want SMS/notification updates on status changes, so that I'm informed without watching the page. *(Needs SMS integration — §12.)*
- **As an integrations engineer,** I want portal/VoIP Caller-ID adapters, so that orders flow in automatically from third parties. *(Integration service deferred — D5/§12.)*
- **As a dispatcher,** I want road-network ETAs and PostGIS zones, so that estimates reflect real routes. *(Replaces local geo — D6/§12.)*
- **As an operator,** I want multi-replica realtime via Redis pub/sub + a WS gateway, so that the platform scales horizontally. *(Replaces in-process fan-out — D3/§12.)*
- **As a data analyst,** I want an analytics warehouse, so that I can report on long-term trends. *(§12.)*
