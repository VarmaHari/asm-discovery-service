# ASM Asset Discovery Service

FastAPI service for authenticated domain management, asynchronous DNS discovery,
scan history, and asset inventory. The service uses PostgreSQL, SQLAlchemy,
Alembic, Celery, Redis, and dnspython.

## Architecture

- FastAPI validates requests, authenticates JWTs, applies role permissions, and
  performs database-backed API operations.
- PostgreSQL stores users, domains, scans, and DNS assets. Alembic is the only
  schema-creation mechanism.
- Redis brokers Celery tasks. One worker performs DNS queries outside the
  request-response path; one Beat process retries pending jobs and fails stale
  scans.
- The worker resolves only A, AAAA, NS, and MX records. Asset API responses
  derive `domain_id` through the asset → scan → domain relationship rather
  than storing a second, potentially inconsistent domain reference on assets.

## Requirements and quick start

Use Python **3.12 or newer** for local development, and Docker Engine/Desktop
with the Compose v2 plugin for the container stack.

Create a private `.env`, replacing the local placeholders with generated
values:

```powershell
Copy-Item .env.example .env
$pgPassword = & python -c "import secrets; print(secrets.token_hex(24))"
$jwtSecret = & python -c "import secrets; print(secrets.token_urlsafe(48))"
$envText = Get-Content .env -Raw
$envText = $envText.Replace("replace-with-a-unique-local-database-password", $pgPassword)
$envText = $envText.Replace("replace-with-a-random-secret-of-at-least-32-bytes", $jwtSecret)
Set-Content -Path .env -Value $envText -Encoding Ascii
```

Start the services:

```powershell
docker compose config --quiet
docker compose up --build --wait
docker compose ps
```

Compose waits for PostgreSQL and Redis health, runs a one-shot migration
service, and starts the API, worker, and singleton Beat process only after
migrations complete. Do not scale the migration or Beat service. For a
multi-replica deployment, run migrations once as a release step before
deploying application replicas.

The API is at `http://localhost:8000`; Swagger UI is
`http://localhost:8000/docs`, OpenAPI JSON is `http://localhost:8000/openapi.json`,
and `GET /health` performs a database readiness query.

### Initial Admin account

Registration requires an authenticated Admin. Create the initial Admin through
the protected command-line bootstrap after migrations and API startup:

```powershell
docker compose exec api python -m app.cli bootstrap-admin
```

The command prompts for name, email, and password, and refuses to create a
second Admin through this bootstrap mechanism. There is no public Admin
registration route.

To stop without deleting PostgreSQL data:

```powershell
docker compose down
```

`docker compose down -v` removes the database volume and its data.

## Local development without containerized API

Keep PostgreSQL and Redis running, then run the application, worker, and Beat
in separate terminals:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
docker compose up -d db redis
python -m alembic upgrade head
python -m uvicorn app.main:app --reload
```

In separate terminals:

```powershell
python -m celery -A app.workers.celery_app:celery_app worker --pool=solo --loglevel=INFO
python -m celery -A app.workers.celery_app:celery_app beat --loglevel=INFO
```

Run exactly one Beat process. Create the initial Admin using
`python -m app.cli bootstrap-admin`.

## Configuration

| Variable | Purpose |
|---|---|
| `APP_NAME`, `APP_VERSION` | Service metadata |
| `APP_LOG_LEVEL` | Structured application log level |
| `APP_PORT` | Compose host port for the API |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_PORT` | Compose PostgreSQL settings |
| `DATABASE_URL` | SQLAlchemy PostgreSQL URL |
| `JWT_SECRET_KEY` | HS256 signing key; must contain at least 32 UTF-8 bytes |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Access-token lifetime |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Celery/Redis connection URLs |
| `DNS_LOOKUP_TIMEOUT_SECONDS` | Per-record DNS lookup timeout |
| `SCAN_MAX_RUNTIME_SECONDS` | Threshold for failing stale running scans |

Use unique secrets outside local development. `.env` is ignored by Git and
excluded from the Docker build context. Never commit or log credentials.

## Authentication and authorization

`POST /auth/register` requires an Admin bearer token and accepts `full_name`,
`email`, `password`, and `role` (`ADMIN`, `ANALYST`, or `VIEWER`). New passwords
must be at least 12 characters and include lowercase, uppercase, numeric, and
special characters; bcrypt accepts at most 72 UTF-8 bytes. Passwords are
hashed with bcrypt (cost 12). Existing Argon2id hashes are verified and
upgraded to bcrypt after a successful login.

`POST /auth/login` returns a short-lived HS256 bearer token with `sub`, `iat`,
`exp`, `iss`, and `token_type` claims. Use the token with `GET /auth/me` and
protected operations. Invalid or expired tokens return `401`; inactive users
and authenticated users without permission receive `403`.

