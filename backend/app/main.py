from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.database import engine
from backend.app.models import Base, init_db
import os
import multiprocessing

from backend.app.api import auth, scans, matches, machines, proxies
from backend.app.sse import router as sse_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables + apply lightweight migrations
    init_db()
    yield
    # Shutdown: nothing special


app = FastAPI(
    title="OpenCode Scanner",
    description="Web UI for masscan-powered cloud scanner",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict to your domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth")
app.include_router(scans.router, prefix="/api")
app.include_router(matches.router, prefix="/api")
app.include_router(machines.router, prefix="/api")
app.include_router(proxies.router, prefix="/api")
app.include_router(sse_router, prefix="/api/scans")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/system/info")
def system_info():
    """Return machine resource info and recommended scan settings."""
    cpu_count = multiprocessing.cpu_count()

    # Memory in GB
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_kb = int(line.split()[1])
                    mem_gb = round(mem_kb / (1024 * 1024), 1)
                    break
            else:
                mem_gb = 4.0
    except Exception:
        mem_gb = 4.0

    # Recommendations for cloud scans (many IPs)
    cloud_workers = min(cpu_count, 16)
    cloud_parallel = min(max(1, cpu_count // 2), 8)
    cloud_rate = min(2500 * cpu_count, 20000)

    # Recommendations for single-IP scans (single target)
    single_rate = min(10000 * cpu_count, 100000)
    single_workers = min(cpu_count, 16)
    single_parallel = max(1, min(cpu_count // 4, 2))

    return {
        "cpu_count": cpu_count,
        "memory_gb": mem_gb,
        "recommendations": {
            "cloud": {
                "rate": cloud_rate,
                "workers": cloud_workers,
                "parallel": cloud_parallel,
                "description": f"{cloud_parallel} parallel instances × {cloud_workers} port workers = {cloud_parallel * cloud_workers} total masscan processes",
            },
            "single_ip": {
                "rate": single_rate,
                "workers": single_workers,
                "parallel": single_parallel,
                "description": f"{single_parallel} parallel instances × {single_workers} port workers = {single_parallel * single_workers} total masscan processes",
            },
        },
    }
