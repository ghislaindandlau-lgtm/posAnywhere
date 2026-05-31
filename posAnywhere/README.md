# posAnywhere.io — POS + Delivery Orchestration Platform

A runnable reference implementation of the architecture described in
`../architecture.md`. It is built as a **Python FastAPI modular monolith**:
one deployable application whose internal modules map 1:1 to the services in
the C4 container diagram (Order, Dispatch & Routing, Driver, Settlement,
Tracking) plus an in-process realtime/WebSocket tier.

> Why a modular monolith? It captures the same domain boundaries and event
> flow as the microservice design in the architecture doc, but with minimal
> infrastructure (**PostgreSQL only** — no Kafka/Redis required) so you can run
> it easily and later split modules into services without rewriting them.

---

## Features implemented

- **Authentication** — user registration + login with bcrypt-hashed passwords and JWT bearer tokens (roles: admin/manager/staff).
- **Multi-channel order intake** — phone (Caller ID), online store, portal, dine-in.
- **Dispatch & Routing engine** (the core differentiator):
  - **Zone Engine** — resolves a delivery address to a zone + fee via point-in-polygon.
  - **ETA Calculator** — kitchen prep time + haversine travel time.
  - **Route Batcher + Driver Assigner** — groups pending orders into runs and assigns free drivers.
- **Live GPS tracking** — drivers stream positions over WebSocket; the POS map and
  customer pages update in real time.
- **App-free customer tracking** — a token-addressed light web page with live status + map.
- **Driver settlement & reporting** — end-of-shift cash reconciliation and an ops summary.
- **Two web UIs** — a staff **POS & Dispatch console** and a **customer tracking page**.

---

## Project layout

```
posAnywhere/
└── backend/
    ├── app/
    │   ├── main.py          # FastAPI app: wiring, CORS, static pages, dispatch WS
    │   ├── config.py        # Typed settings loaded from env / .env
    │   ├── database.py      # SQLAlchemy engine, session, Base, init_db()
    │   ├── models.py        # ORM models = the ER diagram (architecture §6)
    │   ├── schemas.py       # Pydantic request/response contracts
    │   ├── geo.py           # haversine + point-in-polygon (local "Maps Adapter")
    │   ├── domain.py        # shared helpers: status transitions + realtime publish
    │   ├── realtime.py      # in-process WebSocket pub/sub manager
    │   ├── seed.py          # demo data: tenant, location, zones, drivers, orders
    │   ├── modules/
    │   │   ├── orders.py     # Order service (intake + lifecycle)
    │   │   ├── dispatch.py   # Dispatch & Routing engine (architecture §4)
    │   │   ├── drivers.py    # Driver state + GPS WebSocket ingestion
    │   │   ├── settlement.py # Settlement + reporting
    │   │   └── tracking.py   # App-free customer tracking (REST + WS)
    │   └── static/
    │       ├── pos.html      # Staff POS + dispatch console
    │       └── tracking.html # Customer app-free tracking page
    ├── requirements.txt
    ├── Dockerfile
    ├── docker-compose.yml    # optional: app + PostgreSQL
    └── .env.example
```

---

## Run locally (PostgreSQL required)

Prerequisites: **Python 3.11+** and a reachable **PostgreSQL** instance.
The quickest local Postgres is the one bundled in `docker-compose.yml`.

From the `posAnywhere/backend` folder:

```bash
# 1) Start a local PostgreSQL (matches the default DATABASE_URL)
docker compose up -d db

# 2) Create and activate a virtual environment
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# 3) Install dependencies
pip install -r requirements.txt

# 4) Configure the DB connection (defaults already point at the Postgres above)
copy .env.example .env       # Windows
# cp .env.example .env        # macOS/Linux

# 5) Seed demo data (tenant, location, zones, drivers, sample orders)
python -m app.seed

# 6) Start the server
uvicorn app.main:app --reload
```

> The app is **PostgreSQL-only** at runtime; there is no SQLite fallback. If
> `DATABASE_URL` is unset it defaults to
> `postgresql+psycopg2://posanywhere:posanywhere@localhost:5432/posanywhere`.
> (SQLite is used solely by the automated test suite below.)

Then open:

- **POS & Dispatch console:** http://localhost:8000/
- **API docs (Swagger):** http://localhost:8000/docs
- **Customer tracking:** the seed step prints `/track?token=...` links.