| Operation | Admin | Analyst | Viewer |
|---|---:|---:|---:|
| Domain read/create/delete | Yes | Read/create | Read |
| Scan history/manual trigger | Yes | Read/trigger | Read |
| Asset inventory | Yes | Read | Read |
| User registration/administration | Yes | No | No |
| `GET /auth/me` | Authenticated | Authenticated | Authenticated |
| Login, health | Public | Public | Public |

Domains are globally unique after normalization. Admins can view all domains;
Analysts and Viewers see only domains they created.

## API routes

All list routes use one-based `page` and `limit` pagination. The default limit
is 20 and the maximum is 100.

| Method and route | Permission | Description |
|---|---|---|
| `POST /auth/register` | Admin | Create a user with a requested role |
| `POST /auth/login` | Public | Authenticate and issue an access token |
| `GET /auth/me` | Authenticated | Return the current account |
| `POST /domains` | Admin, Analyst | Create a normalized domain and queue its initial scan |
| `GET /domains?page=1&limit=20&status=PENDING&search=example` | All roles | Filter and paginate visible domains |
| `GET /domains/{domain_id}` | All roles | Retrieve one visible domain |
| `DELETE /domains/{domain_id}` | Admin | Delete a domain and its scans/assets |
| `POST /domains/{domain_id}/scan` | Admin, Analyst | Queue a manual scan; returns `202` with `scan_id` and `status` |
| `GET /domains/{domain_id}/scans?page=1&limit=20` | All roles | Paginated scan history |
| `GET /assets?domain_id={id}&record_type=A&page=1&limit=20` | All roles | Filter and paginate visible DNS assets |

Domain creation example:

```json
{
  "domain": "example.com"
}
```

Successful creation returns HTTP `201`, the normalized domain, its creator,
uppercase status, timestamps, and the initial scan scheduling result:

```json
{
  "id": "generated-uuid",
  "domain": "example.com",
  "created_by": "user-uuid",
  "status": "PENDING",
  "created_at": "2026-10-21T10:00:00Z",
  "updated_at": "2026-10-21T10:00:00Z",
  "initial_scan": {
    "id": "scan-uuid",
    "status": "PENDING",
    "scheduled": true
  }
}
```

Manual scan request:

```powershell
curl.exe -X POST "http://localhost:8000/domains/DOMAIN_UUID/scan" `
  -H "Authorization: Bearer ACCESS_TOKEN"
```

Accepted response (`202`):

```json
{
  "scan_id": "scan-uuid",
  "status": "PENDING"
}
```

A manual request conflicts with HTTP `409` if a scan is already running.
Database-backed status values are `PENDING`, `RUNNING`, `COMPLETED`, and
`FAILED`. Domain status follows the current scan lifecycle. Error responses use
a consistent `detail` field; request-validation errors omit submitted values
to avoid reflecting passwords or other sensitive input.

## Tests and verification

Tests use SQLite for database/API integration and mock DNS responses; they do
not require live PostgreSQL, Redis, or external DNS:

```powershell
python -m pip install -r requirements.txt
python -m pytest tests -ra
```

For a clean local database, configure `DATABASE_URL` and run:

```powershell
python -m alembic upgrade head
python -m alembic check
```

The case-insensitive email/domain indexes are database-enforced. Migration
`0006` stops with an actionable error if it finds existing email or domain
duplicates that would violate those constraints; resolve duplicates before
retrying. See [implementation status](docs/IMPLEMENTATION_STATUS.md) for
executed test results and infrastructure limitations.

## Design trade-offs and future improvements

- `assets.domain_id` is derived through the scan relationship rather than
  duplicated in the asset row. This avoids storing contradictory parent IDs;
  the repository uses indexed scan/domain joins for filtering.
- Scans are durable database rows before Celery dispatch. If Redis is
  temporarily unavailable, the API reports the queue failure and Beat can
  redispatch persisted pending work.
- DNS data is limited to the assignment's A, AAAA, NS, and MX record types.
- Future work, outside this assignment's scope, could include operational
  metrics, rate limiting, richer audit retention, and production deployment
  automation.

## Troubleshooting

- **Docker unavailable:** Start Docker Desktop/Engine and confirm `docker info`.
- **PostgreSQL or Redis unhealthy:** Check `docker compose logs db redis`.
- **Migration failure:** Check `docker compose logs migrate`; resolve any
  pre-existing duplicate email/domain data and rerun the one-shot migration.
- **API unhealthy:** Check `docker compose logs api` and confirm `GET /health`
  can reach PostgreSQL.
- **Scans remain pending:** Check `docker compose ps` and `docker compose logs
  redis worker beat`; confirm exactly one Beat and at least one worker are
  healthy.
- **JWT configuration rejected:** Set a random `JWT_SECRET_KEY` of at least
  32 UTF-8 bytes and restart the services.

## Submission checklist

Inspect the Git changes, ensure `.env` and local database files are excluded,
and submit the branch/repository using the assignment's delivery instructions:

```powershell
git status --short
git check-ignore .env
git add -A
git diff --cached --check
git diff --cached --stat
git commit -m "Align ASM discovery service with assignment"
git push -u origin HEAD
```
