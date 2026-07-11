#!/usr/bin/env python3
"""
opencode_mdns.py — Discover opencode servers via mDNS/Bonjour.

Passively listens for mDNS service advertisements on the local network
and fingerprints any HTTP services that look like opencode.

Requirements (one of):
  - python-zeroconf:  pip install zeroconf
  - avahi-utils:       apt install avahi-utils

Usage:
  python3 opencode_mdns.py                          # Passive discovery (zeroconf)
  python3 opencode_mdns.py --avahi                   # Use avahi-browse instead
  python3 opencode_mdns.py --timeout 60               # Listen for 60 seconds
  python3 opencode_mdns.py --fingerprint              # Also fingerprint found services
"""

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# mDNS service types that opencode might advertise as
OPECODE_SERVICE_TYPES = [
    "_http._tcp.local.",
    "_https._tcp.local.",
    "_opencode._tcp.local.",
    "_opencode._http._tcp.local.",
]


def _is_opencode_name(name: str) -> bool:
    """Check if an mDNS service name suggests opencode."""
    name_lower = name.lower()
    for kw in ["opencode", "open-code", "code-server", "opencode-server"]:
        if kw in name_lower:
            return True
    return False


# Try importing zeroconf
try:
    from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    HAS_ZEROCONF = True

    class OpenCodeListener(ServiceListener):
        """Zeroconf listener that collects opencode-related services."""

        def __init__(self):
            self.found: list[dict] = []

        def add_service(self, zc: Zeroconf, type_: str, name: str):
            info = zc.get_service_info(type_, name)
            if info and info.addresses:
                ip = socket.inet_ntoa(info.addresses[0])
                port = info.port
                props = {k.decode(): v.decode() if isinstance(v, bytes) else str(v)
                         for k, v in info.properties.items()}
                entry = {
                    "name": name,
                    "type": type_,
                    "ip": ip,
                    "port": port,
                    "server": info.server.rstrip(".") if info.server else name,
                    "properties": props,
                }
                if _is_opencode_name(name) or info.server and _is_opencode_name(info.server):
                    entry["opencode_hint"] = "name_match"
                elif port in (4096, 3000) and type_.startswith("_http"):
                    entry["opencode_hint"] = "port_match"
                self.found.append(entry)
                print(f"  [mDNS] {name} → {ip}:{port} ({type_}){' ⚡ opencode?' if 'opencode_hint' in entry else ''}")

        def update_service(self, zc: Zeroconf, type_: str, name: str):
            pass

        def remove_service(self, zc: Zeroconf, type_: str, name: str):
            pass

except ImportError:
    HAS_ZEROCONF = False
    ServiceListener = None
    ServiceBrowser = None
    Zeroconf = None

try:
    from fingerprint import FingerprintEngine
    HAS_FINGERPRINT = True
except ImportError:
    HAS_FINGERPRINT = False


class OpenCodeListener(ServiceListener):
    """Zeroconf listener that collects opencode-related services."""

    def __init__(self):
        self.found: list[dict] = []

    def add_service(self, zc: Zeroconf, type_: str, name: str):
        info = zc.get_service_info(type_, name)
        if info and info.addresses:
            ip = socket.inet_ntoa(info.addresses[0])
            port = info.port
            props = {k.decode(): v.decode() if isinstance(v, bytes) else str(v)
                     for k, v in info.properties.items()}
            entry = {
                "name": name,
                "type": type_,
                "ip": ip,
                "port": port,
                "server": info.server.rstrip(".") if info.server else name,
                "properties": props,
            }
            if _is_opencode_name(name) or info.server and _is_opencode_name(info.server):
                entry["opencode_hint"] = "name_match"
            elif port in (4096, 3000) and type_.startswith("_http"):
                entry["opencode_hint"] = "port_match"
            self.found.append(entry)
            print(f"  [mDNS] {name} → {ip}:{port} ({type_}){' ⚡ opencode?' if 'opencode_hint' in entry else ''}")

    def update_service(self, zc: Zeroconf, type_: str, name: str):
        pass

    def remove_service(self, zc: Zeroconf, type_: str, name: str):
        pass


