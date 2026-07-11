#!/usr/bin/env python3
"""
find_opencode.py — Fingerprint opencode web servers from masscan results or IP list.

Usage:
  python3 find_opencode.py --masscan-dir scans/          # Parse masscan JSON output
  python3 find_opencode.py --targets ip_list.txt          # Read IP:port pairs from file
  python3 find_opencode.py --masscan-dir scans/ --output results.json
  python3 find_opencode.py --targets ip_list.txt --score 7 --verbose
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fingerprint import FingerprintEngine


def parse_masscan_dir(masscan_dir: str) -> set[tuple[str, int]]:
    """Parse all masscan_batch_*.json files in a directory into (ip, port) pairs."""
    import glob
    pairs = set()
    files = sorted(glob.glob(f"{masscan_dir}/masscan_batch_*.json"))
    if not files:
        print(f"ERROR: No masscan_batch_*.json files found in {masscan_dir}", file=sys.stderr)
        sys.exit(1)

    for fpath in files:
        with open(fpath) as f:
            content = f.read().strip()
            # Try standard JSON array first
            if content.startswith("["):
                try:
                    data = json.loads(content)
                    if isinstance(data, list):
                        for rec in data:
                            for p in rec.get("ports", []):
                                pairs.add((rec["ip"], p["port"]))
                        continue
                except json.JSONDecodeError:
                    pass
            # Fall back to line-by-line (JSONL or truncated array)
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("[") or line.startswith("#") or line.startswith("]"):
                    continue
                try:
                    rec = json.loads(line)
                    for p in rec.get("ports", []):
                        pairs.add((rec["ip"], p["port"]))
                except json.JSONDecodeError:
                    continue

    print(f"Parsed {len(files)} masscan files -> {len(pairs):,} unique IP:port pairs")
    return pairs


def parse_targets_file(path: str) -> set[tuple[str, int]]:
    """Parse a file with one IP:port per line."""
    pairs = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                ip, port = line.rsplit(":", 1)
                pairs.add((ip.strip(), int(port)))
            except ValueError:
                print(f"  SKIP malformed: {line}", file=sys.stderr)

    print(f"Loaded {len(pairs):,} targets from {path}")
    return pairs


async def main():
    parser = argparse.ArgumentParser(
        description="find_opencode — Fingerprint opencode web servers from masscan results"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--masscan-dir", help="Directory with masscan_batch_*.json files")
    source.add_argument("--targets", help="File with one 'IP:port' per line")
    source.add_argument("--target", action="append", help="Single 'IP:port' to probe (repeatable)")

    parser.add_argument("--output", "-o", default=None, help="Save results as JSON")
    parser.add_argument("--concurrency", "-c", type=int, default=500, help="Concurrent HTTP probes (default: 500)")
    parser.add_argument("--timeout", "-t", type=float, default=3.0, help="HTTP request timeout in seconds (default: 3)")
    parser.add_argument("--score", "-s", type=int, default=5, help="Min score to report (default: 5, max 17)")
    parser.add_argument("--high-confidence", action="store_true", help="Shortcut for --score 13 (zero false positives)")
    parser.add_argument("--min-version", default=None, help="Only report matches >= this version (e.g. 1.14.0)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show all probes including negatives")
    parser.add_argument("--table", action="store_true", default=True, help="Print results as table (default)")
    parser.add_argument("--json-out", action="store_true", help="Print results as JSON to stdout")

    args = parser.parse_args()

    # Resolve targets
    if args.masscan_dir:
        pairs = parse_masscan_dir(args.masscan_dir)
    elif args.targets:
        pairs = parse_targets_file(args.targets)
    elif args.target:
        pairs = set()
        for t in args.target:
            ip, port = t.rsplit(":", 1)
            pairs.add((ip.strip(), int(port)))
    else:
        pairs = set()

    if args.high_confidence:
        args.score = 13

    if not pairs:
        print("No targets to scan.", file=sys.stderr)
        sys.exit(1)

    print(f"Probing {len(pairs):,} targets with {args.concurrency} concurrent workers...")
    if args.score < 5:
        print(f"  (low threshold {args.score} — expect many false positives)")

    engine = FingerprintEngine(
        concurrency=args.concurrency,
        timeout=args.timeout,
        score_threshold=args.score,
    )

    matches = await engine.probe_candidates(list(pairs))

    # Apply min_version filter if requested
    if args.min_version and matches:
        def _parse_version(v: str) -> tuple:
            try:
                return tuple(int(x) for x in v.split("."))
            except (ValueError, AttributeError):
                return (0,)
        min_v = _parse_version(args.min_version)
        filtered = []
        for m in matches:
            health = m.get("details", {}).get("health", {})
            ver = health.get("version", "0")
            if _parse_version(ver) >= min_v:
                filtered.append(m)
        dropped = len(matches) - len(filtered)
        if dropped > 0:
            print(f"  Version filter: dropped {dropped} matches below v{args.min_version}")
        matches = filtered

    # Output
    if args.json_out:
        output = [{"ip": m["ip"], "port": m["port"], "score": m["score"],
                    "confidence": m["confidence"], "methods": m["methods_hit"],
                    "details": m.get("details", {})} for m in matches]
        print(json.dumps(output, indent=2))
    else:
        print(f"\n{'='*70}")
        if matches:
            print(f"  Found {len(matches)} opencode server(s):")
            print(f"{'='*70}")
            print(f"  {'IP':<18} {'Port':<7} {'Score':<7} {'Methods':<20} {'Version':<12}")
            print(f"  {'-'*17} {'-'*6} {'-'*6} {'-'*19} {'-'*11}")
            for m in matches:
                methods = ",".join(m["methods_hit"])
                details = m.get("details", {})
                health = details.get("health", {})
                version = health.get("version", "?")
                if not version or version == "?":
                    path_info = details.get("path", {})
                    if path_info:
                        version = "(detected)"
                    elif "auth_realm" in m["methods_hit"]:
                        version = "(auth)"
                    else:
                        version = "?"

                print(f"  {m['ip']:<18} {m['port']:<7} {m['score']}/{sum([5,4,4,3,2,2,1]):<5} {methods:<20} {version:<12}")

            if args.verbose:
                for m in matches:
                    print(f"\n  --- {m['ip']}:{m['port']} ---")
                    print(f"  Methods: {m['methods_hit']}")
                    print(f"  Details: {json.dumps(m.get('details',{}), indent=4)}")
        else:
            print("  No opencode servers found.")
        print(f"{'='*70}")
        print(f"  Candidates scanned: {len(pairs):,}")
        print(f"  Matches confirmed:  {len(matches)}")

    # Save to file
    if args.output:
        output = {
            "matches": [{"ip": m["ip"], "port": m["port"], "score": m["score"],
                          "confidence": m["confidence"], "methods_hit": m["methods_hit"],
                          "details": m.get("details", {})} for m in matches],
            "stats": {"candidates_scanned": len(pairs), "matches_found": len(matches)},
        }
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
