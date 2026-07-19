import json
from datetime import datetime
from typing import Any

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, JSON, create_engine
from sqlalchemy.orm import relationship

from backend.app.database import Base, engine


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, index=True, nullable=False)
    email = Column(String(128), unique=True, index=True, nullable=False)
    hashed_password = Column(String(128), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    scans = relationship("ScanJob", back_populates="owner", cascade="all, delete-orphan")
    machines = relationship("ScanMachine", back_populates="owner", cascade="all, delete-orphan")
    proxies = relationship("ProxyConfig", back_populates="owner", cascade="all, delete-orphan")


class ScanMachine(Base):
    __tablename__ = "scan_machines"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(128), nullable=False)
    host = Column(String(256), nullable=False)
    port = Column(Integer, default=22, nullable=False)
    username = Column(String(128), nullable=False)
    auth_type = Column(String(16), default="key", nullable=False)  # key | password
    encrypted_secret = Column(Text, nullable=False)  # Fernet-encrypted private key or password
    use_sudo = Column(Boolean, default=False, nullable=False)
    last_tested_at = Column(DateTime, nullable=True)
    last_test_ok = Column(Boolean, nullable=True)
    last_test_message = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="machines")


class ProxyConfig(Base):
    __tablename__ = "proxy_configs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(128), nullable=False)
    scheme = Column(String(16), default="http", nullable=False)  # http | https | socks5 | socks5h
    host = Column(String(256), nullable=False)
    port = Column(Integer, nullable=False)
    username = Column(String(128), nullable=True)
    encrypted_password = Column(Text, nullable=True)  # Fernet-encrypted, optional
    last_tested_at = Column(DateTime, nullable=True)
    last_test_ok = Column(Boolean, nullable=True)
    last_test_message = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="proxies")


class ScanJob(Base):
    __tablename__ = "scan_jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(256), nullable=False)
    status = Column(String(32), default="pending", nullable=False)  # pending, queued, running, completed, failed, cancelled
    providers = Column(JSON, default=list)
    ports = Column(JSON, default=list)
    llm_mode = Column(Boolean, default=False)
    rate = Column(Integer, default=2500)
    workers = Column(Integer, default=1)
    parallel = Column(Integer, default=4)
    retry = Column(Integer, default=1)
    score_threshold = Column(Integer, default=5)
    target_ip = Column(String(64), nullable=True)
    full_sweep = Column(String(64), nullable=True)
    machine_id = Column(Integer, ForeignKey("scan_machines.id"), nullable=True)
    stats_json = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="scans")
    matches = relationship("Match", back_populates="scan_job", cascade="all, delete-orphan")
    logs = relationship("ScanLog", back_populates="scan_job", cascade="all, delete-orphan", order_by="ScanLog.created_at")


class Match(Base):
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    scan_job_id = Column(Integer, ForeignKey("scan_jobs.id"), nullable=False)
    ip = Column(String(64), nullable=False)
    port = Column(Integer, nullable=False)
    scheme = Column(String(16), default="http")
    score = Column(Integer, default=0)
    service = Column(String(64), default="unknown")
    provider = Column(String(64), nullable=True)
    region = Column(String(16), nullable=True)
    methods_hit = Column(JSON, default=list)
    details_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Verification fields
    verified_status = Column(String(16), default="pending", nullable=False)
    verified_at = Column(DateTime, nullable=True)
    verification_details = Column(JSON, default=dict)
    model_type = Column(String(16), nullable=True)

    scan_job = relationship("ScanJob", back_populates="matches")


class ScanLog(Base):
    __tablename__ = "scan_logs"

    id = Column(Integer, primary_key=True, index=True)
    scan_job_id = Column(Integer, ForeignKey("scan_jobs.id"), nullable=False)
    phase = Column(String(64), nullable=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    scan_job = relationship("ScanJob", back_populates="logs")


def init_db():
    """Create tables + apply additive migrations.

    Safe under concurrent worker startups (uvicorn --workers N) via a Postgres
    advisory lock: only one worker creates tables, others wait then find them
    already present (checkfirst skips).
    """
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_lock(727272)"))
        try:
            Base.metadata.create_all(bind=conn, checkfirst=True)
            _migrate_locked(conn)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(727272)"))


def _migrate_locked(conn):
    """Apply lightweight additive migrations. create_all only creates missing
    tables, not missing columns, so we add nullable columns when absent.
    Runs under the init_db advisory lock.
    """
    from sqlalchemy import inspect, text
    insp = inspect(conn)
    if "scan_jobs" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("scan_jobs")}
        if "machine_id" not in cols:
            conn.execute(text("ALTER TABLE scan_jobs ADD COLUMN IF NOT EXISTS machine_id INTEGER NULL"))
        if "retry" not in cols:
            conn.execute(text("ALTER TABLE scan_jobs ADD COLUMN IF NOT EXISTS retry INTEGER DEFAULT 1"))

