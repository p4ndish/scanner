#!/usr/bin/env python3
"""
Fast CLI importer for opencode-scanner results.json files.

Bypasses HTTP entirely — streams the JSON from disk and inserts directly
into PostgreSQL via COPY (the fastest bulk-load method).

Usage (inside Docker container):
    docker compose exec web python3 import_results.py results/results.json

Usage (on host, needs DB access):
    export DATABASE_URL=postgresql+psycopg2://scanner:scannerpass@localhost:5432/opencode_scanner
    python3 import_results.py results/results.json

Expected speed: 5,000–15,000 matches/sec depending on disk and DB.
A 500MB file (~2M matches) should finish in under 3 minutes.
"""

import argparse
import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow importing backend modules when run from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.app.models import Base, ScanJob
from backend.app.streaming_json import stream_matches_from_file


def import_file(filepath: str, user_id: int = 1, batch_size: int = 20000):
    filepath = Path(filepath)
    if not filepath.exists():
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    size_mb = filepath.stat().st_size / (1024 * 1024)
    print(f"File: {filepath} ({size_mb:.1f} MB)")

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://scanner:scannerpass@db:5432/opencode_scanner",
    )
    engine = create_engine(db_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    db = Session()

    # Peek at first few matches to detect mode
    print("Scanning file...")
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
            print("Error: no matches found in file", file=sys.stderr)
            sys.exit(1)

    print(f"  Detected: {'LLM' if is_llm else 'OpenCode'} mode, {len(ports_seen)} unique ports")

    # Create scan job
    now = datetime.now(timezone.utc)
    job = ScanJob(
        user_id=user_id,
        name=f"CLI Import {now.strftime('%Y-%m-%d %H:%M')}",
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
    print(f"Created scan job #{job.id}")

    # Get raw psycopg2 connection for COPY
    raw_conn = db.connection().connection
    cursor = raw_conn.cursor()

    # Use COPY for maximum speed
    imported = 0
    t0 = time.time()
    last_report = t0
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter='\t', quoting=csv.QUOTE_NONE, escapechar='\\')

    print(f"Importing with batch size {batch_size} via PostgreSQL COPY...")

    # Pre-build column order for COPY
    # matches table columns: id, scan_job_id, ip, port, scheme, score, service, provider, region, methods_hit, details_json, created_at
    for m in stream_matches_from_file(str(filepath)):
        writer.writerow([
            job.id,
            m.get("ip", "unknown"),
            m.get("port", 0),
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

            now_time = time.time()
            if now_time - last_report >= 2:
                rate = imported / (now_time - t0)
                print(f"  {imported:,} imported @ {rate:,.0f} matches/sec")
                last_report = now_time

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

    elapsed = time.time() - t0
    print(f"\nDone! Imported {imported:,} matches in {elapsed:.1f}s ({imported/elapsed:,.0f}/sec)")

    # Update stats
    job.stats_json = {"matches_found": imported}
    db.commit()
    db.close()

    return imported


def main():
    parser = argparse.ArgumentParser(
        description="Fast CLI importer for opencode-scanner results.json"
    )
    parser.add_argument("file", help="Path to results.json")
    parser.add_argument("--user-id", type=int, default=1, help="User ID to own the import (default: 1)")
    parser.add_argument("--batch-size", type=int, default=20000, help="COPY batch size (default: 20000)")
    args = parser.parse_args()

    import_file(args.file, user_id=args.user_id, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
