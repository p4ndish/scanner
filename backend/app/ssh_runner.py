"""SSH-based remote scanner execution.

Flow: connect → sync scanner code (hybrid: only if stale) → ensure Python deps
→ run scanner.py remotely with live stdout streaming → fetch results.json back.

Used by the Celery worker (tasks.py) when a ScanJob targets a ScanMachine.
"""
import io
import os
import shlex
import time
from pathlib import Path

import paramiko


# Scanner files that must exist on the remote machine to run a scan.
SCANNER_FILES = [
    "scanner.py",
    "fingerprint.py",
    "llm_fingerprint.py",
    "masscan_runner.py",
    "reporter.py",
    "cloud_providers.json",
]

# Bump to force a re-sync of code to all remotes (e.g. after a scanner.py change).
DEPLOY_VERSION = "v1"

REMOTE_REQUIREMENTS = ["aiohttp>=3.9.0", "requests>=2.31.0"]


class SSHError(Exception):
    pass


class SSHCancelled(Exception):
    """Raised when a remote scan is cancelled by the user."""
    pass


def _load_private_key(secret: str):
    """Try parsing a private key string as Ed25519/ECDSA/RSA/DSS."""
    sio = io.StringIO(secret)
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey, paramiko.DSSKey):
        try:
            sio.seek(0)
            return cls.from_private_key(sio)
        except paramiko.SSHException:
            continue
    raise SSHError("Could not parse private key (tried Ed25519/ECDSA/RSA/DSS)")


