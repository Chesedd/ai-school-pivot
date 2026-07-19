# AI School Pivot

Technical skeleton for the Content Bank MVP. The repository contains a FastAPI
backend, a React/Vite frontend, and PostgreSQL 17. Database integration is not
part of this phase.

## Local launch

1. Copy the example environment file: `cp .env.example .env`.
2. Start all services: `docker compose up --build`.
3. Open the frontend at `http://localhost:5173/content-bank` and the backend
   health endpoint at `http://localhost:8000/health`.

Stop the environment with `docker compose down`. Add `-v` only when you also
want to remove the local PostgreSQL data volume.
