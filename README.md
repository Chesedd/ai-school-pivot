# AI School Pivot

Content Bank MVP through phase 2.10A: FastAPI/SQLAlchemy task slices, version-scoped methodology,
a transactionally atomic, read-only audit history API,
a filterable React/Vite task table and creation form, and PostgreSQL 17.
Alembic remains the schema owner.

## Local launch

1. Copy the environment template: `cp .env.example .env`.
2. Start the environment: `docker compose up --build -d`.
3. Open `http://localhost:5173/content-bank` or check
   `http://localhost:8000/health`.

Stop it with `docker compose down`. Do not add `-v` unless deliberately
removing the local PostgreSQL data volume.

## Database commands

Run these commands with the environment running. Alembic reads `DATABASE_URL`
from the backend container environment; no database secret is stored in the
migration configuration.

```bash
# Apply the current schema revision.
docker compose exec backend alembic upgrade head

# Revert the most recently applied revision.
docker compose exec backend alembic downgrade -1

# Show the database's current revision.
docker compose exec backend alembic current

# Verify model metadata has no pending migration operations.
docker compose exec backend alembic check

# Seed the idempotent demo/dev catalog (grades 1–11 and one Informatics chain).
docker compose exec backend python -m app.db.seed
```

The task list and creation form are at `/content-bank`. They load the five
read-only catalogs from `/api/content-bank/catalog/{catalog_name}`; the list
uses `GET /api/content-bank/tasks` and the form submits to the same path with
`POST`.

Phase 2.6A adds atomic full replacement at `PUT
/api/content-bank/task-versions/{task_version_id}/methodology`; the task card
returns the saved read model at `latest_version.methodology`. Apply migrations
with `docker compose exec backend alembic upgrade head`. The frontend editor is
intentionally deferred to phase 2.6B.

Phase 2.9A adds `audit_log` and `GET
/api/content-bank/tasks/{task_id}/audit`. Apply revision `20260728_01` before
running the backend tests. A PostgreSQL integration run requires a separately
created database whose name ends in `_test` and `TEST_DATABASE_URL`; do not
seed that test database.

## JSON import preview and commit (2.10A)

CSV/XLSX parsing is intentionally performed by the frontend client in phase
2.10B. The backend accepts normalized JSON and independently validates it.

```bash
curl -sS -H 'Content-Type: application/json' -d @preview.json \
  http://localhost:8000/api/content-bank/imports/preview
# Copy import_token and valid row_number values, then:
curl -sS -H 'Content-Type: application/json' \
  -d '{"import_token":"<token>","row_numbers":[2]}' \
  http://localhost:8000/api/content-bank/imports/commit
```

`preview.json` has `{"format":"csv","rows":[...]}`; each row is the normal
task-create payload plus a stable `row_number`. Preview persists no task or
audit event. Commit atomically creates only the selected valid rows. Tokens
expire after 30 minutes and can be committed once.
