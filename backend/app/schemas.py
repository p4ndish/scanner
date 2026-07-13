from datetime import datetime
from typing import Optional, List, Any

from pydantic import BaseModel, ConfigDict


# ─── Auth ───

class UserCreate(BaseModel):
    username: str
    email: str
    password: str


class UserLogin(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ─── Scan Jobs ───

class ScanJobCreate(BaseModel):
    name: str
    providers: Optional[List[str]] = None
    target_ip: Optional[str] = None
    ports: Optional[List[str]] = None
    llm_mode: bool = False
    rate: int = 2500
    workers: int = 1
    parallel: int = 4
    score_threshold: int = 5
    full_sweep: Optional[str] = None


class ScanJobOut(BaseModel):
    id: int
    user_id: int
    name: str
    status: str
    providers: Optional[List[str]]
    target_ip: Optional[str]
    ports: Optional[List[str]]
    llm_mode: bool
    rate: int
    workers: int
    parallel: int
    score_threshold: int
    full_sweep: Optional[str]
    stats_json: Optional[dict]
    error_message: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScanJobDetailOut(ScanJobOut):
    matches: List["MatchOut"] = []

    model_config = ConfigDict(from_attributes=True)


class ScanJobList(BaseModel):
    id: int
    name: str
    status: str
    providers: Optional[List[str]]
    target_ip: Optional[str]
    llm_mode: bool
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime
    match_count: int = 0

    model_config = ConfigDict(from_attributes=True)


# ─── Matches ───

class ScanJobRef(BaseModel):
    id: int
    llm_mode: bool
    name: str

    model_config = ConfigDict(from_attributes=True)


class MatchOut(BaseModel):
    id: int
    scan_job_id: int
    ip: str
    port: int
    scheme: str
    score: int
    service: str
    provider: Optional[str]
    region: Optional[str]
    methods_hit: List[str]
    details_json: Optional[dict]
    created_at: datetime
    scan_job: Optional[ScanJobRef] = None
    verified_status: str = "pending"
    verified_at: Optional[datetime] = None
    verification_details: Optional[dict] = None
    model_type: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# ─── Scan Logs ───

class ScanLogOut(BaseModel):
    id: int
    scan_job_id: int
    phase: Optional[str]
    message: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ─── Dashboard Stats ───

class DashboardStats(BaseModel):
    total_scans: int
    total_matches: int
    active_scans: int
    last_scan_at: Optional[datetime]
    matches_by_provider: List[dict]


# Fix forward reference
ScanJobDetailOut.model_rebuild()
