# AI School Pivot

Content Bank MVP phase 2.3: a FastAPI/SQLAlchemy create-task vertical slice,
React/Vite creation form, and PostgreSQL 17. Alembic remains the schema owner.

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

The creation form is at `/content-bank`; it loads the five read-only catalogs
from `/api/content-bank/catalog/{catalog_name}` and submits to
`/api/content-bank/tasks`.
