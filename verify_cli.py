#!/usr/bin/env python3
"""
Standalone CLI verifier for opencode-scanner matches.

Runs honeypot detection (3-check) directly against the database
with configurable concurrency.  Faster than the Celery task because
it runs in the foreground with more threads.

Usage (inside Docker container):
    docker compose exec -T web python3 verify_cli.py
    docker compose exec -T web python3 verify_cli.py --workers 200 --timeout 3
    docker compose exec -T web python3 verify_cli.py --service ollama --workers 100
    docker compose exec -T web python3 verify_cli.py --reverify-unreachable --workers 50

Usage (on host, needs DB access):
    export DATABASE_URL=postgresql+psycopg2://scanner:scannerpass@localhost:5432/opencode_scanner
    python3 verify_cli.py --workers 200

Expected speed: 100-300 matches/sec depending on network latency.
A 200K file finishes in 10-30 minutes.
"""

import argparse
import concurrent.futures
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app.llm_probe import verify_endpoint
from backend.app.models import Match, ScanJob


# ── graceful shutdown ──
_shutdown_requested = False


def _sigint_handler(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    print("\n  Shutdown requested, finishing current chunk...", flush=True)


signal.signal(signal.SIGINT, _sigint_handler)
signal.signal(signal.SIGTERM, _sigint_handler)


def _fmt(n: int) -> str:
    return f"{n:,}"


def _progress_bar(done: int, total: int, width: int = 40) -> str:
    pct = min(100, int(done / total * 100)) if total else 0
    filled = int(width * pct / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct:3d}% ({_fmt(done)} / {_fmt(total)})"


def verify_chunk(db_session, match_dicts, max_workers, timeout, db_batch):
    """Verify a chunk of matches and return updates."""
    updates = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(
                verify_endpoint, md["ip"], md["port"], md["scheme"], timeout
            ): md
            for md in match_dicts
        }

        for future in concurrent.futures.as_completed(futures):
            if _shutdown_requested:
                break
            md = futures[future]
            try:
                status, details = future.result()
            except Exception:
                status, details = "unreachable", {"error": "exception"}
            updates.append((md["id"], status, details))

    # Persist to DB
    if updates:
        now = datetime.now(timezone.utc)
        for match_id, status, details in updates:
            db_session.query(Match).filter(Match.id == match_id).update({
                "verified_status": status,
                "verified_at": now,
                "verification_details": details,
            })
        db_session.commit()

    return updates


def main():
    parser = argparse.ArgumentParser(
        description="CLI verifier for opencode-scanner honeypot detection"
    )
    parser.add_argument(
        "--workers", type=int, default=100,
        help="Concurrent HTTP threads (default: 100)"
    )
    parser.add_argument(
        "--chunk-size", type=int, default=1000,
        help="DB rows fetched per chunk (default: 1000)"
    )
    parser.add_argument(
        "--db-batch", type=int, default=100,
        help="Update DB every N verified matches within a chunk (default: 100)"
    )
    parser.add_argument(
        "--timeout", type=float, default=3,
        help="HTTP timeout per endpoint probe in seconds (default: 3)"
    )
    parser.add_argument(
        "--user-id", type=int, default=1,
        help="User ID whose matches to verify (default: 1)"
    )
    parser.add_argument(
        "--service", type=str, default=None,
        help="Only verify matches with this service (e.g. ollama, vllm_compat)"
    )
    parser.add_argument(
        "--provider", type=str, default=None,
        help="Only verify matches with this provider"
    )
    parser.add_argument(
        "--scan-id", type=int, default=None,
        help="Only verify matches from this scan job"
    )
    parser.add_argument(
        "--reverify-unreachable", action="store_true",
        help="Re-verify matches currently marked unreachable"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Do not write to database, just count and simulate"
    )
    args = parser.parse_args()

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://scanner:scannerpass@db:5432/opencode_scanner",
    )
    engine = create_engine(db_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)

    # ── count ──
    count_session = Session()
    q = count_session.query(Match.id).join(ScanJob).filter(
        ScanJob.user_id == args.user_id,
    )
    if args.reverify_unreachable:
        q = q.filter(Match.verified_status == "unreachable")
    else:
        q = q.filter(Match.verified_status.in_(["pending", "unreachable"]))

    if args.service:
        q = q.filter(Match.service == args.service)
    if args.provider:
        q = q.filter(Match.provider == args.provider)
    if args.scan_id:
        q = q.filter(Match.scan_job_id == args.scan_id)

    total = q.count()
    count_session.close()

    if total == 0:
        print("No matches to verify.")
        return

    print(f"Matches to verify: {_fmt(total)}")
    print(f"Workers: {args.workers}  |  Chunk: {args.chunk_size}  |  Timeout: {args.timeout}s")
    if args.dry_run:
        print("DRY RUN — no DB writes")
    print("-" * 60)

    # ── stream & verify ──
    done = 0
    counts = {"legitimate": 0, "honeypot": 0, "unreachable": 0}
    offset = 0
    t0 = time.time()
    last_print = t0

    while offset < total and not _shutdown_requested:
        db = Session()
        chunk_q = db.query(
            Match.id, Match.ip, Match.port, Match.scheme, Match.service
        ).join(ScanJob).filter(
            ScanJob.user_id == args.user_id,
        )
        if args.reverify_unreachable:
            chunk_q = chunk_q.filter(Match.verified_status == "unreachable")
        else:
            chunk_q = chunk_q.filter(Match.verified_status.in_(["pending", "unreachable"]))

        if args.service:
            chunk_q = chunk_q.filter(Match.service == args.service)
        if args.provider:
            chunk_q = chunk_q.filter(Match.provider == args.provider)
        if args.scan_id:
            chunk_q = chunk_q.filter(Match.scan_job_id == args.scan_id)

        rows = chunk_q.order_by(Match.id).offset(offset).limit(args.chunk_size).all()
        if not rows:
            db.close()
            break

        match_dicts = [
            {"id": rid, "ip": ip, "port": port, "scheme": scheme}
            for rid, ip, port, scheme, svc in rows
        ]
        db.close()

        if args.dry_run:
            # Simulate without HTTP
            for md in match_dicts:
                counts["unreachable"] += 1
                done += 1
        else:
            db = Session()
            updates = verify_chunk(
                db, match_dicts, args.workers, args.timeout, args.db_batch
            )
            db.close()
            for _, status, _ in updates:
                counts[status] += 1
                done += 1

        offset += len(rows)

        now = time.time()
        if now - last_print >= 2 or done >= total:
            elapsed = now - t0
            rate = done / elapsed if elapsed else 0
            eta = (total - done) / rate if rate else 0
            print(
                f"  {_progress_bar(done, total)}  "
                f"rate: {rate:,.0f}/s  eta: {eta/60:.1f}m  "
                f"L:{counts['legitimate']} H:{counts['honeypot']} U:{counts['unreachable']}",
                flush=True,
            )
            last_print = now

    # ── summary ──
    elapsed = time.time() - t0
    rate = done / elapsed if elapsed else 0
    print("-" * 60)
    print(f"Done! Verified {_fmt(done)} matches in {elapsed:.1f}s ({rate:,.0f}/sec)")
    print(f"  Legitimate : {_fmt(counts['legitimate'])}")
    print(f"  Honeypot   : {_fmt(counts['honeypot'])}")
    print(f"  Unreachable: {_fmt(counts['unreachable'])}")


if __name__ == "__main__":
    main()
