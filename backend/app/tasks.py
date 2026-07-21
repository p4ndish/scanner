import json
import os
import time
from datetime import datetime
from pathlib import Path

from celery import shared_task
from sqlalchemy.orm import Session

from backend.app.config import get_settings
from backend.app.database import SessionLocal
from backend.app.models import ScanJob, Match, ScanLog
from backend.app.worker import celery_app

settings = get_settings()


def _publish_event(scan_id: int, event_type: str, data: dict):
    """Publish scan event to Redis for SSE consumers."""
    import redis
    r = redis.from_url(settings.REDIS_URL)
    payload = json.dumps({"type": event_type, "data": data, "ts": time.time()})
    r.publish(f"scan:{scan_id}:events", payload)
    # Also keep latest state in Redis for reconnects
    r.setex(f"scan:{scan_id}:state", 3600, payload)


def _log(scan_id: int, phase: str, message: str):
    db = SessionLocal()
    try:
        log = ScanLog(scan_job_id=scan_id, phase=phase, message=message)
        db.add(log)
        db.commit()
        _publish_event(scan_id, "log", {"phase": phase, "message": message})
    finally:
        db.close()


def _update_status(scan_id: int, status: str, error: str = None):
    db = SessionLocal()
    try:
        job = db.query(ScanJob).filter(ScanJob.id == scan_id).first()
        if job:
            job.status = status
            if status == "running" and not job.started_at:
                job.started_at = datetime.utcnow()
            if status in ("completed", "failed", "cancelled"):
                job.completed_at = datetime.utcnow()
            if error:
                job.error_message = error
            db.commit()
            _publish_event(scan_id, "status", {"status": status, "error": error})
    finally:
        db.close()


class ScanCancelled(Exception):
    """Raised when a scan is cancelled by the user."""
    pass


def _split_list(val):
    """Parse a comma-separated filter value into a clean list. None/empty -> []."""
    if not val:
        return []
    return [v.strip() for v in val.split(",") if v.strip()]


def _check_cancelled(scan_id: int):
    """Check if the scan has been cancelled via Redis flag."""
    import redis
    r = redis.from_url(settings.REDIS_URL)
    if r.get(f"scan:{scan_id}:cancelled"):
        raise ScanCancelled("Scan cancelled by user")


def _is_cancelled(scan_id: int) -> bool:
    """Non-raising variant of _check_cancelled for the SSH polling loop."""
    import redis
    r = redis.from_url(settings.REDIS_URL)
    return bool(r.get(f"scan:{scan_id}:cancelled"))


def run_remote_scan(scan_id: int, job, db: Session):
    """Execute a scan on a remote SSH machine and import the results back."""
    import tempfile
    from backend.app.models import ScanMachine
    from backend.app.crypto import decrypt
    from backend.app.ssh_runner import SSHRunner, SSHError, SSHCancelled, build_scanner_cli
    from backend.app.import_core import import_into_existing_job

    machine = db.query(ScanMachine).filter(ScanMachine.id == job.machine_id).first()
    if not machine:
        raise RuntimeError(f"ScanMachine {job.machine_id} not found")

    secret = decrypt(machine.encrypted_secret)
    if not secret:
        raise RuntimeError("Could not decrypt machine credentials (ENCRYPTION_KEY mismatch?)")

    _update_status(scan_id, "running")
    _log(scan_id, "init", f"Remote scan on {machine.name} ({machine.username}@{machine.host}:{machine.port})")

    runner = SSHRunner(machine.host, machine.port, machine.username, machine.auth_type, secret)
    try:
        _log(scan_id, "connect", "Connecting over SSH...")
        runner.connect(timeout=20)
        _log(scan_id, "connect", "Connected")

        ok, msg = runner.check_masscan()
        if not ok:
            raise RuntimeError(f"masscan is not installed on {machine.name}. Install it on the remote host.")
        _log(scan_id, "connect", f"masscan available: {msg}")

        runner.sync_if_stale(log=lambda m: _log(scan_id, "sync", m))
        runner.ensure_deps(log=lambda m: _log(scan_id, "sync", m))

        remote_output = f"/tmp/opencode_scan_{scan_id}"
        cmd = build_scanner_cli(job, remote_output, use_sudo=machine.use_sudo)
        _log(scan_id, "config", f"Remote: {cmd}")

        exit_code = runner.run_scanner(
            cmd,
            on_line=lambda line: _log(scan_id, "remote", line) if line.strip() else None,
            cancel_check=lambda: _is_cancelled(scan_id),
        )

        if exit_code != 0:
            raise RuntimeError(f"Remote scanner exited with code {exit_code}")

        _log(scan_id, "fetch", "Fetching results from remote...")
        tmp = tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".json")
        tmp.close()
        runner.fetch_file(f"{remote_output}/results.json", tmp.name)

        _log(scan_id, "fetch", "Importing results into this scan...")
        imported, skipped = import_into_existing_job(tmp.name, scan_id, db)

        try:
            os.unlink(tmp.name)
        except OSError:
            pass

        _log(scan_id, "done", f"Scan complete: {imported:,} matches imported (skipped {skipped} dups)")
        _publish_event(scan_id, "done", {"matches": imported})
        _update_status(scan_id, "completed")

    except SSHCancelled:
        _log(scan_id, "cancelled", "Remote scan cancelled by user")
        _update_status(scan_id, "cancelled")
    finally:
        runner.close()


