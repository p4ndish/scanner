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


def _check_cancelled(scan_id: int):
    """Check if the scan has been cancelled via Redis flag."""
    import redis
    r = redis.from_url(settings.REDIS_URL)
    if r.get(f"scan:{scan_id}:cancelled"):
        raise ScanCancelled("Scan cancelled by user")


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
            batch_ips=5000000,
            retry=2,
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

def _probe_llm(match, prompt, timeout=10):
    """Send a prompt to an LLM endpoint and return the response text or None on failure."""
    import requests

    base_url = f"{match.scheme}://{match.ip}:{match.port}"
    service = match.service or "unknown"

    try:
        if service in ("ollama",):
            r = requests.post(
                f"{base_url}/api/generate",
                json={"model": "", "prompt": prompt, "stream": False},
                timeout=timeout,
            )
            if r.status_code == 200:
                return r.json().get("response", "")
            return None

        elif service in ("vllm", "textgen", "llamacpp", "openwebui", "anythingllm", "lm_studio"):
            r = requests.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": "",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 50,
                    "stream": False,
                },
                timeout=timeout,
            )
            if r.status_code == 200:
                choice = r.json().get("choices", [{}])[0]
                msg = choice.get("message", {})
                return msg.get("content", "")
            return None

        elif service == "kobold":
            r = requests.post(
                f"{base_url}/api/v1/generate",
                json={"prompt": prompt, "max_length": 50},
                timeout=timeout,
            )
            if r.status_code == 200:
                results = r.json().get("results", [{}])
                return results[0].get("text", "")
            return None

        else:
            # Fallback: try OpenAI-compatible
            r = requests.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": "",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 50,
                    "stream": False,
                },
                timeout=timeout,
            )
            if r.status_code == 200:
                choice = r.json().get("choices", [{}])[0]
                msg = choice.get("message", {})
                return msg.get("content", "")
            return None

    except Exception:
        return None


def _verify_single_match(match_dict):
    """Verify a single match. Returns (match_id, status, details)."""
    from types import SimpleNamespace
    match = SimpleNamespace(**match_dict)

    # Check 1: Canary token
    resp1 = _probe_llm(match, "reply only H3llo")
    canary_pass = resp1 is not None and "H3llo" in resp1

    # Check 2: Math question
    resp2 = _probe_llm(match, "What is 7 + 5?")
    math_pass = resp2 is not None and "12" in resp2

    # Check 3: Consistency (same prompt again)
    resp3 = _probe_llm(match, "reply only H3llo")
    consistency_pass = resp3 is not None and resp1 != resp3

    if resp1 is None and resp2 is None and resp3 is None:
        status = "unreachable"
    elif canary_pass and math_pass and consistency_pass:
        status = "legitimate"
    else:
        status = "honeypot"

    details = {
        "canary_pass": canary_pass,
        "math_pass": math_pass,
        "consistency_pass": consistency_pass,
        "responses": [
            {"check": "canary", "prompt": "reply only H3llo", "response": resp1},
            {"check": "math", "prompt": "What is 7 + 5?", "response": resp2},
            {"check": "consistency", "prompt": "reply only H3llo", "response": resp3},
        ],
    }

    return match.id, status, details


@celery_app.task(bind=True, max_retries=0)
def verify_matches_task(self, user_id: int, filters: dict = None):
    """Background task to verify LLM matches using 3-check honeypot detection."""
    import concurrent.futures
    import redis
    import os

    db = SessionLocal()
    try:
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        r = redis.from_url(redis_url)

        # Mark as running immediately
        r.setex(
            f"verify:{user_id}:progress",
            3600,
            json.dumps({"total": 0, "done": 0, "state": "running", "message": "Loading matches..."}),
        )

        # Build query — only select columns we need (much faster)
        q = db.query(
            Match.id, Match.ip, Match.port, Match.scheme, Match.service
        ).join(ScanJob).filter(
            ScanJob.user_id == user_id,
            Match.verified_status.in_(["pending", "unreachable"]),
        )

        if filters:
            if filters.get("provider"):
                q = q.filter(Match.provider == filters["provider"])
            if filters.get("service"):
                q = q.filter(Match.service == filters["service"])
            if filters.get("scan_id"):
                q = q.filter(Match.scan_job_id == filters["scan_id"])
            if filters.get("verified_status"):
                q = q.filter(Match.verified_status == filters["verified_status"])
            if filters.get("match_ids"):
                q = q.filter(Match.id.in_(filters["match_ids"]))
            elif filters.get("all_unreachable"):
                q = q.filter(Match.verified_status == "unreachable")

        rows = q.all()
        total = len(rows)

        if total == 0:
            r.setex(
                f"verify:{user_id}:progress",
                3600,
                json.dumps({"total": 0, "done": 0, "state": "completed"}),
            )
            return

        # Convert to dicts for pickling in ThreadPoolExecutor
        match_dicts = [
            {"id": rid, "ip": ip, "port": port, "scheme": scheme, "service": svc}
            for rid, ip, port, scheme, svc in rows
        ]

        counts = {"legitimate": 0, "honeypot": 0, "unreachable": 0}
        done = 0
        batch_updates = []
        BATCH_SIZE = 500

        def update_progress():
            r.setex(
                f"verify:{user_id}:progress",
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

        # Run verification with ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
            futures = {executor.submit(_verify_single_match, md): md for md in match_dicts}

            for future in concurrent.futures.as_completed(futures):
                match_id, status, details = future.result()
                counts[status] += 1
                done += 1

                batch_updates.append((match_id, status, details))

                # Flush batch to DB
                if len(batch_updates) >= BATCH_SIZE:
                    for mid, st, det in batch_updates:
                        db.query(Match).filter(Match.id == mid).update({
                            "verified_status": st,
                            "verified_at": datetime.utcnow(),
                            "verification_details": det,
                        })
                    db.commit()
                    batch_updates = []
                    update_progress()

        # Final batch
        if batch_updates:
            for mid, st, det in batch_updates:
                db.query(Match).filter(Match.id == mid).update({
                    "verified_status": st,
                    "verified_at": datetime.utcnow(),
                    "verification_details": det,
                })
            db.commit()

        # Final progress
        r.setex(
            f"verify:{user_id}:progress",
            3600,
            json.dumps({
                "total": total,
                "done": done,
                "legitimate": counts["legitimate"],
                "honeypot": counts["honeypot"],
                "unreachable": counts["unreachable"],
                "state": "completed",
            }),
        )

    except Exception as exc:
        import traceback
        r = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
        r.setex(
            f"verify:{user_id}:progress",
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