### Try the end-to-end flow
1. In the POS, click the map to set a delivery point, then **Place order**.
2. Click **Run dispatch** — a driver is assigned and a run is created.
3. Click a **driver chip**, then click the map repeatedly to stream GPS pings.
4. Open the order's **Track** link — the customer page shows the driver moving live.
5. Click **Deliver** to complete the order and free the driver.

---

## Run the tests

A pytest suite covers the geo helpers, dispatch engine, order lifecycle,
settlement, reporting and tracking (REST + WebSocket). Each test runs against
an isolated throwaway SQLite database (test-only — the app itself never uses
SQLite), so tests stay zero-infrastructure and never touch your dev data.

From `posAnywhere/backend`:

```bash
pip install -r requirements-dev.txt
pytest
```

Expected: **29 passed**.

---

## Run the whole stack in Docker (app + PostgreSQL)

To run everything in containers:

```bash
docker compose up --build
# then, in another terminal, seed the Postgres database:
docker compose exec api python -m app.seed
```

The API is served on http://localhost:8000 and talks to the bundled Postgres.

---

## Configuration

All settings come from environment variables (see `.env.example`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `sqlite:///./posanywhere.db` | DB connection (use a `postgresql+psycopg2://…` URL in prod) |
| `CORS_ORIGINS` | `*` | Comma-separated allowed browser origins |
| `AVERAGE_DRIVER_SPEED_KMH` | `25` | Used by the ETA calculator |
| `DEFAULT_PREP_MINUTES` | `15` | Base kitchen prep time added to every ETA |
| `APP_NAME` | `posAnywhere.io` | Shown in API metadata |

---

## Deploy to the cloud

The app is a standard ASGI service that reads `$PORT` and `$DATABASE_URL`, so it
runs on any container or Python platform. **Always use PostgreSQL in production**
(SQLite is for local dev only).

### Option A — Any Docker host / Kubernetes
```bash
docker build -t posanywhere-backend ./backend
docker run -p 8000:8000 -e DATABASE_URL="postgresql+psycopg2://user:pass@host:5432/posanywhere" posanywhere-backend
```
For Kubernetes, push the image to your registry and run it as a `Deployment`
behind a `Service`/Ingress. Scale the API horizontally (it is stateless); use a
managed PostgreSQL and, when you outgrow the in-process pub/sub, swap
`app/realtime.py` for a Redis-backed implementation (architecture §3).

### Option B — Render.com (simple managed deploy)
1. Push this repo to GitHub.
2. Create a **PostgreSQL** instance on Render and copy its connection string.
3. Create a **Web Service** from the repo with:
   - **Root directory:** `posAnywhere/backend`
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Environment:** `DATABASE_URL` = the Postgres string (use the `postgresql+psycopg2://` prefix).
4. After the first deploy, run the seed once from the Render shell:
   `python -m app.seed`.

### Option C — Fly.io
```bash
fly launch --dockerfile backend/Dockerfile   # generates fly.toml
fly postgres create && fly postgres attach <db-app>   # sets DATABASE_URL
fly deploy
```

### Option D — Heroku
```bash
heroku create
heroku addons:create heroku-postgresql:essential-0
# Heroku sets DATABASE_URL as postgres://…  — the app rewrites the scheme if needed.
git push heroku main
```

> **Production notes (from architecture §9):** terminate TLS at a load
> balancer/API gateway, keep payment data with a PCI-compliant provider (never
> store cards), encrypt PII at rest, and run schema migrations (e.g. Alembic)
> instead of `create_all()` once the schema stabilises.

---

## API quick reference

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/orders` | Place a new order (prices delivery + computes ETA) |
| GET | `/api/orders` | List orders (filter by `location_id`, `status`) |
| POST | `/api/orders/{id}/status?status=…` | Advance order status |
| POST | `/api/orders/{id}/deliver` | Mark delivered + free the driver |
| POST | `/api/dispatch/quote` | Get delivery fee + ETA for an address |
| POST | `/api/dispatch/run` | Batch pending orders into runs and assign drivers |
| GET | `/api/drivers` | List drivers + live positions |
| POST | `/api/drivers/{id}/status?status=…` | Set driver availability |
| WS | `/api/drivers/{id}/gps` | Stream `{lat,lng}` GPS pings |
| GET | `/api/tracking/{token}` | Customer tracking snapshot |
| WS | `/api/tracking/{token}/ws` | Live customer tracking updates |
| POST | `/api/settlements/generate` | Reconcile a driver's shift cash |
| GET | `/api/reports/summary` | Operational summary |
| WS | `/api/dispatch/ws` | Fleet-wide live updates (POS map) |

Interactive docs are always available at `/docs`.