@celery_app.task(bind=True, max_retries=0)
def run_scan_task(self, scan_id: int):
    """Main Celery task that executes a scanner job."""
    import sys
    import argparse
    import asyncio

    # Import scanner modules
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    import scanner
    from masscan_runner import MasscanRunner, check_masscan, check_zmap, run_zmap, detect_default_interface, detect_gateway_mac
    from fingerprint import FingerprintEngine
    from llm_fingerprint import LLMFingerprintEngine
    from reporter import Reporter

    db = SessionLocal()
    try:
        job = db.query(ScanJob).filter(ScanJob.id == scan_id).first()
        if not job:
            raise ValueError(f"ScanJob {scan_id} not found")

        # If user cancelled while this task was queued, respect it immediately
        if job.status == "cancelled":
            _log(scan_id, "cancelled", "Scan was cancelled before it started")
            return

        # Remote SSH scan path — dispatch and return
        if job.machine_id:
            try:
                run_remote_scan(scan_id, job, db)
            except ScanCancelled:
                _log(scan_id, "cancelled", "Scan cancelled by user")
                _update_status(scan_id, "cancelled")
            except Exception as exc:
                import traceback
                _log(scan_id, "error", str(exc))
                _update_status(scan_id, "failed", error=str(exc))
                raise
            finally:
                db.close()
            return

        _update_status(scan_id, "running")
        _log(scan_id, "init", f"Starting scan: {job.name}")

        # Check Redis flag BEFORE clearing it (race-condition guard)
        import redis
        r = redis.from_url(settings.REDIS_URL)
        if r.get(f"scan:{scan_id}:cancelled"):
            _log(scan_id, "cancelled", "Scan was cancelled before it started (Redis flag)")
            _update_status(scan_id, "cancelled")
            return
        r.delete(f"scan:{scan_id}:cancelled")

        # Build args like the CLI would
        output_dir = str(Path(settings.RESULTS_DIR) / f"scan_{scan_id}")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        is_single_ip = bool(job.target_ip)
        providers = ",".join(job.providers) if job.providers else ""
        ports_str = ",".join(job.ports) if job.ports else ("4096,3000,8080" if not job.llm_mode else "11434,8080,8000,1234,5000,5001,7860,8888,3001")

        from types import SimpleNamespace

        args = SimpleNamespace(
            providers=providers,
            ports=ports_str,
            llm_mode=job.llm_mode,
            full_sweep=job.full_sweep,
            rate=job.rate,
            parallel=job.parallel,
            workers=job.workers,
            http_concurrency=1000 if job.llm_mode else 500,
            score=job.score_threshold,
            high_confidence=False,
            min_version=None,
            skip_ping=False,
            force_zmap=False,
            zmap_rate=250000,
            batch_ips=2000000,
            retry=job.retry if job.retry is not None else 1,
            interface=None,
            router_ip=None,
            use_sudo=False,
            output=output_dir,
            raw=True,
            dry_run=False,
            all=False,
        )

        if is_single_ip:
            _log(scan_id, "config", f"Target: {job.target_ip} | Ports: {ports_str} | LLM: {job.llm_mode} | Rate: {job.rate}")
        else:
            _log(scan_id, "config", f"Providers: {providers} | Ports: {ports_str} | LLM: {job.llm_mode} | Rate: {job.rate}")

        if not check_masscan():
            raise RuntimeError("masscan not found in worker container")

        ports = [p.strip() for p in ports_str.split(",")]
        candidates = []
        providers_data = {}
        provider_index = []

        if is_single_ip:
            # ─── Single-IP scan path ───
            _log(scan_id, "phase0", "Single IP mode — skipping provider resolution and zmap")

            import tempfile
            from masscan_runner import _run_masscan_batch, parse_masscan_json, chunk_ports

            ip_file = tempfile.NamedTemporaryFile(mode="w", suffix="_single_ip.txt", delete=False)
            ip_file.write(job.target_ip + "\n")
            ip_file.close()

            # Run masscan directly (no ProcessPoolExecutor — Celery daemon can't spawn children)
            scan_dir = Path(output_dir) / "scans"
            scan_dir.mkdir(parents=True, exist_ok=True)
            port_chunks = chunk_ports(ports, max(1, args.workers))
            candidates = []

            for ci, port_chunk in enumerate(port_chunks):
                out_file = str(scan_dir / f"masscan_single_ports{ci:02d}.json")
                _log(scan_id, "phase1", f"masscan single-IP chunk {ci+1}/{len(port_chunks)}: ports {','.join(port_chunk)}")
                _, hosts_found, output_path, was_error = _run_masscan_batch(
                    ip_file.name, port_chunk, args.rate, out_file,
                    ci, len(port_chunks),
                    None, None, args.use_sudo,
                )
                chunk_candidates = parse_masscan_json(output_path)
                candidates.extend(chunk_candidates)
                _log(scan_id, "phase1", f"  chunk {ci+1} done: {len(chunk_candidates)} open ports")

            try:
                os.unlink(ip_file.name)
            except OSError:
                pass

            candidates = list(set(candidates))
            _log(scan_id, "phase1", f"Masscan complete: {len(candidates):,} open ports on {job.target_ip}")
            _publish_event(scan_id, "progress", {"phase": "masscan", "candidates": len(candidates)})

            # Also create a minimal provider_index for the single IP
            try:
                import ipaddress
                ranges_path = os.path.join(os.path.dirname(os.path.abspath(scanner.__file__)), "cloud_providers.json")
                providers_data = scanner.load_providers(ranges_path)
                provider_index = scanner._build_provider_index(providers_data)
            except Exception:
                pass

        else:
            # ─── Cloud-provider scan path (original) ───
            # Phase 0: zmap (if available)
            if check_zmap() and not args.skip_ping:
                _log(scan_id, "phase0", "Running zmap ICMP pre-filter...")
                _log(scan_id, "phase0", "zmap pre-filter skipped in web mode (masscan will scan all)")
            else:
                _log(scan_id, "phase0", "zmap not available or disabled")

            # Phase 1: masscan
            _log(scan_id, "phase1", "Starting masscan port scan...")

            ranges_path = os.path.join(os.path.dirname(os.path.abspath(scanner.__file__)), "cloud_providers.json")
            providers_data = scanner.load_providers(ranges_path)
            try:
                selected_providers = scanner.resolve_providers({"targets": {"providers": providers.split(",")}}, providers_data)
            except SystemExit as exc:
                raise RuntimeError("No valid providers selected") from exc

            all_prefixes = []
            for name, info in selected_providers.items():
                all_prefixes.extend(info["ipv4_prefixes"])

            masscan_runner = MasscanRunner(
                rate=args.rate,
                parallel=args.parallel,
                port_workers=args.workers,
                batch_target_ips=args.batch_ips,
                max_retries=args.retry,
                use_sudo=args.use_sudo,
            )

            candidates = masscan_runner.run(
                all_prefixes, ports,
                output_dir=str(Path(output_dir) / "scans"),
                alive_file=None,
            )

            _log(scan_id, "phase1", f"Masscan complete: {len(candidates):,} candidates found")
            _publish_event(scan_id, "progress", {"phase": "masscan", "candidates": len(candidates)})

            provider_index = scanner._build_provider_index(providers_data)

        if not candidates:
            _log(scan_id, "phase2", "No open ports to fingerprint")
            _update_status(scan_id, "completed")
            return

        # Checkpoint: user may have cancelled while masscan was running
        _check_cancelled(scan_id)

        # Phase 2: fingerprint
        _log(scan_id, "phase2", f"Fingerprinting {len(candidates):,} candidates...")

        reporter = Reporter(output_dir=output_dir)
        reporter.start()
        reporter.add_candidates(len(candidates))

        if args.llm_mode:
            engine = LLMFingerprintEngine(
                concurrency=args.http_concurrency,
                timeout=3.0,
                score_threshold=args.score,
            )
        else:
            engine = FingerprintEngine(
                concurrency=args.http_concurrency,
                score_threshold=args.score,
            )

        matches = asyncio.run(engine.probe_candidates(candidates, reporter=reporter))

        _log(scan_id, "phase2", f"Fingerprint complete: {len(matches)} matches")
        _publish_event(scan_id, "progress", {"phase": "fingerprint", "matches": len(matches)})

        # Checkpoint: before full sweep
        _check_cancelled(scan_id)

        # Full sweep (cloud mode only, or single-ip if full_sweep set)
        all_matches = list(matches)
        if args.full_sweep and matches:
            _log(scan_id, "phase2b", f"Full sweep on {len(set(m['ip'] for m in matches))} confirmed IPs...")
            confirmed_ips = list(set(m["ip"] for m in matches))
            import tempfile
            sweep_file = tempfile.NamedTemporaryFile(mode="w", suffix="_sweep.txt", delete=False)
            for ip in confirmed_ips:
                sweep_file.write(ip + "\n")
            sweep_file.close()

            sweep_ports = [p.strip() for p in args.full_sweep.split(",")]
            sweep_runner = MasscanRunner(
                rate=args.rate,
                parallel=1,
                port_workers=args.workers,
                batch_target_ips=len(confirmed_ips),
                max_retries=args.retry,
                use_sudo=args.use_sudo,
            )
            try:
                sweep_candidates = sweep_runner.run(
                    [], sweep_ports,
                    output_dir=str(Path(output_dir) / "scans_sweep"),
                    alive_file=sweep_file.name,
                )
            except Exception as e:
                _log(scan_id, "phase2b", f"Full sweep error: {e}")
                sweep_candidates = []
            finally:
                import os as _os
                _os.unlink(sweep_file.name)

            if sweep_candidates:
                known_ports = set(int(p.strip()) for p in ports_str.split(","))
                new_candidates = [(ip, port) for ip, port in sweep_candidates if port not in known_ports]
                _log(scan_id, "phase2b", f"New candidates from sweep: {len(new_candidates):,}")
                if new_candidates:
                    if args.llm_mode:
                        sweep_engine = LLMFingerprintEngine(concurrency=args.http_concurrency, timeout=3.0, score_threshold=args.score)
                    else:
                        sweep_engine = FingerprintEngine(concurrency=args.http_concurrency, score_threshold=args.score)
                    sweep_matches = asyncio.run(sweep_engine.probe_candidates(new_candidates, reporter=reporter))
                    all_matches.extend(sweep_matches)
                    _log(scan_id, "phase2b", f"New matches from sweep: {len(sweep_matches)}")

        # Enrich and save
        provider_stats = {}
        for m in all_matches:
            provider, region = scanner._resolve_provider(m["ip"], provider_index)
            m["provider"] = provider
            m["region"] = region
            key = provider
            if key not in provider_stats:
                provider_stats[key] = {"count": 0, "region": region}
            provider_stats[key]["count"] += 1

        reporter.stats["provider_breakdown"] = provider_stats
        reporter.finish()
        reporter.save_matches()

        # Persist matches to DB
        for m in all_matches:
            match = Match(
                scan_job_id=scan_id,
                ip=m["ip"],
                port=m["port"],
                scheme=m.get("scheme", "http"),
                score=m.get("score", 0),
                service=m.get("service", "unknown"),
                provider=m.get("provider"),
                region=m.get("region"),
                methods_hit=m.get("methods_hit", []),
                details_json=m.get("details", {}),
            )
            db.add(match)

        job.stats_json = reporter.stats
        db.commit()

        _log(scan_id, "done", f"Scan complete: {len(all_matches)} total matches")
        _publish_event(scan_id, "done", {"matches": len(all_matches)})
        _update_status(scan_id, "completed")

    except ScanCancelled as exc:
        _log(scan_id, "cancelled", "Scan cancelled by user")
        _update_status(scan_id, "cancelled")
        # Don't re-raise — this is a clean exit
    except Exception as exc:
        import traceback
        err = traceback.format_exc()
        _log(scan_id, "error", str(exc))
        _update_status(scan_id, "failed", error=str(exc))
        raise
    finally:
        db.close()


