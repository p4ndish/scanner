# OpenCode Scanner

Masscan-powered scanner to discover **opencode web servers** and **LLM APIs** running across cloud provider IP ranges. Features a modern web UI with real-time scan progress, single-IP targeting, and auto-recommended settings based on your machine's resources.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick Start (Web UI)](#quick-start-web-ui)
- [Quick Start (CLI)](#quick-start-cli)
- [Web UI Usage](#web-ui-usage)
- [CLI Usage](#cli-usage)
- [Deployment](#deployment)
- [Development](#development)
- [Troubleshooting](#troubleshooting)

---

## Features

- **Cloud-wide scanning** — Scan 17 cloud providers (~440M IPs) with masscan + zmap pre-filter
- **Single-IP targeting** — Scan one specific IP with full port sweep or selected ports
- **Multi-method fingerprinting** — 7 detection methods scoring from 1-17 confidence
- **LLM mode** — Hunt for Ollama, vLLM, llama.cpp, Kobold, and other LLM servers
- **Two-phase full sweep** — Fast known-port scan, then full range only on confirmed IPs
- **Modern Web UI** — React + Tailwind + Vite with live logs via SSE
- **Auto-recommended settings** — Rate/workers/parallel tuned to your CPU and RAM
- **Scan cancellation** — Cancel queued or running scans from the web UI
- **JWT authentication** — Multi-user support with persistent scan history
- **Dockerized** — Single `docker compose up` deployment

---

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
                    │  (masscan)   │
                    └─────────────┘
```

**Scan phases:**
1. **zmap ICMP** pre-filter (optional) → find alive hosts
2. **masscan** port scan → candidate IP:port pairs
3. **HTTP fingerprint** → confirmed opencode/LLM servers
4. **Full sweep** (optional) → scan full port range on confirmed IPs only

---

## Quick Start (Web UI)

### Prerequisites

- Docker + Docker Compose
- Linux host with `NET_RAW` capability (for masscan)

### 1. Build the frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

### 2. Start the stack

```bash
docker compose up --build -d
```

### 3. Create your first user

```bash
curl -X POST http://localhost:8088/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","email":"admin@example.com","password":"adminpass"}'
```

### 4. Open the app

Go to `http://localhost:8088` in your browser and log in.

---

## Quick Start (CLI)

### Prerequisites

- Python 3.10+
- [masscan](https://github.com/robertdavidgraham/masscan) (`apt install masscan`)
- [sudo](https://en.wikipedia.org/wiki/Sudo) (masscan needs raw socket access)
- Optional: [zmap](https://zmap.io/) for ICMP pre-filter (`apt install zmap`)

```bash
pip install -r requirements.txt
```

### Run your first scan

```bash
# Dry run — see what will be scanned
python3 scanner.py --all --dry-run

# Scan a single provider (fastest)
python3 scanner.py --providers digitalocean

# Scan multiple providers
python3 scanner.py --providers hetzner,ovh_cloud,scaleway
```

---

## Web UI Usage

### Dashboard

The dashboard shows scan statistics, recent matches, and active scan count.

### Creating a Scan

1. Click **New Scan** in the sidebar
2. Choose **scan target**:
   - **Cloud Providers** — Select one or more providers from the grid
   - **Single IP** — Enter a specific IPv4 address (e.g. `8.8.8.8`)
3. Toggle **LLM mode** if hunting for LLM APIs instead of opencode
4. Choose **port preset** or enter custom ports (comma-separated)
5. Set **full sweep range** (optional) — e.g. `1-65535` or `3000-65535`
6. Check **System Resources** panel for auto-recommended settings
7. Click **Start Scan**

### System Recommendations

The web UI automatically detects your machine's CPU cores and RAM, then recommends optimal masscan settings:

| Mode | Rate | Workers | Parallel | Description |
|------|------|---------|----------|-------------|
| Single IP | `10,000 * cores` (max 100k) | `min(cores, 16)` | `max(1, cores/4)` | Fast port discovery on one host |
| Cloud | `2,500 * cores` (max 20k) | `min(cores, 16)` | `max(1, cores/2)` | Balanced across massive IP ranges |

Click **"Use recommended settings"** to auto-fill rate, workers, and parallel.

### Watching Progress

Click any scan in the list to view:
- **Live terminal** — Real-time masscan + fingerprint logs via SSE
- **Stats cards** — Candidates found, matches confirmed, duration
- **Matches table** — IP:Port, service, provider, score, detection methods

### Cancelling a Scan

While a scan is **queued** or **running**, a red **Cancel** button appears in the scan detail page. Click it to stop the scan. The worker will exit cleanly after finishing the current masscan batch.

### Scan History

All scans are persisted to PostgreSQL. View past scans, their results, and logs at any time. Delete scans you no longer need.

---

## CLI Usage

### Provider Selection

| Command | Description |
|---|---|
| `--all` | Scan all 17 providers (~440M IPs) |
| `--providers aws,google_cloud` | Scan specific providers (comma-separated) |
| `--providers alibaba_cloud,tencent_cloud` | Chinese cloud providers |
| `--providers hetzner,ovh_cloud,scaleway` | European providers (~10M IPs, fastest) |

### Port Scanning

| Command | Description |
|---|---|
| `--ports 4096` | Only scan opencode default port (fastest) |
| `--ports 4096,3000,8080` | Known opencode ports (default) |
| `--ports 80,443,3000,4096,8080` | Extended scan including HTTP/HTTPS |
| `--llm-mode` | Switch to LLM ports (11434, 8080, 8000, 5000, etc.) |

### Performance Tuning

| Command | Description |
|---|---|
| `--rate 5000` | Packets/sec per masscan instance (default: 2500) |
| `--parallel 8` | Concurrent masscan instances (default: 4) |
| `--workers 8` | Port-range workers — splits port list into N chunks |
| `--batch-ips 1000000` | Target IPs per batch (default: 5M) |
| `--retry 1` | Max retries for zero-hit batches (default: 2) |

> **Total masscan processes** = `--parallel` x `--workers`. Example: `--parallel 4 --workers 8` = 32 concurrent masscan processes.

### Network Configuration

| Command | Description |
|---|---|
| `--interface eth0` | Network interface for masscan |
| `--router-ip 10.0.0.1` | Router IP (needed for bond interfaces) |
| `--sudo` / `--no-sudo` | Control sudo usage |

### Fingerprint

| Command | Description |
|---|---|
| `--http-concurrency 1000` | Concurrent HTTP probes (default: 500) |
| `--score 7` | Minimum confidence score (default: 5, max: 17) |
| `--high-confidence` | Shortcut for `--score 13` (zero false positives) |
| `--min-version 1.14.0` | Only report matches >= this version |

### Pre-filter

| Command | Description |
|---|---|
| `--skip-ping` | Skip zmap ICMP pre-filter |
| `--force-zmap` | Force re-run zmap even if cached |
| `--zmap-rate 500000` | zmap packets/sec (default: 250000) |

### Two-Phase Full Sweep

| Command | Description |
|---|---|
| `--full-sweep` | Scan known ports first, then `3000-65535` on confirmed IPs |
| `--full-sweep 1-65535` | Custom sweep range |

> Two-phase mode turns a 60-hour 65,535-port scan into ~15 minutes by only sweeping IPs that already have a fingerprint match.

### Output

| Command | Description |
|---|---|
| `--output custom_dir` | Output directory (default: `results/`) |
| `--dry-run` | Show summary only, don't scan |

---

## CLI Use Cases

### 1. Find opencode servers on DigitalOcean

```bash
python3 scanner.py --providers digitalocean --ports 4096 --rate 5000
```
**ETA:** ~10 minutes (3.1M IPs, 1 port)

### 2. Scan European providers

```bash
python3 scanner.py --providers ovh_cloud,hetzner,scaleway,ionos --rate 3000
```
**ETA:** ~20 minutes (10M IPs, 3 ports)

### 3. Hunt LLM APIs across Chinese cloud

```bash
python3 scanner.py --providers alibaba_cloud,tencent_cloud,huawei_cloud \
  --llm-mode --rate 5000 --parallel 8
```

### 4. AWS-only (very large)

```bash
python3 scanner.py --providers aws --ports 4096 --rate 10000 --parallel 8
```
**ETA:** ~3 hours (233M IPs, 1 port)

### 5. Aggressive fast scan with full sweep

```bash
python3 scanner.py --providers vultr,digitalocean \
  --ports 4096,3000,80,443 --rate 10000 --parallel 16 --full-sweep
```

### 6. Single IP full port scan

```bash
# Scan one IP on all ports (CLI doesn't support single-IP mode natively — use web UI)
# Or use masscan directly:
masscan 8.8.8.8 -p1-65535 --rate 50000
```

### 7. Low-confidence brute search

```bash
python3 scanner.py --providers hetzner --score 3 --ports 80,443,3000,4096,8080
```

### 8. Dry run before committing

```bash
python3 scanner.py --all --dry-run
```

### 9. Two-phase full sweep

```bash
python3 scanner.py --providers hetzner --full-sweep --high-confidence
```

### 10. mDNS passive discovery

```bash
python3 opencode_mdns.py --timeout 60 --fingerprint
```

---

## Fingerprint Methods

| # | Method | Weight | What it checks |
|---|--------|--------|----------------|
| 1 | `/global/health` | **5** | JSON `{"healthy":true,"version":"x.y.z"}` |
| 2 | `/doc` | **4** | OpenAPI 3.1 spec with opencode operationIds |
| 3 | `/path` | **4** | JSON `{home, state, config, worktree, directory}` |
| 4 | `/doc` title | **3** | OpenAPI spec info contains "OpenCode" |
| 5 | Auth realm | **2** | `WWW-Authenticate: Basic realm="opencode"` |
| 6 | Error shape | **2** | Structured JSON error responses |
| 7 | Port hint | **1** | Known opencode ports (4096, 3000, 8080) |

**Match threshold:** score >= 5 (configurable via `--score` or web UI)

---

## Provider Reference

| Provider | Region | Prefixes | Est. IPs |
|---|---|---|---|
| AWS | us | 16,791 | 233.8M |
| Google Cloud | us | 4,598 | 52.1M |
| Microsoft Azure | us | 1,154 | 81.8M |
| Akamai/Linode | us | 4,278 | 16.9M |
| Tencent Cloud | cn | 3,317 | 14.8M |
| Oracle Cloud | us | 2,317 | 5.4M |
| Cloudflare | us | 2,466 | 1.5M |
| Alibaba Cloud | cn | 1,677 | 10.7M |
| Vultr | us | 1,659 | 1.4M |
| DigitalOcean | us | 861 | 3.1M |
| Huawei Cloud | cn | 930 | 2.8M |
| IBM Cloud | us | 338 | 3.8M |
| OVH Cloud | eu | 677 | 4.6M |
| Baidu Cloud | cn | 182 | 0.7M |
| Hetzner | eu | 86 | 3.0M |
| Scaleway | eu | 40 | 2.5M |
| IONOS | eu | 520 | 0.9M |

---

## Deployment

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+psycopg2://scanner:scannerpass@db:5432/opencode_scanner` | PostgreSQL connection |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `SECRET_KEY` | `change-me-in-production` | JWT signing key |

### Production Checklist

1. **Change `SECRET_KEY`** in `docker-compose.yml` to a long random string
2. **Disable CORS wildcard** in `backend/app/main.py`
3. **Add SSL/TLS** via Nginx certificates or reverse proxy (Traefik, Caddy)
4. **Disable `--reload`** in the web container command
5. **Use a strong password** for PostgreSQL
6. **Firewall**: only expose port 8088/443 and 22 (SSH). Do not expose 5432 or 6379 to the internet.

### VPS Requirements

- Linux with kernel >= 4.x
- `NET_RAW` and `NET_ADMIN` capabilities (or run worker with `--privileged`)
- Minimum 2 CPU cores, 4 GB RAM (recommended: 4+ cores, 8+ GB)
- ~10 GB disk for masscan JSON outputs

---

## Development

### Local Backend

```bash
docker compose up -d db redis
source venv/bin/activate
export DATABASE_URL=postgresql+psycopg2://scanner:scannerpass@localhost:5432/opencode_scanner
export REDIS_URL=redis://localhost:6379/0
uvicorn backend.app.main:app --reload
```

### Local Frontend

```bash
cd frontend
npm run dev
```

### Local Celery Worker

```bash
source venv/bin/activate
export DATABASE_URL=postgresql+psycopg2://scanner:scannerpass@localhost:5432/opencode_scanner
export REDIS_URL=redis://localhost:6379/0
celery -A backend.app.worker.celery_app worker --loglevel=info --concurrency=1
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/auth/register` | POST | Create user |
| `/api/auth/login` | POST | Get JWT token |
| `/api/auth/me` | GET | Current user |
| `/api/system/info` | GET | CPU/RAM + recommended settings |
| `/api/scans` | GET | List scans |
| `/api/scans` | POST | Create scan |
| `/api/scans/{id}` | GET | Scan detail |
| `/api/scans/{id}/cancel` | POST | Cancel scan |
| `/api/scans/{id}/logs` | GET | Scan logs |
| `/api/scans/{id}/events` | GET | SSE live events |

---

## Troubleshooting

### masscan fails in worker

The worker container needs raw socket access. On Linux, `cap_add: [NET_RAW, NET_ADMIN]` is usually sufficient. If masscan still fails with "adapter" errors, try running the worker with `network_mode: host` (but then you must point `DATABASE_URL` and `REDIS_URL` to the host's IP, not Docker service names).

### Frontend not updating after changes

Rebuild it:
```bash
cd frontend && npm run build
docker compose restart nginx
```

### Database migrations

Tables are auto-created on startup. For proper migrations, install Alembic:
```bash
docker compose exec web alembic revision --autogenerate -m "message"
docker compose exec web alembic upgrade head
```

### "daemonic processes" error

If you see `AssertionError: daemonic processes are not allowed to have children`, ensure you're using the latest `masscan_runner.py` which uses `ThreadPoolExecutor` instead of `ProcessPoolExecutor`.

---

## License

MIT
