#!/usr/bin/env python3
"""
opencode-scanner — Find opencode web servers across cloud provider IP ranges.

Uses masscan for fast port discovery, zmap for pre-filtering, and async HTTP
fingerprinting with multiple detection methods to identify opencode servers.

Usage:
  python scanner.py --all                          # Scan all providers
  python scanner.py --providers aws,google_cloud   # Scan specific providers
  python scanner.py --all --ports 4096,3000        # Custom ports
  python scanner.py --all --rate 3000 --parallel 6  # Tune performance
  python scanner.py --all --skip-ping              # Skip zmap pre-filter
  python scanner.py --dry-run                      # Show what would be scanned
"""

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional

from fingerprint import FingerprintEngine
from llm_fingerprint import LLMFingerprintEngine
from masscan_runner import MasscanRunner, check_masscan, check_zmap, run_zmap, detect_default_interface, detect_gateway_mac
from reporter import Reporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("scanner")

RUNNING = True


def signal_handler(sig, frame):
    global RUNNING
    logger.info("Received interrupt. Finishing current batch and saving results...")
    RUNNING = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def load_config(config_path: str) -> dict:
    """Load config from file or return defaults."""
    defaults = {
        "targets": {"providers": ["all"], "ports": [4096, 3000, 8080]},
        "masscan": {
            "rate_per_instance": 2500,
            "parallel_instances": 4,
            "batch_prefix_count": 500,
            "batch_target_ip_count": 5000000,
            "max_retries": 2,
            "retry_rate_multiplier": 0.5,
        },
        "fingerprint": {
            "http_concurrency": 500,
            "request_timeout": 3.0,
            "score_threshold": 5,
            "verify_methods": [
                "health", "doc", "path", "doc_title",
                "auth_realm", "error_shape", "port_hint",
            ],
        },
        "pre_filter": {
            "zmap_enabled": True,
            "zmap_rate": 250000,
            "zmap_retries": 1,
        },
        "output": {
            "directory": "results",
            "save_raw_masscan": True,
            "save_raw_zmap": False,
        },
    }

    if os.path.exists(config_path):
        with open(config_path) as f:
            user_config = json.load(f)
            # Deep merge (simple two-level)
            for section in ["targets", "masscan", "fingerprint", "pre_filter", "output"]:
                if section in user_config:
                    defaults[section].update(user_config[section])

    return defaults


def load_providers(ranges_path: str) -> dict:
    """Load cloud providers JSON."""
    with open(ranges_path) as f:
        data = json.load(f)
    # Remove $meta if present
    data.pop("$meta", None)
    return data


def resolve_providers(config: dict, providers_data: dict) -> dict:
    """Resolve which providers to scan based on config."""
    requested = config["targets"]["providers"]
    if "all" in requested:
        return providers_data

    selected = {}
    for name in requested:
        name = name.strip().lower()
        if name in providers_data:
            selected[name] = providers_data[name]
        else:
            logger.warning(f"Provider '{name}' not found in cloud_providers.json")

    if not selected:
        logger.error("No valid providers selected!")
        sys.exit(1)

    return selected