# ─── Match Verification Task ───

from backend.app.llm_probe import verify_endpoint


# Per-request probe timeout (seconds). Bumped when proxying since proxies add latency.
_VERIFY_TIMEOUT = 4


def _verify_single_match(match_dict):
    """Verify a single match. Returns (match_id, status, details)."""
    match = match_dict
    status, details = verify_endpoint(
        match["ip"], match["port"], match["scheme"], timeout=_VERIFY_TIMEOUT
    )
    return match["id"], status, details


@celery_app.task(bind=True, max_retries=0)
def verify_matches_task(self, user_id: int, filters: dict = None, use_proxy: bool = False):
    """Background task to verify LLM matches using 3-check honeypot detection.

    Processes matches in small DB chunks to avoid loading everything into memory.
    When use_proxy is True, routes all verification requests through the user's
    configured proxy pool (round-robin).
    """
    import concurrent.futures
    import redis
    import os

    # Activate the proxy pool if requested
    active_proxy = False
    proxy_count = 0
    global _VERIFY_TIMEOUT
    if use_proxy:
        from backend.app.models import ProxyConfig
        from backend.app.api.proxies import build_proxy_url
        from backend.app.llm_probe import set_proxy_pool
        pdb = SessionLocal()
        try:
            proxy_rows = (
                pdb.query(ProxyConfig)
                .filter(ProxyConfig.user_id == user_id)
                .all()
            )
            proxy_urls = [build_proxy_url(p) for p in proxy_rows]
        finally:
            pdb.close()
        if proxy_urls:
            set_proxy_pool(proxy_urls)
            active_proxy = True
            proxy_count = len(proxy_urls)
            # Proxies add latency and choke on high concurrency — bump the
            # per-request timeout and let the caller lower worker count.
            _VERIFY_TIMEOUT = 10

            # Pre-flight: make sure the proxy actually works before grinding
            # through thousands of hosts. A dead/slow proxy would otherwise mark
            # every host "unreachable" and waste minutes/hours.
            import requests as _requests
            import time as _time
            PROXY_MAX_LATENCY = 8.0  # seconds; slower = unusable for verification
            alive = []
            slow_msg = None
            for purl in proxy_urls:
                t0 = _time.time()
                try:
                    pr = _requests.get("http://ip-api.com/json",
                                       proxies={"http": purl, "https": purl}, timeout=12)
                    lat = _time.time() - t0
                    if pr.status_code == 200:
                        if lat > PROXY_MAX_LATENCY:
                            print(f"proxy pre-flight SLOW: {purl.split('@')[-1]} ({lat:.1f}s > {PROXY_MAX_LATENCY}s)")
                            slow_msg = f"Proxy {purl.split('@')[-1]} responded in {lat:.1f}s — too slow for verification (needs < {PROXY_MAX_LATENCY:.0f}s). Get a faster proxy."
                        else:
                            alive.append(purl)
                            print(f"proxy pre-flight OK: {purl.split('@')[-1]} ({lat:.1f}s)")
                except Exception as e:
                    print(f"proxy pre-flight FAIL: {purl.split('@')[-1]} -> {type(e).__name__}")
                    slow_msg = f"All proxies failed the pre-flight check (timeout/unreachable). The proxy is down or unreachable from the worker."
            if not alive:
                # All proxies are dead/slow — abort with a clear state instead of
                # producing a meaningless all-unreachable result.
                set_proxy_pool(None)
                _VERIFY_TIMEOUT = 4
                _r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
                scope = f"scan_{filters['scan_id']}" if filters and filters.get("scan_id") else "all"
                _r.setex(f"verify:{user_id}:{scope}:progress", 3600, json.dumps({
                    "total": 0, "done": 0, "state": "failed",
                    "error": slow_msg or "All proxies failed the pre-flight check.",
                }))
                return
            # Keep only the alive proxies in the pool
            if len(alive) < len(proxy_urls):
                set_proxy_pool(alive)
                proxy_count = len(alive)
        else:
            # No proxies configured — proceed without (but note it)
            set_proxy_pool(None)

    # Concurrency through a proxy. A robust proxy (3proxy maxconn 1000) handles
    # 800+ concurrent with flat latency, so 120 per proxy is a good balance:
    # fast, but conservative enough not to exhaust the upstream router's NAT
    # table over a long verify. Scales with the number of proxies; capped at 300.
    max_workers = min(300, max(20, 120 * proxy_count)) if active_proxy else 200

    try:
        return _run_verification(self, user_id, filters or {}, active_proxy, max_workers)
    finally:
        if active_proxy:
            from backend.app.llm_probe import clear_proxy_pool
            clear_proxy_pool()
            _VERIFY_TIMEOUT = 4


