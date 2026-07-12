#!/usr/bin/env python3
"""
Fast CLI importer for opencode-scanner results.json files.

Usage (inside Docker container):
    docker compose exec web python3 import_results.py results/results.json

Usage (on host, needs DB access):
    export DATABASE_URL=postgresql+psycopg2://scanner:scannerpass@localhost:5432/opencode_scanner
    python3 import_results.py results/results.json

Expected speed: 10,000–15,000 matches/sec.
A 500MB file (~2M matches) finishes in under 3 minutes.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.app.import_core import fast_import_results


def main():
    parser = argparse.ArgumentParser(
        description="Fast CLI importer for opencode-scanner results.json"
    )
    parser.add_argument("file", help="Path to results.json")
    parser.add_argument("--user-id", type=int, default=1, help="User ID to own the import (default: 1)")
    parser.add_argument("--batch-size", type=int, default=50000, help="COPY batch size (default: 50000)")
    args = parser.parse_args()

    filepath = Path(args.file)
    size_mb = filepath.stat().st_size / (1024 * 1024)
    print(f"File: {filepath} ({size_mb:.1f} MB)")
    print("Importing via PostgreSQL COPY...")

    t0 = time.time()
    imported, job_id = fast_import_results(str(filepath), user_id=args.user_id, batch_size=args.batch_size)
    elapsed = time.time() - t0

    print(f"\nDone! Imported {imported:,} matches in {elapsed:.1f}s ({imported/elapsed:,.0f}/sec)")
    print(f"Scan job #{job_id}")


if __name__ == "__main__":
    main()