def discover_zeroconf(timeout: int = 30) -> list[dict]:
    """Use python-zeroconf to browse for mDNS services."""
    listener = OpenCodeListener()
    zc = Zeroconf()
    browsers = []

    for svc_type in OPECODE_SERVICE_TYPES:
        try:
            browser = ServiceBrowser(zc, svc_type, listener)
            browsers.append(browser)
            print(f"  Browsing {svc_type}")
        except Exception as e:
            print(f"  (skip {svc_type}: {e})", file=sys.stderr)

    print(f"\n  Listening for {timeout}s... (Ctrl+C to stop)")

    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n  Interrupted.")

    for browser in browsers:
        try:
            browser.cancel()
        except Exception:
            pass
    zc.close()

    return listener.found


def discover_avahi(timeout: int = 30) -> list[dict]:
    """Use avahi-browse to discover mDNS services."""
    found = []
    cmd = ["avahi-browse", "--all", "--terminate", "--resolve", "-t", "-p"]

    print(f"  Running avahi-browse for {timeout}s...")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            stdout, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, _ = proc.communicate()

        for line in stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split(";")
            if len(parts) < 8:
                continue
            # Format: =;eth0;IPv4;hostname;_http._tcp;local;opencode.local;192.168.1.5;4096;...
            iface, proto, name, svc_type, domain, hostname, addr, port_str = parts[1:9]
            try:
                port = int(port_str)
            except ValueError:
                continue
            entry = {
                "name": name,
                "type": f"{svc_type}.{domain}",
                "ip": addr,
                "port": port,
                "server": hostname.rstrip(".") if hostname else name,
                "properties": {},
            }
            if _is_opencode_name(name) or _is_opencode_name(hostname):
                entry["opencode_hint"] = "name_match"
            elif port in (4096, 3000) and "_http" in svc_type:
                entry["opencode_hint"] = "port_match"
            found.append(entry)
            print(f"  [mDNS] {name} → {addr}:{port} ({svc_type}){' ⚡ opencode?' if 'opencode_hint' in entry else ''}")

    except FileNotFoundError:
        print("ERROR: avahi-browse not found. Install: apt install avahi-utils", file=sys.stderr)
        sys.exit(1)

    return found


async def main():
    parser = argparse.ArgumentParser(
        description="opencode_mdns — Discover opencode servers via mDNS"
    )
    parser.add_argument("--timeout", "-t", type=int, default=30,
                        help="Listen timeout in seconds (default: 30)")
    parser.add_argument("--avahi", action="store_true",
                        help="Use avahi-browse instead of zeroconf")
    parser.add_argument("--fingerprint", "-f", action="store_true",
                        help="Fingerprint discovered services to confirm opencode")
    parser.add_argument("--json-out", action="store_true",
                        help="Output results as JSON")
    parser.add_argument("--output", "-o", default=None,
                        help="Save results to JSON file")

    args = parser.parse_args()

    # Discover
    if args.avahi:
        services = discover_avahi(timeout=args.timeout)
    elif HAS_ZEROCONF:
        services = discover_zeroconf(timeout=args.timeout)
    else:
        print("ERROR: Neither python-zeroconf nor --avahi available.", file=sys.stderr)
        print("  Install: pip install zeroconf   OR   apt install avahi-utils", file=sys.stderr)
        sys.exit(1)

    if not services:
        print("\n  No mDNS services found.")
        return

    print(f"\n  Found {len(services)} service(s)")

    # Fingerprint if requested
    matches = []
    if args.fingerprint and HAS_FINGERPRINT and services:
        pairs = [(s["ip"], s["port"]) for s in services if "opencode_hint" in s]
        if not pairs:
            pairs = [(s["ip"], s["port"]) for s in services]

        print(f"\n  Fingerprinting {len(pairs)} target(s)...")
        engine = FingerprintEngine(concurrency=50, timeout=3.0, score_threshold=5)
        matches = await engine.probe_candidates(pairs)

        if matches:
            print(f"  Confirmed {len(matches)} opencode server(s):")
            for m in matches:
                ver = m.get("details", {}).get("health", {}).get("version", "?")
                print(f"    {m['ip']}:{m['port']}  score={m['score']}  v{ver}  {','.join(m['methods_hit'])}")
        else:
            print("  No opencode servers confirmed.")

    # Output
    output = {
        "discovered_services": services,
        "matches": matches,
    }

    if args.json_out:
        print(json.dumps(output, indent=2))

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\n  Saved to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