def summarize_scan(providers: dict, ports: list[str], config: dict):
    """Print scan summary."""
    total_ips = sum(
        p.get("estimated_ipv4_hosts", 0)
        for p in providers.values()
    )
    total_prefixes = sum(
        p.get("prefix_count", 0)
        for p in providers.values()
    )
    region_counts = {}
    for name, info in providers.items():
        region = info.get("region", "?")
        region_counts[region] = region_counts.get(region, 0) + 1

    logger.info("=" * 60)
    logger.info("  OpenCode Scanner — Scan Summary")
    logger.info("=" * 60)
    logger.info(f"  Providers:  {len(providers)} ({', '.join(region_counts.keys())})")
    logger.info(f"  Prefixes:   {total_prefixes:,}")
    logger.info(f"  Est. IPs:   ~{total_ips:,}")
    port_count = 0
    total_probes = 0
    for p in ports:
        if "-" in str(p):
            parts = str(p).split("-")
            try:
                start, end = int(parts[0]), int(parts[1])
                port_count += (end - start + 1)
            except ValueError:
                port_count += 1
        else:
            try:
                int(p)
                port_count += 1
            except ValueError:
                port_count += 1
    total_probes = total_ips * port_count
    pps_total = config["masscan"]["rate_per_instance"] * config["masscan"]["parallel_instances"]
    est_seconds = total_probes / pps_total if pps_total > 0 else 0
    logger.info(f"  Ports:      {port_count} (probes: ~{total_probes:,})")
    if est_seconds > 3600:
        logger.info(f"  Est. time:  {est_seconds/3600:.1f} hours ({est_seconds/86400:.1f} days)")
    elif est_seconds > 60:
        logger.info(f"  Est. time:  {est_seconds/60:.1f} minutes")
    else:
        logger.info(f"  Est. time:  {est_seconds:.0f} seconds")
    if port_count > 10:
        logger.warning(f"  Scanning {port_count} ports — this may take a very long time!")
    logger.info(f"  Masscan rate: {config['masscan']['rate_per_instance']:,} x {config['masscan']['parallel_instances']}")
    logger.info(f"  Interface:   {config['masscan'].get('interface') or 'auto'}")
    logger.info(f"  Router IP:   {config['masscan'].get('router_ip') or 'auto'}")
    logger.info(f"  Sudo:        {config['masscan'].get('use_sudo', False)}")
    logger.info(f"  Pre-filter: {'zmap ICMP' if config['pre_filter']['zmap_enabled'] else 'disabled'}")
    logger.info(f"  Fingerprint: {config['fingerprint']['http_concurrency']} concurrent")
    logger.info("=" * 60)


def _count_ports(ports: list[str]) -> int:
    """Count total ports from a mixed list of individual ports and ranges."""
    count = 0
    for p in ports:
        p = str(p)
        if "-" in p:
            try:
                start, end = int(p.split("-")[0]), int(p.split("-")[1])
                count += (end - start + 1)
            except ValueError:
                count += 1
        else:
            count += 1
    return count


def _build_provider_index(providers_data: dict) -> list[tuple[ipaddress.IPv4Network, str, str]]:
    """Build an index of (network, provider_name, region) for fast IP lookup."""
    index = []
    for name, info in providers_data.items():
        region = info.get("region", "?")
        for prefix in info.get("ipv4_prefixes", []):
            try:
                net = ipaddress.IPv4Network(prefix, strict=False)
                index.append((net, name, region))
            except ValueError:
                pass
    index.sort(key=lambda x: x[0].prefixlen, reverse=True)  # longest prefix first
    return index


def _resolve_provider(ip: str, index: list) -> tuple[str, str]:
    """Resolve an IP to (provider_name, region) using the prefix index."""
    try:
        addr = ipaddress.IPv4Address(ip)
    except ValueError:
        return ("unknown", "?")
    for net, name, region in index:
        if addr in net:
            return (name, region)
    return ("unknown", "?")


