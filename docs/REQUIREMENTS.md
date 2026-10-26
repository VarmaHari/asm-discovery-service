# Assignment Requirements

**Source:** assignment text supplied in the conversation. This file replaces
the earlier provisional brief-only notes.

## Runtime and technology

- Python 3.12 or newer; FastAPI, Pydantic v2, PostgreSQL, SQLAlchemy 2.x, and
  Alembic migrations.
- JWT authentication, bcrypt password hashing, and environment-based secrets.
- Celery/Redis for background DNS work; dnspython for DNS lookups.
- Dockerfile and Docker Compose for the API, database, and worker services.
- Automated tests for API, authorization, persistence, DNS, and worker
  behavior. External DNS must be mocked in tests.

## Authentication and users

- Store user email, `full_name`, a password hash, role, active state, and
  timestamps. Email must be unique at the database level.
- Registration accepts `full_name`, email, password, and role; it is available
  only to an authenticated Admin.
- Do not expose public Admin registration. Provide a secure initial Admin
  bootstrap procedure.
- Hash new passwords with bcrypt; enforce minimum length and basic complexity.
- Login validates credentials without revealing whether an email exists.
- Access tokens are signed JWTs with required claims and expiration. A reusable
  `get_current_user` dependency validates tokens and active accounts.
- Return `401` for missing/invalid authentication and `403` for an authenticated
  user who is inactive or lacks permission.

## Roles and authorization

Role values are `ADMIN`, `ANALYST`, and `VIEWER`. Enforce access in API
dependencies; frontend behavior and Swagger visibility are not authorization.

| Operation | Admin | Analyst | Viewer |
|---|---:|---:|---:|
| Domain read/create/update/delete | Yes | Read/create | Read |
| Scan read/create/update/delete | Yes | Read/create | Read |
| Asset read/create/update/delete | Yes | Read | Read |
| User administration | Yes | No | No |
| Current-user profile | Authenticated | Authenticated | Authenticated |
| Login and health | Public | Public | Public |
| Registration | Authenticated Admin | No | No |

Only operations actually defined by this service are exposed; do not add
unsupported domain discovery features.

## Domain management

- Create, list, retrieve, and delete normalized, validated domain names.
- Prevent duplicate domains globally at the database level.
- Domain status is one of `PENDING`, `RUNNING`, `COMPLETED`, or `FAILED`.
- Domain creation automatically creates/queues its initial scan; a queue
  failure must be reported and must not be represented as a completed scan.
- Domain listing supports `page`, `limit`, `status`, and `search`.
- Scope non-Admin domain visibility to domains created by that user; Admins
  can inspect all domains.

## DNS discovery, scans, and assets

- Resolve only A, AAAA, NS, and MX DNS records, using bounded lookup timeouts.
- Classify missing records, DNS errors/timeouts, and unexpected failures.
- Execute lookups asynchronously outside the API request-response path.
- Persist job/scan transitions and assets. Duplicate job delivery must not
  repeat a terminal scan or create duplicate assets.
- A domain may have at most one `RUNNING` scan at a time. Failed jobs must not
  remain `RUNNING` indefinitely or be marked successful.
- `POST /domains/{id}/scan` manually queues a scan and returns the required
  accepted response; return a conflict if a scan is already running.
- Provide paginated scan history at `GET /domains/{id}/scans`.
- Provide paginated asset inventory with domain and DNS record-type filters.

## Persistence and reliability

- Use foreign keys, unique/check constraints, and indexes for required
  relationships, uniqueness, filtering, and scan concurrency.
- Use transaction-safe database operations; close request-scoped sessions.
- Run `alembic upgrade head` successfully on a clean database.
- Handle database, queue, and worker errors explicitly. Do not leak passwords,
  JWT secrets, or raw database errors through API responses.
- Emit structured request, authentication, worker-lifecycle, and error logs.
- Return consistent HTTP error response structures.

## Delivery and documentation

- Docker Compose starts PostgreSQL, the API, and worker infrastructure,
  including Redis when required by the Celery architecture.
- Include database readiness checks and a deliberate migration procedure that
  does not run concurrently in application replicas.
- README documents install/start commands, environment variables, migrations,
  initial Admin setup, Swagger, tests, troubleshooting, architecture,
  trade-offs, and future improvements.
- Provide `.env.example` without real credentials. Do not commit secrets.
- The final submission includes source, migrations, tests, Docker files,
  documentation, and the GitHub repository/commit history/access requested in
  the assignment.
