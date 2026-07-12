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
    score_threshold = Column(Integer, default=5)
    target_ip = Column(String(64), nullable=True)
    full_sweep = Column(String(64), nullable=True)
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
    Base.metadata.create_all(bind=engine)
