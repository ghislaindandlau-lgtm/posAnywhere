# posAnywhere.io — Software Architecture Document (As-Built)

**Author role:** IT Development Team
**Date:** 2026-05-31
**Status:** **AS-BUILT.** This document describes the system **actually implemented** in `posAnywhere/backend` on a 1:1 basis. The diagrams in §2–§9 reflect the code that exists today.
**Convention:** [C4 Model](https://c4model.com) (Context → Container → Component → Code) + [arc42](https://arc42.org) document structure. Diagrams are drawn as **plain-text ASCII art** so they render in *any* viewer — including Windsurf IDE — with no Mermaid/plugin required.

> **What this is.** A runnable **Python FastAPI modular monolith** that implements the posAnywhere.io domain (multi-channel order intake, delivery-zone engine, dispatch/route batching, live driver GPS, app-free customer tracking, driver settlement). It runs on **PostgreSQL** with an in-process realtime layer (no Kafka/Redis), and is container-ready.
>
> The original microservice/event-driven **reference design** (Kafka, Redis, PostGIS, separate realtime tier, integration adapters) is **not** what is built today; it is preserved in **§12 — Scale-Out Target** as the future direction.

---

## 1. Introduction & Goals

posAnywhere.io is a **POS + delivery-orchestration platform** for restaurants operating their own courier fleets. This as-built implementation delivers the core operational loop end-to-end.

### Implemented capabilities
- **Multi-channel intake** — phone (Caller-ID style), online store, delivery portal, dine-in (all via one REST endpoint).
- **Delivery-zone engine** — address → zone + fee via point-in-polygon.
- **ETA calculation** — kitchen prep time + straight-line (haversine) travel time.
- **Dispatch** — batches pending orders into runs and assigns available drivers.
- **Live GPS + realtime** — drivers stream positions over WebSocket; POS map and customer page update live via in-process pub/sub.
- **App-free customer tracking** — a token-addressed light web page (no install).
- **Settlement & reporting** — end-of-shift cash reconciliation + ops summary.
- **Two web UIs** — staff POS/dispatch console and customer tracking page.

### Quality attributes (as realized today)
| Attribute | How it is addressed in the build |
|-----------|----------------------------------|
| **Simplicity / runnability** | Single process, PostgreSQL only, no broker/cache to operate |
| **Real-time latency** | In-process WebSocket fan-out (no network hop) |
| **Integrability**     | Clean REST API + module boundaries ready to split into services |
| **Portability** | Stateless app reads `$PORT`/`$DATABASE_URL`; Docker image provided |
| **Testability** | 33 pytest cases against an isolated DB |

> Aspirational targets from the product brief (350 orders/min burst, 20M+ lifetime orders, HA) are **design goals for §12**, not characteristics of this single-process build.

---

## 2. C4 Level 1 — System Context (as-built)

```text
      +-------------------+   +------------------+   +-----------------+
      | Restaurant Staff  |   |  Delivery Driver |   |   End Customer  |
      |   (POS console)   |   |  (GPS WS client) |   | (tracking page) |
      +---------+---------+   +---------+--------+   +--------+--------+
                | HTTPS/JSON            | WS GPS pings        | HTTP + WS
                v                       v                     v
      +-------------------------------------------------------------------+
      |                posAnywhere.io PLATFORM (single app)               |
      |        FastAPI modular monolith + bundled static web UIs          |
      +-------------------------------------------------------------------+
                                     |
                                     v
                         +-----------------------+
                         | PostgreSQL            |
                         | (runtime database)    |
                         +-----------------------+

   NOT INTEGRATED in this build (no external calls are made):
   +--------+ +------+ +------+ +-------+ +------+ +-------+
   | Portal | | Maps | | Pay  | | VoIP  | |Notify| |Fiscal |
   | APIs   | | Geo  | | Term | |CallID | | SMS  | | Acct  |
   +--------+ +------+ +------+ +-------+ +------+ +-------+
   Geocoding/distance is computed locally (app/geo.py). Channel + Caller-ID
   are represented by an enum on the order; no external systems are contacted.
```

---

## 3. C4 Level 2 — Container Diagram (as-built)

```text
   Staff (browser)     Driver (browser)        Customer (browser)
        |                    |                        |
        | HTTPS/JSON         | WS {lat,lng}           | HTTP + WS
        v                    v                        v
  +--------------+     (GPS simulator in       +----------------+
  | pos.html     |      the POS page)          | tracking.html  |
  | POS+dispatch |                             | app-free track |
  | map (Leaflet)|                             | (Leaflet)      |
  +------+-------+                             +-------+--------+
         |        \__ WS /api/dispatch/ws __        |  WS /api/tracking/{token}/ws
         |                                  \       |
         v                                   v      v
  +---------------------------------------------------------------+
  |   FastAPI application (app/main.py)  -- run by uvicorn         |
  |                                                               |
  |  Routers (app/modules/*):                                     |
  |   +--------+ +----------+ +---------+ +-----------+ +--------+ |
  |   | orders | | dispatch | | drivers | | settlement| |tracking| |
  |   +---+----+ +----+-----+ +----+----+ +-----+-----+ +---+----+ |
  |       |           |            |            |           |      |
  |       +-----------+-----+------+------------+-----------+      |
  |                         |                                      |
  |          domain.py (status transitions + publish)             |
  |                         |                                      |
  |        realtime.py  (IN-PROCESS pub/sub ConnectionManager)     |
  |                         |                                      |
  |        SQLAlchemy ORM (app/models.py, app/database.py)         |
  +-----------------------------------+---------------------------+
                                      |
                                      v
                          +-----------------------+
                          | PostgreSQL            |
                          | (via DATABASE_URL)    |
                          +-----------------------+

  Notes vs the §12 target:  NO Kafka/RabbitMQ broker, NO Redis, NO separate
  realtime tier, NO Integration service, NO analytics warehouse. Module calls
  are in-process Python function calls; realtime fan-out is in-memory.
```

---

## 4. C4 Level 3 — Component Diagram (Dispatch module, as-built)

The dispatch engine is the core differentiator. It lives in `app/modules/dispatch.py` and uses local geo math instead of an external Maps API.

```text
  +-----------------------------------------------------------+
  |        Dispatch module  (app/modules/dispatch.py)         |
  |                                                           |
  |   +----------------+   uses       +--------------------+  |
  |   | Zone Engine    |------------> | app/geo.py         |  |
  |   | resolve_zone() |  point_in_   | haversine_km()     |  |
  |   | addr->zone/fee |  polygon()   | point_in_polygon() |  |
  |   +-------+--------+              +--------------------+  |
  |           | reads DeliveryZone.polygon (JSON, via ORM)    |
  |           |                                               |
  |   +----------------+   uses       +--------------------+  |
  |   | Route Batcher  |------------> | ETA Calculator     |  |
  |   | dispatch_      |              | calculate_eta()    |  |
  |   | pending()      |              | prep + travel      |  |
  |   +-------+--------+              +--------------------+  |
  |           |                                               |
  |   +----------------+   reads      +--------------------+  |
  |   | Driver Assigner|------------> | Driver State (DB)  |  |
  |   | runs->drivers  |              | status/last_lat/lng|  |
  |   +-------+--------+              +--------------------+  |
  |           | persists Run + Order.run_id (ORM)             |
  |           | publishes via domain.publish_order_update --> |--+
  +-----------|-----------------------------------------------+  |
              v                                                  v
        +-----------------+                          realtime.py (in-process
        | PostgreSQL      |                          pub/sub) -> POS map +
        +-----------------+                          tracking pages
```

---

## 5. Order Lifecycle — Sequence Diagram (as-built)

```text
  Channel     Order       Dispatch    Driver(WS)  Tracking    Customer
  (enum)      module      module      drivers mod  module      page
     |           |           |           |          |           |
  1) |--POST ---->           |           |          |           |
     | /api/orders           |           |          |           |
  2) |           |--resolve_zone()------>|          |           |
  3) |           |<--zone, fee, ETA------|          |           |
  4) |           |--record NEW, ACCEPTED + publish ----------->(WS snapshot)
  5) |           |           |           |          |           |--live--->|
     |   [staff: POST /api/orders/{id}/status?status=preparing] |          |
  6) |           |  POST /api/dispatch/run ->                    |          |
     |           |           |--batch + assign driver (Run)      |          |
     |           |           |--publish ASSIGNED -------------->(WS update)->|
  7) |           |           |           |--WS {lat,lng} ping    |          |
     |           |           |           |--update + publish -->(WS update)->|
  8) |   [staff: POST /api/orders/{id}/status?status=on_the_way]            |
  9) |   [staff: POST /api/orders/{id}/deliver]                             |
 10) |           |--record DELIVERED + publish --------------->(WS update)->|
 11) |           |--if run complete: Run.completed_at, driver -> AVAILABLE  |
     v           v           v           v          v           v
```

> There is no Integration service and no payment capture call: the channel
> posts directly to the Order API, and "payment" is represented by the
> DELIVERED status transition only.

---

## 6. Domain Data Model (ER Diagram, matches app/models.py)

```text
  +----------+ 1     * +-----------+ 1   * +---------------+
  |  TENANT  |---------| LOCATION  |-------| DELIVERY_ZONE |
  +----------+  owns   +-----------+ defines+--------------+
                            | 1                  ^ 1
                            | receives           | priced by
                            v *                  | *
  +----------+ 1     * +-----------+--------------+
  | CUSTOMER |---------|   ORDER   |
  +----------+ places  +-----------+
                         | 1   | 1            * +-----+ 1   * +--------+
                contains | *   | emits          | RUN |-------| DRIVER |
                         v     v *              +-----+groups +--------+
                  +-----------+ +--------------+   ^ groups(1..*)  | 1
                  | ORDER_ITEM| | STATUS_EVENT |   |               | reconciled
                  +-----------+ +--------------+   +--ORDER        v *
                                                            +------------+
                                                            | SETTLEMENT |
                                                            +------------+

  Columns as implemented (SQLAlchemy ORM, app/models.py):
    TENANT(id, name)
    LOCATION(id, tenant_id, name, address, lat, lng)
    DELIVERY_ZONE(id, location_id, name, polygon[JSON list of [lat,lng]], fee)
    CUSTOMER(id, phone, name, address, lat, lng)
    ORDER(id, location_id, customer_id, run_id, channel, status,
          items_total, delivery_fee, total, zone_id, eta_minutes,
          tracking_token, created_at)
    ORDER_ITEM(id, order_id, name, qty, price)
    STATUS_EVENT(id, order_id, status, ts)
    DRIVER(id, name, status, last_lat, last_lng)
    RUN(id, driver_id, started_at, completed_at)
    SETTLEMENT(id, driver_id, shift_date, cash_total, orders_delivered, created_at)
    USER(id, email[unique], full_name, hashed_password, role,
         tenant_id, is_active, created_at)

  Enums:  OrderChannel(dine_in, phone, portal, online_store)
          OrderStatus(new, accepted, preparing, assigned, on_the_way,
                      delivered, cancelled)
          DriverStatus(offline, available, on_run)
          UserRole(admin, manager, staff)
  Note: zones use a plain JSON polygon (not PostGIS geometry).
  Cardinality:  1=one   *=many.
```

---

## 7. Deployment View (as-built)

```text
  CLIENT (any browser)            SINGLE DEPLOYABLE              DATA
  +-------------------+      +--------------------------+   +-----------------+
  | POS console       |      | Container / host:        |   | PostgreSQL      |
  | Tracking page     |<====>| uvicorn app.main:app     |==>| (required, via  |
  | Driver GPS (WS)   | HTTP | - REST + WebSocket        |   |  DATABASE_URL)  |
  +-------------------+  +WS | - serves static UIs       |   +-----------------+
                             | - in-process pub/sub      |
   Static assets are served  | reads $PORT, $DATABASE_URL|
   by the same app (CDN      +--------------------------+
   optional, not required).

  Local run:    docker compose up -d db   then   uvicorn app.main:app --reload
                (seed once: python -m app.seed)
  Container:    Dockerfile -> uvicorn on $PORT
  Compose:      docker-compose.yml runs app + PostgreSQL
  Cloud:        any container host / Render / Fly.io / Heroku (see README).

  Horizontal scaling caveat: the in-process pub/sub (realtime.py) is per-
  process, so multi-replica realtime requires the §12 Redis swap first.
```

---

## 8. Decisions As Built (and deltas vs the reference design)

| # | Decision in this build | Why / Note | Reference-design delta |
|---|------------------------|-----------|------------------------|
| D1 | **Modular monolith** (one FastAPI app) | Minimal infra (just PostgreSQL); clear module seams | was microservices |
| D2 | **In-process calls** between modules | Simplicity; no ops overhead | replaces Kafka/RabbitMQ broker |
| D3 | **In-process pub/sub** (`realtime.py`) | Low-latency, no dependency | replaces Redis pub/sub + realtime tier |
| D4 | **App-free tracking** = static page + WS | Matches "no app install" goal | same intent, lighter impl |
| D5 | **No integration adapters** yet | Channel is an enum; intake via REST | Integration service deferred |
| D6 | **Point-in-polygon** in `app/geo.py` | Dependency-free geospatial | replaces PostgreSQL + PostGIS |
| D7 | **PostgreSQL only** (psycopg2 driver) | Single supported runtime DB; no SQLite fallback | SQLite is used by the test suite only |
| D8 | **`Base.metadata.create_all()`** on startup | Fast bootstrap | migrations (Alembic) deferred |
| D9 | **Vanilla HTML/JS + Tailwind/Leaflet CDN** | Build-free frontend | replaces SPA build pipeline |

---

## 9. Cross-Cutting Concerns (current state)

- **Security:** User authentication via `/api/auth` (bcrypt-hashed passwords + JWT bearer tokens, `app/security.py`); role field on users (`admin`/`manager`/`staff`) ready for RBAC enforcement; configurable CORS (`CORS_ORIGINS`); unguessable per-order `tracking_token` for the public tracking page; no card data stored. **Deferred:** TLS termination, per-endpoint RBAC checks, refresh tokens, PII-at-rest encryption — to be added for production (see §12).
- **Observability:** centralised structured logging (`app/logging_config.py`): every HTTP request is logged with a correlation id (`X-Request-ID`), method, path, status and duration; every failure is documented (raised `HTTPException` -> warning with reason, validation errors -> warning, unhandled exceptions -> error with stack trace). Key business actions (auth login/register, order created, status changes, dispatch runs, driver status, settlements, WS connect/disconnect) emit audit lines. Every log line is enriched with the request id and the authenticated user (`user:<id>` decoded from the JWT, or `anonymous`/`invalid-token`) via `contextvars`. Output is human-readable text or one-line JSON for aggregators (`LOG_FORMAT=text|json`). Level via `LOG_LEVEL`, optional rotating file via `LOG_FILE`; plus `GET /api/health` liveness probe. **Deferred:** metrics, distributed tracing.
- **Resilience:** DB work runs in transactions; WebSocket clients auto-reconnect; dead sockets are pruned on broadcast. **Deferred:** broker retries/dead-letter, DB read replicas.
- **Testing:** `pytest` suite (33 tests) covering auth (register/login/JWT), request logging/failure documentation/JSON+user context, geo, dispatch, order lifecycle, settlement, reporting and tracking (REST + WS), each against an isolated SQLite DB (test-only; the app runtime uses PostgreSQL).
- **Scalability:** App layer is stateless and can scale horizontally **except** the in-process realtime fan-out (single-process); see §12 for the Redis-backed swap.

---

## 10. Repository Layout & Module Mapping

```text
posAnywhere/
└── backend/
    ├── app/
    │   ├── main.py          # app wiring, CORS, catalog endpoints, dispatch WS, static UIs
    │   ├── config.py        # typed settings from env/.env
    │   ├── database.py      # engine, SessionLocal, Base, init_db()
    │   ├── models.py        # ORM models  (see §6)
    │   ├── schemas.py       # Pydantic request/response contracts
    │   ├── geo.py           # haversine + point-in-polygon (local Maps replacement)
    │   ├── domain.py        # status transitions + realtime publish helpers
    │   ├── realtime.py      # in-process WebSocket pub/sub manager
    │   ├── logging_config.py# logging setup: text/JSON formatters + request/user context
    │   ├── security.py      # password hashing + JWT + get_current_user
    │   ├── seed.py          # demo data + tracking links
    │   ├── modules/
    │   │   ├── auth.py       # register / login / me (JWT)
    │   │   ├── orders.py     # §5 intake + lifecycle
    │   │   ├── dispatch.py   # §4 zone/ETA/batcher/assigner
    │   │   ├── drivers.py    # driver state + GPS WebSocket
    │   │   ├── settlement.py # settlement + reporting
    │   │   └── tracking.py   # app-free tracking (REST + WS)
    │   └── static/{pos.html, tracking.html}
    ├── tests/               # pytest suite (33 tests)
    ├── requirements.txt / requirements-dev.txt
    ├── Dockerfile / docker-compose.yml / .env.example
```

| Container (§3) | Implemented by |
|----------------|----------------|
| Auth | `app/modules/auth.py` + `app/security.py` |
| POS Web | `app/static/pos.html` |
| Tracking Page | `app/static/tracking.html` |
| Driver GPS client | GPS simulator inside `pos.html` → `WS /api/drivers/{id}/gps` |
| Order service | `app/modules/orders.py` |
| Dispatch & Routing | `app/modules/dispatch.py` (+ `app/geo.py`) |
| Driver service | `app/modules/drivers.py` |
| Settlement & Report | `app/modules/settlement.py` |
| Tracking / Realtime | `app/modules/tracking.py` + `app/realtime.py` |
| Operational DB | PostgreSQL (`DATABASE_URL`) |

### HTTP / WS API (as implemented)
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/auth/register` | Create a user account |
| POST | `/api/auth/login` | Email + password -> JWT access token |
| GET | `/api/auth/me` | Current authenticated user (Bearer token) |
| POST | `/api/orders` | Place order (prices delivery + ETA) |
| GET | `/api/orders` | List orders (filter `location_id`, `status`) |
| GET | `/api/orders/{id}` | Get one order |
| POST | `/api/orders/{id}/status?status=` | Advance status |
| POST | `/api/orders/{id}/deliver` | Mark delivered + free driver |
| POST | `/api/dispatch/quote` | Fee + ETA quote |
| POST | `/api/dispatch/run` | Batch + assign pending orders |
| GET | `/api/drivers` | List drivers + positions |
| POST | `/api/drivers/{id}/status?status=` | Set availability |
| POST | `/api/drivers/{id}/location` | HTTP GPS fallback |
| WS | `/api/drivers/{id}/gps` | Stream `{lat,lng}` pings |
| GET | `/api/tracking/{token}` | Tracking snapshot |
| WS | `/api/tracking/{token}/ws` | Live tracking updates |
| POST | `/api/settlements/generate?driver_id=&shift_date=` | Reconcile shift cash |
| GET | `/api/settlements` | List settlements |
| GET | `/api/reports/summary` | Ops summary |
| GET | `/api/locations` | Locations + zones (catalog) |
| GET | `/api/health` | Liveness probe |
| WS | `/api/dispatch/ws` | Fleet-wide live updates (POS map) |

> Run/test/deploy instructions live in `posAnywhere/README.md`.

---

## 11. How to View These Diagrams

All diagrams are **plain-text ASCII art** inside fenced ` ```text ` code blocks. They need **no Mermaid engine, plugin, or preview mode** — they display identically in Windsurf IDE, any text editor, terminal, or rendered Markdown. Use a **monospaced font** for correct alignment.

---

## 12. Scale-Out Target (future reference — NOT built today)

The original convention-complete reference design is retained here as the
intended evolution path when load/availability requirements (e.g. 350 orders/
min bursts, HA, many portal integrations) justify the added operational cost.

**Planned changes from the as-built monolith:**

- Split modules into independently deployable **services** (Order, Dispatch, Integration, Tracking, Settlement).
- Introduce an **event broker** (Kafka/RabbitMQ) between intake and dispatch (replaces D2 in-process calls).
- Add **Redis** for live driver positions + pub/sub and a **separate realtime/WebSocket gateway** (replaces D3 in-process fan-out).
- Add an **Integration service** with adapter-per-portal + VoIP Caller-ID (replaces D5).
- Move zones to **PostgreSQL + PostGIS** geometry (replaces D6 point-in-polygon).
- Add **Maps/Payments/SMS** external integrations, an **analytics warehouse**, **Alembic** migrations (replaces D8), an **API gateway** with TLS/authN-Z, and read replicas.

```text
  [Clients] -> CDN/WAF/LB + API Gateway -> {Order, Dispatch, Integration,
               Tracking, Settlement} services
                     |  events                     |
                     v                              v
            Kafka/RabbitMQ broker            Redis (cache + pub/sub) + WS gateway
                     |                              |
                     v                              v
        PostgreSQL(+PostGIS, replicas)       Analytics warehouse
        External: Maps | Payments | SMS | VoIP CallerID | Fiscal | Portals
```

This target keeps the same domain model (§6) and order lifecycle (§5); only the
deployment topology and infrastructure dependencies change.