def _run_verification(self, user_id: int, filters: dict, using_proxy: bool, max_workers: int = 200):
    """Inner verification loop (executed under the proxy-pool context)."""
    import concurrent.futures
    import redis
    import os

    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    r = redis.from_url(redis_url)

    db = SessionLocal()
    # Progress is scoped per verify target so the UI can show import-specific
    # progress instead of a single global counter. scope = scan_<id> or "all".
    scope = f"scan_{filters['scan_id']}" if filters and filters.get("scan_id") else "all"
    pkey = f"verify:{user_id}:{scope}:progress"
    try:
        # ── Phase 1: count total ──
        q = db.query(Match.id).join(ScanJob).filter(
            ScanJob.user_id == user_id,
            Match.verified_status.in_(["pending", "unreachable"]),
        )

        if filters:
            if filters.get("provider"):
                q = q.filter(Match.provider.in_(_split_list(filters["provider"])))
            if filters.get("service"):
                q = q.filter(Match.service.in_(_split_list(filters["service"])))
            if filters.get("scan_id"):
                q = q.filter(Match.scan_job_id == filters["scan_id"])
            if filters.get("verified_status"):
                q = q.filter(Match.verified_status.in_(_split_list(filters["verified_status"])))
            if filters.get("match_ids"):
                q = q.filter(Match.id.in_(filters["match_ids"]))
            elif filters.get("all_unreachable"):
                q = q.filter(Match.verified_status == "unreachable")

        total = q.count()

        if total == 0:
            r.setex(
                pkey,
                3600,
                json.dumps({"total": 0, "done": 0, "state": "completed"}),
            )
            return

        r.setex(
            pkey,
            3600,
            json.dumps({"total": total, "done": 0, "state": "running", "using_proxy": using_proxy}),
        )

        counts = {"legitimate": 0, "honeypot": 0, "unreachable": 0}
        done = 0
        CHUNK_SIZE = 500  # DB rows per chunk
        DB_BATCH = 25     # flush progress every N verified (lower = more responsive UI, esp. slow proxy verifies)

        def update_progress():
            r.setex(
                pkey,
                3600,
                json.dumps({
                    "total": total,
                    "done": done,
                    "legitimate": counts["legitimate"],
                    "honeypot": counts["honeypot"],
                    "unreachable": counts["unreachable"],
                    "state": "running",
                }),
            )

        # ── Phase 2: stream through DB in chunks ──
        offset = 0
        cancelled = False
        while offset < total:
            # Check for cancellation between chunks (POST /matches/verify/cancel)
            if r.get(f"verify:{user_id}:cancel"):
                cancelled = True
                break

            # Fetch one chunk
            chunk_q = db.query(
                Match.id, Match.ip, Match.port, Match.scheme, Match.service
            ).join(ScanJob).filter(
                ScanJob.user_id == user_id,
                Match.verified_status.in_(["pending", "unreachable"]),
            )

            # Re-apply filters
            if filters:
                if filters.get("provider"):
                    chunk_q = chunk_q.filter(Match.provider == filters["provider"])
                if filters.get("service"):
                    chunk_q = chunk_q.filter(Match.service == filters["service"])
                if filters.get("scan_id"):
                    chunk_q = chunk_q.filter(Match.scan_job_id == filters["scan_id"])
                if filters.get("verified_status"):
                    chunk_q = chunk_q.filter(Match.verified_status == filters["verified_status"])
                if filters.get("match_ids"):
                    chunk_q = chunk_q.filter(Match.id.in_(filters["match_ids"]))
                elif filters.get("all_unreachable"):
                    chunk_q = chunk_q.filter(Match.verified_status == "unreachable")

            rows = chunk_q.order_by(Match.id).offset(offset).limit(CHUNK_SIZE).all()
            if not rows:
                break

            match_dicts = [
                {"id": rid, "ip": ip, "port": port, "scheme": scheme, "service": svc}
                for rid, ip, port, scheme, svc in rows
            ]

            # Verify chunk concurrently
            batch_updates = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_verify_single_match, md): md for md in match_dicts}
                for future in concurrent.futures.as_completed(futures):
                    match_id, status, details = future.result()
                    counts[status] += 1
                    done += 1
                    batch_updates.append((match_id, status, details))

                    # Flush to DB
                    if len(batch_updates) >= DB_BATCH:
                        for mid, st, det in batch_updates:
                            db.query(Match).filter(Match.id == mid).update({
                                "verified_status": st,
                                "verified_at": datetime.utcnow(),
                                "verification_details": det,
                                "model_type": det.get("model_type"),
                            })
                        db.commit()
                        batch_updates = []
                        update_progress()

            # Flush remaining in chunk
            if batch_updates:
                for mid, st, det in batch_updates:
                    db.query(Match).filter(Match.id == mid).update({
                        "verified_status": st,
                        "verified_at": datetime.utcnow(),
                        "verification_details": det,
                        "model_type": det.get("model_type"),
                    })
                db.commit()
                update_progress()

            offset += len(rows)

        # Clear the cancel flag if we consumed it
        if cancelled:
            r.delete(f"verify:{user_id}:cancel")

        # ── Phase 3: done (or cancelled) ──
        r.setex(
            pkey,
            3600,
            json.dumps({
                "total": total,
                "done": done,
                "legitimate": counts["legitimate"],
                "honeypot": counts["honeypot"],
                "unreachable": counts["unreachable"],
                "state": "cancelled" if cancelled else "completed",
            }),
        )

    except Exception as exc:
        import traceback
        r.setex(
            pkey,
            3600,
            json.dumps({
                "total": total if 'total' in dir() else 0,
                "done": done if 'done' in dir() else 0,
                "state": "failed",
                "error": str(exc),
            }),
        )
        raise
    finally:
        db.close()
