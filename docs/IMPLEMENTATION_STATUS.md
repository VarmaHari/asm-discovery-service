# Implementation Status

## Assignment alignment

The implementation has been updated against the assignment text supplied in
the conversation. The prior brief-only/provisional status was obsolete.

| Requirement | Implementation and evidence |
|---|---|
| Runtime baseline | Docker image now uses Python 3.12; README documents Python 3.12+. Local runtime/test versions are reported below. |
| User model and registration | Users include `full_name`; Admin-authenticated registration accepts `full_name`, email, password, and `ADMIN`/`ANALYST`/`VIEWER`. CLI bootstrap creates the initial Admin. Public registration is rejected. |
| Password security | New password hashes use bcrypt cost 12; registration enforces length and complexity. Existing Argon2id hashes are verified and re-hashed with bcrypt after successful login. |
| Authentication and roles | JWT claims, expiry, `get_current_user`, active account checks, shared Admin/Analyst/Viewer permission matrix, and `401`/`403` distinction remain in place. |
| Email/domain uniqueness | Case-insensitive unique database indexes protect emails and globally unique normalized domains. Migration `0006` checks for duplicates before applying those indexes. |
| Domain management | Request/response uses `domain`, `created_by`, uppercase domain status; status/search filtering and one-based `page`/`limit` pagination are database-backed. Domain creation persists and queues an initial scan. |
| Scan lifecycle and APIs | Status values use `PENDING`/`RUNNING`/`COMPLETED`/`FAILED`; manual trigger is `POST /domains/{domain_id}/scan` and returns only `scan_id`/`status`; history uses `page`/`limit`. Domain status is refreshed from persisted scan states. |
| Asset inventory | Paginated and filtered by domain and record type. Domain association is derived through the scan join to avoid denormalized inconsistent IDs. |
| Database and migration | Migration `0006_assignment_contract_alignment` adds user name, creator and trigger fields, domain status, role/status normalization, case-insensitive uniqueness, and indexes. Alembic clean migration tests exercise SQLite. |
| Error handling and logs | Central HTTP/validation/unexpected-error handlers retain a consistent error shape and do not reflect submitted values. Request, auth, worker, and error events are emitted in structured JSON without credentials. |
| Docker and documentation | Docker Compose includes PostgreSQL, Redis, one-shot migrations, API, worker, and singleton Beat; README documents setup, roles, routes, migrations, bootstrap, tests, trade-offs, and troubleshooting. |

## Verification performed

- Full-suite command:

  ```powershell
  .\myenv\Scripts\python.exe -m pytest tests -ra
  ```

- Final full-suite result: **85 passed, 0 failed, 0 skipped** (85 collected),
  under Python 3.9.7.
- Clean SQLite migration command:
  `python -m alembic upgrade head` followed by `python -m alembic check`.
  Result: migration `0006_assignment_contract_alignment` completed and Alembic
  reported **No new upgrade operations detected**.
- A migration regression test upgrades an existing `0005` schema and verifies
  email/role normalization, full-name backfill, domain creator/name/status
  backfill, scan status, and `triggered_by`.
- `docker compose config --quiet` passed using a temporary `.env`; the
  temporary file was removed afterward.
- Workspace Pylance diagnostics: **0 issues**.
- The final clean-migration test emits expected SQLite/Alembic warnings
  because SQLite reflection does not expose expression-based index details.
- PostgreSQL-native migration/constraint behavior: **not verified**; no live
  PostgreSQL service was available.
- Redis/Celery live integration: **not verified**; worker behavior is tested
  with mocked DNS and local persistence fixtures.
- Docker image build and end-to-end Compose startup: **not verified** because
  Docker Engine was unavailable previously. Re-run when available:

  ```powershell
  docker compose config --quiet
  docker compose up --build --wait
  docker compose ps
  ```

## Remaining limitations and submission tasks

- A case-insensitive duplicate email or domain already present in an existing
  database prevents migration `0006`; resolve duplicate rows before retrying.
- Live PostgreSQL, Redis/Celery, and Docker runtime verification remain
  outstanding; passing SQLite/mocked tests does not replace those checks.
- No Python 3.12 interpreter is installed in this workspace. The test suite
  ran under Python 3.9.7; the required Docker image is configured for Python
  3.12, but a 3.12 build/runtime remains unverified.
- The repository currently has no Git commits or verified remote submission.
  Stage, review, commit, and push to the assignment-designated repository and
  grant the requested access.

## Submission checklist

- [x] Source, tests, migrations, Docker/Compose configuration, `.env.example`,
  README, requirements, and status documentation are present.
- [x] Credentials are not embedded in source or `.env.example`.
- [x] Relevant API contracts, schemas, roles, data model, and migration were
  aligned with the supplied assignment text.
- [x] Full test suite and clean SQLite migration/metadata checks passed.
- [x] Compose configuration parses; Pylance reports no workspace diagnostics.
- [ ] Live PostgreSQL/Redis and Docker end-to-end verification.
- [ ] Commit and push to the assignment-designated GitHub repository; verify
  required reviewer access.
