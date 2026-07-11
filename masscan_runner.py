import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _ip_count(cidr: str) -> int:
    """Calculate number of addresses in a CIDR prefix."""
    try:
        return 2 ** (32 - int(cidr.split("/")[1]))
    except (ValueError, IndexError):
        return 0


def _expand_prefixes(prefixes: list[str]) -> list[str]:
    """Filter and sort IPv4 prefixes."""
    return sorted(
        [p for p in prefixes if ":" not in p],
        key=lambda x: (int(x.split("/")[1]), tuple(int(n) for n in x.split("/")[0].split("."))),
    )


def chunk_ports(ports: list, n_chunks: int) -> list[list]:
    """
    Split a mixed list of individual ports and ranges into n_chunks balanced groups.

    Input:  ["11434", "8080", "3000-4000", "5000"]
    Output: [["11434", "3000-3499"], ["8080", "3500-4000", "5000"]]  (for n_chunks=2)

    Ranges are expanded into individual port numbers, balanced across chunks,
    then re-collapsed back into masscan-compatible port strings.
    """
    if n_chunks <= 1:
        return [ports]

    # Expand everything to individual port numbers
    all_ports = []
    for p in ports:
        p = str(p).strip()
        if "-" in p:
            try:
                start, end = int(p.split("-")[0]), int(p.split("-")[1])
                all_ports.extend(range(start, end + 1))
            except ValueError:
                pass
        else:
            try:
                all_ports.append(int(p))
            except ValueError:
                pass

    all_ports = sorted(set(all_ports))
    if not all_ports:
        return [ports]

    # Split into n_chunks balanced slices
    chunk_size = math.ceil(len(all_ports) / n_chunks)
    chunks = []
    for i in range(0, len(all_ports), chunk_size):
        slice_ = all_ports[i:i + chunk_size]
        if not slice_:
            continue
        # Re-collapse consecutive runs into ranges to keep masscan CLI short
        collapsed = []
        start = prev = slice_[0]
        for port in slice_[1:]:
            if port == prev + 1:
                prev = port
            else:
                collapsed.append(f"{start}-{prev}" if prev != start else str(start))
                start = prev = port
        collapsed.append(f"{start}-{prev}" if prev != start else str(start))
        chunks.append(collapsed)

    return chunks


def balance_batches(
    prefixes: list[str],
    target_ip_count: int = 5_000_000,
    max_prefixes_per_batch: int = 500,
) -> list[list[str]]:
    """
    Split prefixes into balanced batches by estimated IP count.
    Each batch targets ~target_ip_count IPs, capped at max_prefixes_per_batch prefixes.
    """
    expanded = _expand_prefixes(prefixes)
    batches = []
    current_batch = []
    current_ips = 0

    for cidr in expanded:
        ips = _ip_count(cidr)
        if current_ips + ips > target_ip_count and current_batch:
            batches.append(current_batch)
            current_batch = [cidr]
            current_ips = ips
        elif len(current_batch) >= max_prefixes_per_batch:
            batches.append(current_batch)
            current_batch = [cidr]
            current_ips = ips
        else:
            current_batch.append(cidr)
            current_ips += ips

    if current_batch:
        batches.append(current_batch)

    return batches


def check_masscan() -> bool:
    """Check if masscan is installed and accessible."""
    return shutil.which("masscan") is not None


def check_zmap() -> bool:
    """Check if zmap is installed."""
    return shutil.which("zmap") is not None


def detect_default_interface() -> str:
    """Auto-detect the default network interface."""
    try:
        result = subprocess.run(
            ["ip", "route", "get", "1.1.1.1"],
            capture_output=True, text=True, timeout=5,
        )
        for word in result.stdout.split():
            if word == "dev":
                idx = result.stdout.split().index("dev")
                if idx + 1 < len(result.stdout.split()):
                    return result.stdout.split()[idx + 1]
    except Exception:
        pass

    # Fallback: check common interfaces
    for iface in ["eth0", "ens3", "enp0s3", "wlan0", "bond0"]:
        if os.path.exists(f"/sys/class/net/{iface}"):
            return iface

    return None


def detect_default_router() -> str:
    """Auto-detect the default router (gateway) IP."""
    try:
        result = subprocess.run(
            ["ip", "route", "get", "1.1.1.1"],
            capture_output=True, text=True, timeout=5,
        )
        for part in result.stdout.split():
            if part.startswith("via"):
                # "via 192.168.1.1 dev eth0" -> router is in the next "word"
                continue
        # Parse "x.x.x.x via y.y.y.y dev eth0"
        words = result.stdout.split()
        for i, w in enumerate(words):
            if w == "via" and i + 1 < len(words):
                return words[i + 1]
    except Exception:
        pass

    return None


