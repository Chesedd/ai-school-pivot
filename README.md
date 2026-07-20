# AI School Pivot

Technical skeleton for the Content Bank MVP: a FastAPI backend, React/Vite
frontend, and PostgreSQL 17. The phase 2.2 backend supplies only database
infrastructure, explicit Alembic migrations, and a demo/dev catalog seed; it
has no Content Bank HTTP API or ORM entity models.

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