class SSHRunner:
    def __init__(self, host, port, username, auth_type, secret):
        self.host = host
        self.port = port
        self.username = username
        self.auth_type = auth_type  # "key" | "password"
        self.secret = secret
        self.client = None
        self._home = None
        self._remote_dir = None
        self._current_pid_file = None

    # ── connection ──

    def connect(self, timeout=15):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.auth_type == "key":
            kwargs["pkey"] = _load_private_key(self.secret)
        else:
            kwargs["password"] = self.secret
        try:
            client.connect(**kwargs)
        except paramiko.AuthenticationException as e:
            raise SSHError(f"Authentication failed: {e}")
        except paramiko.SSHException as e:
            raise SSHError(f"SSH connection failed: {e}")
        except OSError as e:
            raise SSHError(f"Could not reach {self.host}:{self.port}: {e}")
        self.client = client
        self._home = self._exec_capture("echo $HOME").strip()
        self._remote_dir = f"{self._home}/opencode-scanner"

    def close(self):
        if self.client:
            self.client.close()
            self.client = None

    # ── low-level exec helpers ──

    def _exec_capture(self, cmd, timeout=30):
        """Run a command, return combined stdout (str)."""
        _, stdout, _ = self.client.exec_command(cmd, timeout=timeout)
        return stdout.read().decode("utf-8", errors="replace")

    def _exec_rc(self, cmd, timeout=30):
        """Run a command, return (rc, stdout)."""
        _, stdout, _ = self.client.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        return rc, out

    # ── code sync (hybrid) ──

    def sync_if_stale(self, log=None):
        """Ensure scanner code is present and up to date on the remote.

        Returns True if files were (re)synced, False if already current.
        """
        self._exec_rc(f"mkdir -p {shlex.quote(self._remote_dir)}")
        marker = f"{self._remote_dir}/.deployed_version"
        rc, out = self._exec_rc(f"cat {marker} 2>/dev/null")
        if rc == 0 and out.strip() == DEPLOY_VERSION:
            if log:
                log(f"Remote code current ({DEPLOY_VERSION}) — skipping sync")
            return False

        if log:
            log(f"Syncing scanner code to {self._remote_dir} ...")
        repo_root = Path(__file__).resolve().parent.parent.parent
        sftp = self.client.open_sftp()
        try:
            for fname in SCANNER_FILES:
                local = repo_root / fname
                if local.exists():
                    sftp.put(str(local), f"{self._remote_dir}/{fname}")
        finally:
            sftp.close()

        # write version marker
        self._exec_rc(f"echo '{DEPLOY_VERSION}' > {marker}")
        if log:
            log("Sync complete")
        return True

    def ensure_deps(self, log=None):
        """Install missing Python deps on the remote (aiohttp, requests)."""
        rc, _ = self._exec_rc("python3 -c 'import aiohttp, requests' 2>/dev/null")
        if rc == 0:
            return
        if log:
            log("Installing Python deps on remote (one-time)...")
        pkgs = " ".join(REMOTE_REQUIREMENTS)
        rc, out = self._exec_rc(f"pip3 install -q {pkgs} 2>&1 || pip install -q {pkgs} 2>&1", timeout=300)
        if rc != 0:
            raise SSHError(f"Failed to install Python deps on remote: {out.strip()[:200]}")
        if log:
            log("Deps installed")

    def check_masscan(self):
        """Return (ok, message)."""
        rc, path = self._exec_rc("command -v masscan || which masscan")
        if rc != 0 or not path.strip():
            return False, "masscan not found on remote machine"
        return True, path.strip()

    # ── run scanner with streaming stdout ──

    def run_scanner(self, cmd, on_line=None, cancel_check=None):
        """Execute the scanner command remotely, streaming stdout line-by-line.

        - on_line(str): called for each stdout line (used to emit scan logs).
        - cancel_check() -> bool: polled between reads; if True the remote
          process is killed and SSHCancelled is raised.

        Returns the process exit code (0 = success).
        """
        scan_tag = f"ocs_{int(time.time())}_{os.getpid()}"
        pid_file = f"/tmp/{scan_tag}.pid"
        self._current_pid_file = pid_file
        # Wrap so we can capture the PID for cancellation, and cd into the work dir.
        wrapped = (
            f"cd {shlex.quote(self._remote_dir)} && "
            f"bash -c 'echo \\$$ > {pid_file}; exec {cmd}'"
        )
        transport = self.client.get_transport()
        chan = transport.open_session()
        chan.exec_command(wrapped)

        try:
            buf = ""
            while True:
                if chan.recv_ready():
                    chunk = chan.recv(8192).decode("utf-8", errors="replace")
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        if on_line:
                            on_line(line.rstrip("\r"))
                if chan.exit_status_ready() and not chan.recv_ready():
                    break
                if cancel_check and cancel_check():
                    self._kill_pid(pid_file)
                    raise SSHCancelled("Scan cancelled by user")
                time.sleep(0.05)

            # flush trailing buffer
            if buf.strip() and on_line:
                on_line(buf.rstrip("\r"))

            return chan.recv_exit_status()
        finally:
            chan.close()
            self._current_pid_file = None

    def _kill_pid(self, pid_file):
        """Kill the remote scanner process group by PID file."""
        self._exec_rc(
            f"kill -TERM $(cat {pid_file} 2>/dev/null) 2>/dev/null; "
            f"sleep 1; kill -KILL $(cat {pid_file} 2>/dev/null) 2>/dev/null; "
            f"rm -f {pid_file}",
            timeout=10,
        )

    # ── fetch results ──

    def fetch_file(self, remote_path, local_path):
        """Download a remote file (results.json) to a local path via SFTP."""
        sftp = self.client.open_sftp()
        try:
            sftp.get(remote_path, local_path)
        except FileNotFoundError:
            raise SSHError(f"Remote results file not found: {remote_path}")
        except IOError as e:
            raise SSHError(f"Failed to fetch {remote_path}: {e}")
        finally:
            sftp.close()


def build_scanner_cli(job, remote_output_dir, use_sudo=False):
    """Build the scanner.py command line for a remote ScanJob.

    Provider-based scans only — the CLI does not support single-IP mode.
    """
    parts = ["python3 scanner.py", "--raw", "--skip-ping"]

    providers = ",".join(job.providers) if job.providers else "all"
    parts.append(f"--providers {shlex.quote(providers)}")

    if job.ports:
        parts.append(f"--ports {shlex.quote(','.join(str(p) for p in job.ports))}")

    if job.llm_mode:
        parts.append("--llm-mode")

    if job.full_sweep:
        parts.append(f"--full-sweep {shlex.quote(job.full_sweep)}")

    if job.rate:
        parts.append(f"--rate {job.rate}")
    if job.parallel:
        parts.append(f"--parallel {job.parallel}")
    if job.workers:
        parts.append(f"--workers {job.workers}")
    if job.score_threshold:
        parts.append(f"--score {job.score_threshold}")

    if use_sudo:
        parts.append("--sudo")
    else:
        parts.append("--no-sudo")

    parts.append(f"--output {shlex.quote(remote_output_dir)}")

    return " ".join(parts)
