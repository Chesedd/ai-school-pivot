# AI School Pivot

Content Bank MVP phase 2.6A: FastAPI/SQLAlchemy task slices and version-scoped methodology,
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