def main():
    parser = argparse.ArgumentParser(
        description="opencode-scanner — Find opencode web servers across cloud providers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scanner.py --all
  python scanner.py --providers aws,google_cloud,alibaba_cloud
  python scanner.py --all --ports 4096,3000 --rate 3000 --parallel 6
  python scanner.py --all --skip-ping
  python scanner.py --all --dry-run
  python scanner.py --all --http-concurrency 1000 --score 7
  python scanner.py --providers hetzner,ovh_cloud --full-sweep
        """,
    )

    targets = parser.add_argument_group("Targets")
    targets.add_argument("--providers", default="all",
                         help="Comma-separated provider names or 'all' (default: all). "
                              "Available: tencent_cloud, alibaba_cloud, huawei_cloud, baidu_cloud, "
                              "aws, google_cloud, microsoft_azure, oracle_cloud, digitalocean, "
                              "akamai_linode, vultr, cloudflare, ibm_cloud, ovh_cloud, hetzner, scaleway, ionos")
    targets.add_argument("--ports", default="4096,3000,8080",
                          help="Ports to scan (default: 4096,3000,8080). "
                               "LLM mode default: 11434,8080,8000,1234,5000,5001,7860,8888,3001")
    targets.add_argument("--full-sweep", nargs="?", const="3000-65535", default=None,
                          help="Two-phase: scan known ports first, then full port range on discovered IPs "
                               "(default range: 3000-65535). Tip: use with --llm-mode")
    targets.add_argument("--all", action="store_true",
                          help="Scan all providers")
    targets.add_argument("--llm-mode", action="store_true",
                          help="Fingerprint for local LLM servers (Ollama, vLLM, llama.cpp, Kobold, etc.) "
                               "instead of opencode servers")

    masscan_group = parser.add_argument_group("Masscan")
    masscan_group.add_argument("--rate", type=int, default=None,
                               help="Masscan rate per instance (default: 2500)")
    masscan_group.add_argument("--parallel", type=int, default=None,
                               help="Parallel masscan instances per port chunk (default: 4)")
    masscan_group.add_argument("--workers", type=int, default=None,
                               help="Port-range workers: split port list into N chunks scanned concurrently "
                                    "(default: 1). Total masscan processes = --parallel × --workers. "
                                    "Example: --workers 8 splits 3000-65535 into 8 chunks of ~7800 ports each")
    masscan_group.add_argument("--batch-ips", type=int, default=None,
                               help="Target IPs per batch (default: 5000000)")
    masscan_group.add_argument("--retry", type=int, default=None,
                               help="Max retries for failed batches (default: 2)")
    masscan_group.add_argument("--interface", default=None,
                               help="Network interface for masscan (e.g. eth0, bond0)")
    masscan_group.add_argument("--router-ip", default=None,
                               help="Router IP for masscan (needed for some interfaces like bond)")
    masscan_group.add_argument("--sudo", dest="use_sudo", action="store_true", default=None,
                               help="Run masscan with sudo")
    masscan_group.add_argument("--no-sudo", dest="use_sudo", action="store_false", default=None,
                               help="Run masscan without sudo")

    fingerprint_group = parser.add_argument_group("Fingerprint")
    fingerprint_group.add_argument("--http-concurrency", type=int, default=None,
                                    help="Concurrent HTTP probes (default: 500)")
    fingerprint_group.add_argument("--score", type=int, default=None,
                                    help="Minimum score threshold (default: 5)")
    fingerprint_group.add_argument("--high-confidence", action="store_true",
                                    help="Shortcut for --score 13 (zero false positives)")
    fingerprint_group.add_argument("--min-version", default=None,
                                    help="Only report matches >= this version (e.g. 1.14.0)")

    prefilter_group = parser.add_argument_group("Pre-filter")
    prefilter_group.add_argument("--skip-ping", action="store_true",
                                  help="Skip zmap ICMP pre-filter")
    prefilter_group.add_argument("--force-zmap", action="store_true",
                                  help="Force re-run zmap even if cached results exist")
    prefilter_group.add_argument("--zmap-rate", type=int, default=None,
                                  help="zmap rate (default: 250000)")

    output_group = parser.add_argument_group("Output")
    output_group.add_argument("--output", default="results",
                              help="Output directory (default: results)")
    output_group.add_argument("--raw", action="store_true",
                              help="Also write a raw host:port list to results/llm_raw.txt (or opencode_raw.txt)")
    output_group.add_argument("--dry-run", action="store_true",
                              help="Show summary without scanning")

    args = parser.parse_args()

    if args.all:
        args.providers = "all"

    # Load config
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    config = load_config(config_path)

    # CLI args override config
    llm_mode = args.llm_mode
    # In LLM mode, default ports switch to known LLM ports unless user specified something
    if llm_mode and args.ports == "4096,3000,8080":
        ports = ["11434", "8080", "8000", "1234", "5000", "5001", "7860", "8888", "3001"]
    else:
        ports = [p.strip() for p in args.ports.split(",")]
    rate = args.rate or config["masscan"]["rate_per_instance"]
    parallel = args.parallel or config["masscan"]["parallel_instances"]
    port_workers = args.workers or config["masscan"].get("port_workers", 1)
    batch_ips = args.batch_ips or config["masscan"]["batch_target_ip_count"]
    retries = args.retry or config["masscan"]["max_retries"]
    # LLM mode: default to higher HTTP concurrency since we're probing many ports
    default_conc = 1000 if llm_mode else config["fingerprint"]["http_concurrency"]
    http_conc = args.http_concurrency or default_conc
    score_threshold = args.score or config["fingerprint"]["score_threshold"]
    if args.high_confidence:
        score_threshold = 13
    min_version = args.min_version
    skip_ping = args.skip_ping
    zmap_enabled = config["pre_filter"]["zmap_enabled"] and not skip_ping
    zmap_rate = args.zmap_rate or config["pre_filter"]["zmap_rate"]
    output_dir = args.output
    interface = args.interface or config["masscan"].get("interface")
    router_ip = args.router_ip or config["masscan"].get("router_ip")
    use_sudo = args.use_sudo if args.use_sudo is not None else config["masscan"].get("use_sudo", False)

    # Load provider data
    ranges_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud_providers.json")
    if not os.path.exists(ranges_path):
        logger.error(f"cloud_providers.json not found at {ranges_path}")
        logger.error("Run fetch_providers.py first to download IP ranges.")
        sys.exit(1)

    providers_data = load_providers(ranges_path)
    providers = resolve_providers({"targets": {"providers": args.providers.split(",")}}, providers_data)

    config["targets"]["providers"] = list(providers.keys())
    config["targets"]["ports"] = ports
    config["masscan"]["rate_per_instance"] = rate
    config["masscan"]["parallel_instances"] = parallel
    config["masscan"]["batch_target_ip_count"] = batch_ips
    config["masscan"]["max_retries"] = retries
    config["masscan"]["interface"] = interface
    config["masscan"]["router_ip"] = router_ip
    config["masscan"]["use_sudo"] = use_sudo
    config["fingerprint"]["http_concurrency"] = http_conc
    config["fingerprint"]["score_threshold"] = score_threshold

    summarize_scan(providers, ports, config)

    if args.dry_run:
        for name, info in providers.items():
            logger.info(f"  {name}: {info['prefix_count']} prefixes, ~{info['estimated_ipv4_hosts']:,} IPs")
        logger.info("Dry run complete. No scanning performed.")
        return

    # Merge all prefixes
    all_prefixes = []
    for name, info in providers.items():
        all_prefixes.extend(info["ipv4_prefixes"])

    total_ips = sum(info.get("estimated_ipv4_hosts", 0) for info in providers.values())
    logger.info(f"Total: {len(all_prefixes):,} prefixes, ~{total_ips:,} IPv4 addresses")
    logger.info(f"When running on all prefixes with {rate} pkt/s × {parallel} instances = {rate*parallel} pkt/s total")

    # Check masscan
    if not check_masscan():
        logger.error("masscan not found. Install it: apt install masscan")
        sys.exit(1)

    # Init reporter
    reporter = Reporter(output_dir=output_dir)
    reporter.start()

    # Phase 0: zmap pre-filter (optional, with cache)
    alive_file = None
    alive_count = 0
    gateway_mac = None
    cached_alive_path = str(Path(output_dir) / "zmap_alive.txt")
    force_zmap = args.force_zmap if hasattr(args, 'force_zmap') else False

    if zmap_enabled and check_zmap():
        logger.info("Phase 0: zmap ICMP pre-filter")
        alive_file = cached_alive_path

        if os.path.exists(cached_alive_path) and not force_zmap:
            alive_count = sum(1 for _ in open(cached_alive_path))
            alive_pct = alive_count / total_ips * 100 if total_ips > 0 else 0
            logger.info(f"Phase 0: using cached zmap results ({alive_count:,} alive, {alive_pct:.1f}%)")
            reporter.add_phase_stat("phase0_zmap", {
                "alive_hosts": alive_count,
                "total_ips_estimated": total_ips,
                "response_rate_pct": round(alive_pct, 2),
                "cached": True,
            })
        else:
            if force_zmap and os.path.exists(cached_alive_path):
                logger.info("Phase 0: --force-zmap flag set, re-scanning")
            result = run_zmap(
                all_prefixes, ports,
                output_file=alive_file,
                rate=zmap_rate,
                retries=config["pre_filter"]["zmap_retries"],
                interface=interface or detect_default_interface(),
                gateway_mac=gateway_mac or detect_gateway_mac(),
                use_sudo=use_sudo,
            )
            if result:
                alive_count = sum(1 for _ in open(result))
                alive_pct = alive_count / total_ips * 100 if total_ips > 0 else 0
                reporter.add_phase_stat("phase0_zmap", {
                    "alive_hosts": alive_count,
                    "total_ips_estimated": total_ips,
                    "response_rate_pct": round(alive_pct, 2),
                    "cached": False,
                })
                logger.info(f"Phase 0 done: {alive_count:,} hosts alive ({alive_pct:.1f}%)")
            else:
                logger.warning("zmap failed, falling back to full prefix scan")
                alive_file = None

        if alive_file and alive_count > 0:
            port_count = _count_ports(ports)
            ph1_probes = alive_count * port_count
            ph1_pps = rate * parallel
            ph1_sec = ph1_probes / ph1_pps if ph1_pps > 0 else 0
            if ph1_sec > 3600:
                logger.info(f"  Phase 1 est: ~{ph1_sec/3600:.1f}h ({ph1_probes:,} probes)")
            elif ph1_sec > 60:
                logger.info(f"  Phase 1 est: ~{ph1_sec/60:.1f}m ({ph1_probes:,} probes)")
            else:
                logger.info(f"  Phase 1 est: ~{ph1_sec:.0f}s ({ph1_probes:,} probes)")

            if not config["output"]["save_raw_zmap"]:
                pass
    elif zmap_enabled:
        logger.info("Phase 0: skipped (zmap not installed)")
    else:
        logger.info("Phase 0: skipped (disabled)")

    # Phase 1: masscan
    logger.info("Phase 1: masscan port scan")
    reporter.add_phase_stat("phase1_masscan", {
        "prefixes": len(all_prefixes),
        "ports": ports,
        "rate": rate,
        "parallel": parallel,
    })

    masscan = MasscanRunner(
        rate=rate,
        parallel=parallel,
        port_workers=port_workers,
        batch_target_ips=batch_ips,
        max_retries=retries,
        interface=interface,
        router_ip=router_ip,
        use_sudo=use_sudo,
    )

    logger.info(f"  Detected interface: {masscan.interface}")
    logger.info(f"  Detected router:    {masscan.router_ip}")
    logger.info(f"  Using sudo:         {masscan.use_sudo}")
    logger.info(f"  Port workers:       {masscan.port_workers} (total masscan procs: {masscan.parallel * masscan.port_workers})")

    try:
        candidates = masscan.run(all_prefixes, ports, output_dir=str(Path(output_dir) / "scans"), alive_file=alive_file)
        reporter.add_candidates(len(candidates))
    except Exception as e:
        logger.error(f"Masscan failed: {e}")
        reporter.finish()
        reporter.save_matches()
        sys.exit(1)

    if not candidates:
        logger.info("No open ports found. Scan complete.")
        reporter.finish()
        reporter.save_matches()
        return

    logger.info(f"Phase 1 done: {len(candidates):,} candidate hosts")

    # Phase 2: HTTP fingerprint
    logger.info(f"Phase 2: HTTP fingerprint verification ({'LLM mode' if llm_mode else 'opencode mode'})")
    if llm_mode:
        engine = LLMFingerprintEngine(
            concurrency=http_conc,
            timeout=config["fingerprint"]["request_timeout"],
            score_threshold=score_threshold,
        )
    else:
        engine = FingerprintEngine(
            concurrency=http_conc,
            score_threshold=score_threshold,
        )

    matches = asyncio.run(
        engine.probe_candidates(candidates, reporter=reporter)
    )

    reporter.add_phase_stat("phase2_fingerprint", {
        "candidates": len(candidates),
        "matches": len(matches),
        "methods_used": config["fingerprint"]["verify_methods"],
    })

    # Apply min_version filter if requested
    if min_version and matches:
        def _parse_version(v: str) -> tuple:
            try:
                return tuple(int(x) for x in v.split("."))
            except (ValueError, AttributeError):
                return (0,)
        min_v = _parse_version(min_version)
        filtered = []
        for m in matches:
            health = m.get("details", {}).get("health", {})
            ver = health.get("version", "0")
            if _parse_version(ver) >= min_v:
                filtered.append(m)
        dropped = len(matches) - len(filtered)
        if dropped > 0:
            logger.info(f"  Version filter: dropped {dropped} matches below v{min_version}")
        matches = filtered
        reporter.stats["version_filter_min"] = min_version
        reporter.stats["version_filter_dropped"] = dropped

    # Phase 2b: Full sweep on confirmed IPs (two-phase mode)
    all_matches = list(matches)
    if args.full_sweep and matches:
        full_sweep_range = args.full_sweep  # e.g. "3000-65535"
        full_ports = [p.strip() for p in full_sweep_range.split(",")]
        confirmed_ips = list(set(m["ip"] for m in matches))
        label = "LLM" if llm_mode else "opencode"
        logger.info(f"Phase 2b: Full sweep ({full_sweep_range}) on {len(confirmed_ips)} confirmed {label} IPs")

        # Write confirmed IPs to temp file
        import tempfile
        sweep_file = tempfile.NamedTemporaryFile(mode="w", suffix="_sweep.txt", delete=False)
        for ip in confirmed_ips:
            sweep_file.write(ip + "\n")
        sweep_file.close()

        sweep_runner = MasscanRunner(
            rate=rate,
            parallel=1,
            port_workers=port_workers,
            batch_target_ips=len(confirmed_ips),
            max_retries=retries,
            interface=interface,
            router_ip=router_ip,
            use_sudo=use_sudo,
        )

        # Run masscan on confirmed IPs with full port range
        try:
            sweep_candidates = sweep_runner.run(
                [], full_ports,
                output_dir=str(Path(output_dir) / "scans_sweep"),
                alive_file=sweep_file.name,
            )
            os.unlink(sweep_file.name)
        except Exception as e:
            logger.error(f"Full sweep masscan failed: {e}")
            os.unlink(sweep_file.name)
            sweep_candidates = []

        if sweep_candidates:
            logger.info(f"Phase 2b masscan done: {len(sweep_candidates):,} candidates from {len(confirmed_ips)} IPs")

            # Remove the known ports from sweep_candidates to avoid re-fingerprinting
            known_ports = set(int(p.strip()) for p in args.ports.split(","))
            new_candidates = [(ip, port) for ip, port in sweep_candidates if port not in known_ports]
            logger.info(f"  New (non-known-port) candidates: {len(new_candidates):,}")

            if new_candidates:
                if llm_mode:
                    sweep_engine = LLMFingerprintEngine(
                        concurrency=http_conc,
                        timeout=config["fingerprint"]["request_timeout"],
                        score_threshold=score_threshold,
                    )
                else:
                    sweep_engine = FingerprintEngine(
                        concurrency=http_conc,
                        score_threshold=score_threshold,
                    )
                sweep_matches = asyncio.run(
                    sweep_engine.probe_candidates(new_candidates, reporter=reporter)
                )
                logger.info(f"Phase 2b fingerprint: {len(sweep_matches)} new matches")
                all_matches.extend(sweep_matches)

                reporter.add_phase_stat("phase2b_full_sweep", {
                    "full_sweep_range": full_sweep_range,
                    "confirmed_ips": len(confirmed_ips),
                    "sweep_candidates": len(sweep_candidates),
                    "new_matches": len(sweep_matches),
                })
        else:
            os.unlink(sweep_file.name)

    # Final report
    matches = all_matches

    # Enrich matches with provider info
    provider_index = _build_provider_index(providers_data)
    provider_stats: dict[str, dict] = {}
    for m in matches:
        provider, region = _resolve_provider(m["ip"], provider_index)
        m["provider"] = provider
        m["region"] = region
        key = provider
        if key not in provider_stats:
            provider_stats[key] = {"count": 0, "region": region}
        provider_stats[key]["count"] += 1

    reporter.stats["provider_breakdown"] = provider_stats

    reporter.finish()
    result_path = reporter.save_matches()

    # Write raw host:port file
    raw_filename = "llm_raw.txt" if llm_mode else "opencode_raw.txt"
    raw_path = str(Path(output_dir) / raw_filename)
    with open(raw_path, "w") as f:
        for m in matches:
            f.write(f"{m['ip']}:{m['port']}\n")
    logger.info(f"  Raw host:port list: {raw_path}")

    logger.info("=" * 60)
    logger.info("  SCAN COMPLETE")
    logger.info("=" * 60)
    logger.info(f"  Hosts scanned:    {total_ips:,}")
    logger.info(f"  Open ports found: {len(candidates):,}")
    logger.info(f"  Matches confirmed: {len(matches)}")
    if provider_stats:
        for prov, info in sorted(provider_stats.items(), key=lambda x: x[1]["count"], reverse=True):
            logger.info(f"    {prov}: {info['count']}")
    logger.info(f"  Results saved to: {result_path}")
    logger.info(f"  Raw host:port:    {raw_path}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
