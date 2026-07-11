#!/usr/bin/env python3
"""
find_llm.py — Fingerprint local LLM servers (Ollama, vLLM, llama.cpp, Kobold, etc.)
from masscan results or an IP:port list.

Usage:
  python3 find_llm.py --masscan-dir results/scans
  python3 find_llm.py --masscan-dir results/scans --score 9 --raw
  python3 find_llm.py --targets ip_list.txt -o results/llm_confirmed.json
  python3 find_llm.py --target 1.2.3.4:11434 --json-out
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_fingerprint import LLMFingerprintEngine


def parse_masscan_dir(masscan_dir: str) -> set[tuple[str, int]]:
    import glob
    pairs = set()
    files = sorted(glob.glob(f"{masscan_dir}/masscan_batch_*.json"))
    if not files:
        print(f"ERROR: No masscan_batch_*.json files found in {masscan_dir}", file=sys.stderr)
        sys.exit(1)

    for fpath in files:
        try:
            with open(fpath) as f:
                data = json.load(f)
            if isinstance(data, list):
                for rec in data:
                    for p in rec.get("ports", []):
                        pairs.add((rec["ip"], p["port"]))
            elif isinstance(data, dict):
                for rec in data.get("hosts", []):
                    for p in rec.get("ports", []):
                        pairs.add((rec["ip"], p["port"]))
        except (json.JSONDecodeError, KeyError, OSError):
            continue

    print(f"Parsed {len(files)} masscan files → {len(pairs):,} unique IP:port pairs")
    return pairs


def parse_targets_file(path: str) -> set[tuple[str, int]]:
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


def _models_str(models: list) -> str:
    if not models:
        return "-"
    s = ",".join(models[:3])
    if len(models) > 3:
        s += f"+{len(models)-3}"
    return s


async def main():
    parser = argparse.ArgumentParser(
        description="find_llm — Fingerprint local LLM servers from masscan results"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--masscan-dir", help="Directory with masscan_batch_*.json files")
    source.add_argument("--targets", help="File with one 'IP:port' per line")
    source.add_argument("--target", action="append", help="Single 'IP:port' to probe (repeatable)")

    parser.add_argument("--score", "-s", type=int, default=5, help="Min score to report (default: 5)")
    parser.add_argument("--concurrency", "-c", type=int, default=500, help="Concurrent HTTP probes (default: 500)")
    parser.add_argument("--timeout", "-t", type=float, default=3.0, help="HTTP timeout in seconds (default: 3)")
    parser.add_argument("--output", "-o", default=None, help="Save full JSON results to file")
    parser.add_argument("--raw", action="store_true", help="Print raw host:port lines only (no table)")
    parser.add_argument("--json-out", action="store_true", help="Print JSON to stdout")

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

    if not pairs:
        print("No targets to scan.", file=sys.stderr)
        sys.exit(1)

    if not args.raw:
        print(f"Probing {len(pairs):,} targets with {args.concurrency} concurrent workers...")

    engine = LLMFingerprintEngine(
        concurrency=args.concurrency,
        timeout=args.timeout,
        score_threshold=args.score,
    )

    matches = await engine.probe_candidates(list(pairs))

    # ── Raw host:port output ──────────────────────────────────────────────
    if args.raw:
        for m in matches:
            print(f"{m['ip']}:{m['port']}")
        return

    # ── JSON stdout ───────────────────────────────────────────────────────
    if args.json_out:
        print(json.dumps(matches, indent=2))
        return

    # ── Table output ──────────────────────────────────────────────────────
    print(f"\n{'='*78}")
    if matches:
        print(f"  Found {len(matches)} LLM server(s):")
        print(f"{'='*78}")
        print(f"  {'IP':<18} {'Port':<7} {'Score':<8} {'Service':<14} {'Version':<12} Models")
        print(f"  {'-'*17} {'-'*6} {'-'*7} {'-'*13} {'-'*11} {'-'*20}")
        for m in sorted(matches, key=lambda x: x["score"], reverse=True):
            print(
                f"  {m['ip']:<18} {m['port']:<7} {m['score']}/{sum([6,3,5,2,4,4,3,3,1]):<6} "
                f"{m['service']:<14} {(m['version'] or '?'):<12} {_models_str(m['models'])}"
            )
    else:
        print("  No LLM servers found.")
    print(f"{'='*78}")
    print(f"  Candidates scanned: {len(pairs):,}")
    print(f"  Matches confirmed:  {len(matches)}")
    print(f"{'='*78}")

    # ── Save JSON file ────────────────────────────────────────────────────
    if args.output:
        # If --raw flag used with -o, write raw lines; otherwise write JSON
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if args.output.endswith(".txt"):
            with open(args.output, "w") as f:
                for m in matches:
                    f.write(f"{m['ip']}:{m['port']}\n")
            print(f"\nRaw host:port list saved to: {args.output}")
        else:
            payload = {
                "matches": matches,
                "stats": {
                    "candidates_scanned": len(pairs),
                    "matches_found": len(matches),
                },
            }
            with open(args.output, "w") as f:
                json.dump(payload, f, indent=2)
            print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
