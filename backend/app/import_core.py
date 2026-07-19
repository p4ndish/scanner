"""Shared fast import logic used by both CLI and web upload."""
import csv
import io
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.models import ScanJob, Match
from backend.app.streaming_json import stream_matches_from_file


def fast_import_results(filepath: str, user_id: int = 1, batch_size: int = 50000, db_session=None, name: str = None):
    """
    Import a results.json file into PostgreSQL using COPY.
    Skips duplicates: any (ip, port) already owned by this user is ignored.
    Also deduplicates within the same file (only first occurrence kept).

    Returns (imported_count, skipped_count, scan_job_id).

    If db_session is provided, uses it for the ScanJob creation (web mode).
    Otherwise creates its own session (CLI mode).

    If name is provided, it is used as the ScanJob name; otherwise an
    auto-generated "Import {timestamp}" name is used.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    owns_session = db_session is None
    if owns_session:
        db_url = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg2://scanner:scannerpass@db:5432/opencode_scanner",
        )
        engine = create_engine(db_url, pool_pre_ping=True)
        Session = sessionmaker(bind=engine)
        db = Session()
    else:
        db = db_session

    # ── Load existing (ip, port) pairs for this user ──
    existing = set()
    t0 = time.time()
    rows = (
        db.query(Match.ip, Match.port)
        .join(ScanJob)
        .filter(ScanJob.user_id == user_id)
        .all()
    )
    existing = {(r.ip, r.port) for r in rows}
    dedup_time = time.time() - t0
    if existing:
        print(f"  {len(existing):,} existing matches loaded for dedup ({dedup_time:.1f}s)")

    # Peek at first few matches
    is_llm = False
    ports_seen = set()
    match_count = 0

    for m in stream_matches_from_file(str(filepath)):
        ports_seen.add(str(m.get("port", 0)))
        svc = m.get("service", "")
        if svc in (
            "ollama", "vllm", "vllm_compat", "llamacpp",
            "kobold", "textgen", "lm_studio", "anythingllm", "openwebui",
        ):
            is_llm = True
        match_count += 1
        if match_count >= 5:
            break

    if match_count == 0:
        for _ in stream_matches_from_file(str(filepath)):
            match_count += 1
            break
        if match_count == 0:
            raise ValueError("No matches found in file")

    # Create scan job
    now = datetime.now(timezone.utc)
    job = ScanJob(
        user_id=user_id,
        name=name.strip() if name and name.strip() else f"Import {now.strftime('%Y-%m-%d %H:%M')}",
        status="completed",
        providers=["cli_import"],
        ports=list(ports_seen) or ["0"],
        llm_mode=is_llm,
        score_threshold=5,
        stats_json={},
        started_at=now,
        completed_at=now,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # COPY via raw psycopg2 connection
    raw_conn = db.connection().connection
    cursor = raw_conn.cursor()

    imported = 0
    skipped = 0
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter='\t', quoting=csv.QUOTE_NONE, escapechar='\\')

    for m in stream_matches_from_file(str(filepath)):
        ip = m.get("ip", "unknown")
        port = m.get("port", 0)
        key = (ip, port)

        if key in existing:
            skipped += 1
            continue

        existing.add(key)  # prevent intra-file duplicates too

        writer.writerow([
            job.id,
            ip,
            port,
            m.get("scheme", "http"),
            m.get("score", 0),
            m.get("service", "unknown"),
            m.get("provider", ""),
            m.get("region", ""),
            json.dumps(m.get("methods_hit", [])),
            json.dumps(m.get("details", {})),
            now.isoformat(),
        ])
        imported += 1

        if imported % batch_size == 0:
            buf.seek(0)
            cursor.copy_from(
                buf,
                "matches",
                columns=("scan_job_id", "ip", "port", "scheme", "score", "service", "provider", "region", "methods_hit", "details_json", "created_at"),
                sep='\t',
                null='',
            )
            raw_conn.commit()
            buf = io.StringIO()
            writer = csv.writer(buf, delimiter='\t', quoting=csv.QUOTE_NONE, escapechar='\\')

    # Final batch
    if buf.tell() > 0:
        buf.seek(0)
        cursor.copy_from(
            buf,
            "matches",
            columns=("scan_job_id", "ip", "port", "scheme", "score", "service", "provider", "region", "methods_hit", "details_json", "created_at"),
            sep='\t',
            null='',
        )
        raw_conn.commit()

    cursor.close()

    # Update stats
    job.stats_json = {"matches_found": imported, "duplicates_skipped": skipped}
    db.commit()

    if owns_session:
        db.close()

    return imported, skipped, job.id


def import_into_existing_job(filepath, scan_job_id, db_session, batch_size=50000):
    """Load a results.json file into an EXISTING ScanJob (e.g. a remote SSH scan).

    Unlike fast_import_results, this does NOT create a new ScanJob and only
    deduplicates within the file (cross-scan duplicates are kept, since a remote
    scan from a different vantage point may legitimately re-find the same host).

    Returns (imported_count, skipped_count).
    """
    from backend.app.models import ScanJob
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    db = db_session
    job = db.query(ScanJob).filter(ScanJob.id == scan_job_id).first()
    if not job:
        raise ValueError(f"ScanJob {scan_job_id} not found")

    now = datetime.now(timezone.utc)
    raw_conn = db.connection().connection
    cursor = raw_conn.cursor()

    imported = 0
    skipped = 0
    seen = set()  # intra-file dedup only
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter='\t', quoting=csv.QUOTE_NONE, escapechar='\\')

    for m in stream_matches_from_file(str(filepath)):
        ip = m.get("ip", "unknown")
        port = m.get("port", 0)
        key = (ip, port)
        if key in seen:
            skipped += 1
            continue
        seen.add(key)

        writer.writerow([
            scan_job_id,
            ip,
            port,
            m.get("scheme", "http"),
            m.get("score", 0),
            m.get("service", "unknown"),
            m.get("provider", ""),
            m.get("region", ""),
            json.dumps(m.get("methods_hit", [])),
            json.dumps(m.get("details", {})),
            now.isoformat(),
        ])
        imported += 1

        if imported % batch_size == 0:
            buf.seek(0)
            cursor.copy_from(
                buf, "matches",
                columns=("scan_job_id", "ip", "port", "scheme", "score", "service", "provider", "region", "methods_hit", "details_json", "created_at"),
                sep='\t', null='',
            )
            raw_conn.commit()
            buf = io.StringIO()
            writer = csv.writer(buf, delimiter='\t', quoting=csv.QUOTE_NONE, escapechar='\\')

    if buf.tell() > 0:
        buf.seek(0)
        cursor.copy_from(
            buf, "matches",
            columns=("scan_job_id", "ip", "port", "scheme", "score", "service", "provider", "region", "methods_hit", "details_json", "created_at"),
            sep='\t', null='',
        )
        raw_conn.commit()

    cursor.close()

    # Merge counts into the job's stats
    stats = dict(job.stats_json or {})
    stats["matches_found"] = imported
    stats["duplicates_skipped"] = skipped
    job.stats_json = stats
    db.commit()

    return imported, skipped
