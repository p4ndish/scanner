# OpenCode Scanner — Web UI

Dockerized web application for the OpenCode Scanner with a modern React frontend, FastAPI backend, PostgreSQL database, and Celery workers.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   React UI  │────▶│ FastAPI API │────▶│  PostgreSQL │
│  (Tailwind) │◀────│   (web)     │◀────│   (data)    │
└─────────────┘     └──────┬──────┘     └─────────────┘
                           │
                    ┌──────▼──────┐
                    │    Redis    │
                    │   (queue)   │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ Celery Worker│
                    │  (scanner)   │
                    └─────────────┘
```

## Quick Start

### 1. Build the frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

### 2. Start the stack

```bash
docker-compose up --build -d
```

### 3. Create your first user

```bash
curl -X POST http://localhost:8088/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","email":"admin@example.com","password":"adminpass"}'
```

### 4. Open the app

Go to `http://localhost:8088` in your browser and log in.

## Services

| Service | URL | Description |
|---------|-----|-------------|
| Web UI | `http://localhost:8088` | React frontend via Nginx |
| API Docs | `http://localhost:8088/api/docs` | FastAPI Swagger UI |
| API | `http://localhost:8088/api` | FastAPI backend |
| Postgres | `localhost:5432` | Database (exposed for debugging) |
| Redis | `localhost:6379` | Celery broker (exposed for debugging) |

## Scanning from the Web

1. Click **New Scan** in the sidebar
2. Select providers (e.g., `tencent_cloud`)
3. Choose LLM mode or opencode mode
4. Set ports or use presets
5. Configure advanced options (rate, workers, parallel) if needed
6. Click **Start Scan**
7. Watch live progress in the scan detail page

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+psycopg2://scanner:scannerpass@db:5432/opencode_scanner` | PostgreSQL connection |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `SECRET_KEY` | `change-me-in-production` | JWT signing key |

## Production Notes

1. **Change `SECRET_KEY`** in `docker-compose.yml` to a long random string
2. **Disable CORS wildcard** in `backend/app/main.py`
3. **Add SSL/TLS** by mounting certificates into Nginx or using a reverse proxy (Traefik, Caddy)
4. **Disable `--reload`** in the web container command
5. **Use a strong password** for PostgreSQL
6. **Firewall**: only expose port 8088/443 and 22 (SSH). Do not expose 5432 or 6379 to the internet.

## Troubleshooting

### masscan fails in worker

The worker container needs raw socket access. On Linux, `cap_add: [NET_RAW, NET_ADMIN]` is usually sufficient. If masscan still fails with "adapter" errors, try running the worker with `network_mode: host` (but then you must point `DATABASE_URL` and `REDIS_URL` to the host's IP, not Docker service names).

### Frontend not updating after changes

Rebuild it:
```bash
cd frontend && npm run build
```

Then restart nginx:
```bash
docker-compose restart nginx
```

### Database migrations

For now, tables are auto-created on startup. For proper migrations, install Alembic and run:
```bash
docker-compose exec web alembic revision --autogenerate -m "message"
docker-compose exec web alembic upgrade head
```

## Development

Run the backend locally (requires PostgreSQL and Redis running via Docker):

```bash
docker-compose up -d db redis
source venv/bin/activate
export DATABASE_URL=postgresql+psycopg2://scanner:scannerpass@localhost:5432/opencode_scanner
export REDIS_URL=redis://localhost:6379/0
uvicorn backend.app.main:app --reload
```

Run the frontend locally:

```bash
cd frontend
npm run dev
```

Run the Celery worker locally:

```bash
source venv/bin/activate
export DATABASE_URL=postgresql+psycopg2://scanner:scannerpass@localhost:5432/opencode_scanner
export REDIS_URL=redis://localhost:6379/0
celery -A backend.app.worker.celery_app worker --loglevel=info --concurrency=1
```