def detect_gateway_mac(interface: str = None) -> str:
    """Auto-detect the gateway MAC address for zmap/masscan on bond interfaces."""
    if not interface:
        interface = detect_default_interface()
    router = detect_default_router()
    if not router or not interface:
        return None
    try:
        result = subprocess.run(
            ["ip", "neigh", "show", router],
            capture_output=True, text=True, timeout=5,
        )
        for part in result.stdout.split():
            if ":" in part and len(part) == 17:  # xx:xx:xx:xx:xx:xx
                return part
    except Exception:
        pass
    return None


def run_zmap(
    prefixes: list[str],
    ports: list,  # unused by zmap ICMP, kept for API consistency
    output_file: str,
    rate: int = 250000,
    retries: int = 1,
    blacklist: str = "/etc/zmap/blacklist.conf",
    interface: str = None,
    gateway_mac: str = None,
    use_sudo: bool = True,
) -> Optional[str]:
    """
    Run zmap ICMP echo scan to find responsive hosts.
    Saves responsive IPs to a file (one per line).
    Returns path to the responsive hosts file, or None on failure.
    """
    if not check_zmap():
        logger.warning("zmap not found. Skipping pre-filter.")
        return None

    # Auto-detect
    if not interface:
        interface = detect_default_interface()
    if not gateway_mac:
        gateway_mac = detect_gateway_mac(interface)

    # Write prefixes to temp file
    prefix_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    for cidr in prefixes:
        prefix_file.write(cidr + "\n")
    prefix_file.close()

    # Write responsive IPs
    alive_path = Path(output_file)
    alive_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = []
    if use_sudo:
        cmd.append("sudo")
    cmd += [
        "zmap",
        "--probe-module=icmp_echoscan",
        "--output-file", str(alive_path),
        "--whitelist-file", prefix_file.name,
        "--rate", str(rate),
        "--verbosity=0",
    ]
    if interface:
        cmd += ["-i", interface]
    if gateway_mac:
        cmd += ["--gateway-mac", gateway_mac]
    # Note: skipping blacklist — not needed for ICMP scanning
    # and flag name varies by zmap version

    logger.info(f"zmap: finding responsive hosts (rate={rate:,}, iface={interface})...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            logger.error(f"zmap failed (exit={result.returncode}): {result.stderr[:300]}")
            if "gateway" in result.stderr.lower() or "interface" in result.stderr.lower():
                logger.error("Hint: Try --interface and --gateway-mac flags")
            return None

        alive_count = 0
        with open(alive_path) as f:
            alive_count = sum(1 for _ in f)

        logger.info(f"zmap: found {alive_count:,} responsive hosts")
        os.unlink(prefix_file.name)
        return str(alive_path)
    except subprocess.TimeoutExpired:
        logger.error("zmap timed out after 1 hour")
        return None
    except Exception as e:
        logger.error(f"zmap error: {e}")
        return None


def _run_masscan_batch(
    prefix_file: str,
    ports: list[int],
    rate: int,
    output_file: str,
    index: int,
    total_batches: int,
    interface: str = None,
    router_ip: str = None,
    use_sudo: bool = False,
) -> tuple[int, int, str, bool]:
    """
    Run a single masscan instance on one batch.
    Returns (batch_index, hosts_found, output_file_path, was_error).
    """
    port_str = ",".join(str(p) for p in ports)
    cmd = []
    if use_sudo:
        cmd.append("sudo")
    cmd += [
        "masscan",
        "-iL", prefix_file,
        "-p", port_str,
        "--rate", str(rate),
        "-oJ", output_file,
        "--wait", "0",
        "--open-only",
    ]
    if interface:
        cmd += ["--interface", interface]
    if router_ip:
        cmd += ["--router-ip", router_ip]

    logger.info(f"[batch {index+1}/{total_batches}] masscan: starting (rate={rate})...")
    start = time.time()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Collect ALL stderr - masscan outputs progress and errors here
        stderr_lines = []
        for line in proc.stderr:
            line = line.strip()
            stderr_lines.append(line)
            if line and ("rate:" in line or "%" in line or "done" in line.lower()):
                if "100" in line or "done" in line.lower():
                    logger.debug(f"[batch {index+1}] {line}")

        proc.wait(timeout=7200)
        elapsed = time.time() - start

        # Check for errors
        rc = proc.returncode
        if rc != 0:
            error_text = "\n".join(stderr_lines[-5:])
            logger.error(
                f"[batch {index+1}] masscan FAILED (exit={rc}): {error_text}"
            )
            return (index, 0, output_file, True)

        # Check if masscan reported fatal errors in stderr
        stderr_full = "\n".join(stderr_lines)
        if "FAIL" in stderr_full or "FATAL" in stderr_full or "Permission denied" in stderr_full:
            logger.error(
                f"[batch {index+1}] masscan error in stderr:\n{stderr_full[:500]}"
            )
            return (index, 0, output_file, True)

        # Count results
        host_count = 0
        if os.path.exists(output_file):
            with open(output_file) as f:
                host_count = sum(1 for line in f if line.strip())

        logger.info(
            f"[batch {index+1}/{total_batches}] masscan: done in {elapsed:.1f}s, "
            f"{host_count} hosts found"
        )
        return (index, host_count, output_file, False)

    except subprocess.TimeoutExpired:
        proc.kill()
        logger.error(f"[batch {index+1}] masscan timed out after 2 hours")
        return (index, 0, output_file, True)
    except Exception as e:
        logger.error(f"[batch {index+1}] masscan error: {e}")
        return (index, 0, output_file, True)


def parse_masscan_json(output_file: str) -> list[tuple[str, int]]:
    """
    Parse masscan's JSON output format.
    Returns list of (ip, port) tuples.
    """
    candidates = []
    if not os.path.exists(output_file):
        return candidates

    with open(output_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Skip status lines that aren't valid JSON
            if line.startswith("[") or line.startswith("#"):
                continue
            try:
                record = json.loads(line)
                ip = record.get("ip", "")
                ports = record.get("ports", [])
                for p in ports:
                    candidates.append((ip, p["port"]))
            except json.JSONDecodeError:
                continue

    return candidates


class MasscanRunner:
    def __init__(
        self,
        rate: int = 2500,
        parallel: int = 4,
        port_workers: int = 1,
        batch_target_ips: int = 5_000_000,
        batch_max_prefixes: int = 500,
        max_retries: int = 2,
        retry_rate_multiplier: float = 0.5,
        interface: str = None,
        router_ip: str = None,
        use_sudo: bool = False,
    ):
        self.rate = rate
        self.parallel = parallel
        # port_workers: how many port-range chunks to scan concurrently.
        # Each chunk runs as its own masscan process in parallel with IP batches.
        # Total concurrent masscan processes = parallel × port_workers.
        self.port_workers = max(1, port_workers)
        self.batch_target_ips = batch_target_ips
        self.batch_max_prefixes = batch_max_prefixes
        self.max_retries = max_retries
        self.retry_rate_multiplier = retry_rate_multiplier
        self.interface = interface
        self.router_ip = router_ip
        self.use_sudo = use_sudo

        if not self.interface:
            self.interface = detect_default_interface()
        if not self.router_ip:
            self.router_ip = detect_default_router()

        if not check_masscan():
            raise RuntimeError(
                "masscan not found. Install it:\n"
                "  apt install masscan  (Debian/Ubuntu)\n"
                "  brew install masscan  (macOS)\n"
                "  or build from https://github.com/robertdavidgraham/masscan"
            )

    def run(
        self,
        prefixes: list[str],
        ports: list,
        output_dir: str = "scans",
        alive_file: Optional[str] = None,
    ) -> list[tuple[str, int]]:
        """
        Run masscan across all prefixes, optionally filtered by zmap results.

        Parallelism has two independent axes:
          --parallel N    : N masscan processes each covering a different IP batch
          --workers M     : M masscan processes each covering a different port chunk

        Total concurrent masscan processes = N × M.
        For a wide port range like 3000-65535 with --workers 8, each worker
        scans ~7,800 ports simultaneously across all IPs — 8× faster than serial.

        Returns deduplicated list of (ip, port) candidates.
        """
        scan_dir = Path(output_dir)
        scan_dir.mkdir(parents=True, exist_ok=True)

        total_ips = sum(_ip_count(p) for p in prefixes if ":" not in p)

        use_alive = alive_file and os.path.exists(alive_file)
        if use_alive:
            with open(alive_file) as f:
                alive_ips_list = [line.strip() for line in f if line.strip()]
            alive_count = len(alive_ips_list)
            alive_pct = alive_count / total_ips * 100 if total_ips > 0 else 0
            logger.info(
                f"Masscan: {alive_count:,} alive IPs ({alive_pct:.1f}% of ~{total_ips:,}), "
                f"ports={ports}, rate={self.rate}/instance, "
                f"parallel={self.parallel}, port_workers={self.port_workers}"
            )
        else:
            alive_ips_list = []
            logger.info(
                f"Masscan: {len(prefixes):,} prefixes, ~{total_ips:,} total IPs, "
                f"ports={ports}, rate={self.rate}/instance, "
                f"parallel={self.parallel}, port_workers={self.port_workers}"
            )

        # Split port list into port_workers chunks for concurrent scanning
        port_chunks = chunk_ports(ports, self.port_workers)
        if len(port_chunks) > 1:
            logger.info(f"  Port chunking: {len(port_chunks)} chunks across {self.port_workers} workers")
            for ci, pc in enumerate(port_chunks):
                logger.info(f"    chunk {ci+1}: {','.join(pc[:4])}{',...' if len(pc) > 4 else ''}")

        # Balance IP batches
        if use_alive:
            ip_batches = []
            for i in range(0, len(alive_ips_list), self.batch_target_ips):
                ip_batches.append(alive_ips_list[i:i + self.batch_target_ips])
            total_batch_ips = sum(len(b) for b in ip_batches)
        else:
            batches_raw = balance_batches(
                prefixes,
                target_ip_count=self.batch_target_ips,
                max_prefixes_per_batch=self.batch_max_prefixes,
            )
            total_batch_ips = sum(sum(_ip_count(c) for c in b) for b in batches_raw)
            ip_batches = batches_raw

        total_jobs = len(ip_batches) * len(port_chunks)
        logger.info(
            f"  {len(ip_batches)} IP batch(es) × {len(port_chunks)} port chunk(s) "
            f"= {total_jobs} total masscan jobs "
            f"(up to {self.parallel * self.port_workers} concurrent)"
        )

        # Write IP batch files (reused across all port chunks)
        batch_files = []
        for i, batch in enumerate(ip_batches):
            tf = tempfile.NamedTemporaryFile(mode="w", suffix=f"_ipbatch{i}.txt", delete=False)
            for entry in batch:
                tf.write(entry + "\n")
            tf.close()
            batch_files.append(tf.name)

        # Build the full job list: (ip_batch_idx, port_chunk_idx, ip_file, port_list, out_file)
        jobs = []
        for bi, ip_file in enumerate(batch_files):
            for ci, port_chunk in enumerate(port_chunks):
                out_file = str(scan_dir / f"masscan_batch_{bi:04d}_ports{ci:02d}.json")
                jobs.append((bi, ci, ip_file, port_chunk, out_file))

        all_candidates = []
        failed_jobs = []

        max_workers = self.parallel * self.port_workers
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for job_idx, (bi, ci, ip_file, port_chunk, out_file) in enumerate(jobs):
                label = f"ip{bi+1}/{len(ip_batches)}-ports{ci+1}/{len(port_chunks)}"
                future = executor.submit(
                    _run_masscan_batch,
                    ip_file, port_chunk, self.rate, out_file,
                    job_idx, total_jobs,
                    self.interface, self.router_ip, self.use_sudo,
                )
                futures[future] = (job_idx, bi, ci, ip_file, out_file, label)

            for future in as_completed(futures):
                job_idx, bi, ci, ip_file, out_file, label = futures[future]
                try:
                    idx, hosts_found, output_path, was_error = future.result(timeout=7200)
                    candidates = parse_masscan_json(output_path)
                    all_candidates.extend(candidates)
                    logger.info(
                        f"  [{label}] done: {hosts_found} hosts, "
                        f"{len(candidates)} open ports{' (error)' if was_error else ''}"
                    )
                    if hosts_found == 0 and not was_error and self.max_retries > 0:
                        failed_jobs.append((job_idx, bi, ci, ip_file, out_file))
                except Exception as e:
                    logger.error(f"  [{label}] futures error: {e}")

        # Retry failed jobs at lower rate
        for retry in range(self.max_retries):
            if not failed_jobs:
                break
            retry_rate = int(self.rate * (self.retry_rate_multiplier ** (retry + 1)))
            logger.info(f"  Retry round {retry+1}/{self.max_retries} at rate={retry_rate}")
            still_failed = []
            for job_idx, bi, ci, ip_file, out_file in failed_jobs:
                port_chunk = port_chunks[ci]
                try:
                    _, hosts, _, _ = _run_masscan_batch(
                        ip_file, port_chunk, retry_rate, out_file,
                        job_idx, total_jobs,
                        self.interface, self.router_ip, self.use_sudo,
                    )
                    candidates = parse_masscan_json(out_file)
                    all_candidates.extend(candidates)
                    if hosts == 0:
                        still_failed.append((job_idx, bi, ci, ip_file, out_file))
                except Exception:
                    pass
            failed_jobs = still_failed

        # Cleanup IP batch temp files
        for tf in batch_files:
            try:
                os.unlink(tf)
            except OSError:
                pass

        unique = list(set(all_candidates))
        logger.info(
            f"Masscan done: {len(unique):,} unique (ip, port) candidates "
            f"from {len(all_candidates):,} total hits"
        )
        return unique
