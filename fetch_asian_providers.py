#!/usr/bin/env python3
"""
Fetch cloud provider IP prefixes from RIPE Stat API and merge into cloud_providers.json.

Usage:
    python3 fetch_asian_providers.py

This script fetches announced prefixes for each provider's ASN(s),
separates IPv4 and IPv6, and merges them into cloud_providers.json.
"""

import json
import os
import sys
import time
import urllib.request
import ipaddress

API_BASE = "https://stat.ripe.net/data/announced-prefixes/data.json"

PROVIDERS = {
    "ucloud": {
        "region": "cn",
        "asns": ["AS55933", "AS135388"],
        "description": "UCloud Information Technology",
    },
    "kingsoft_cloud": {
        "region": "cn",
        "asns": ["AS134357"],
        "description": "Kingsoft Cloud Holdings",
    },
    "volcengine": {
        "region": "cn",
        "asns": ["AS137338", "AS145609", "AS147053"],
        "description": "Volcengine (ByteDance Cloud)",
    },
    "jd_cloud": {
        "region": "cn",
        "asns": ["AS136933", "AS135550"],
        "description": "JD Cloud (JD.com)",
    },
    "china_telecom_cloud": {
        "region": "cn",
        "asns": ["AS4809"],
        "description": "China Telecom CN2 Premium / Cloud (AS4809 only, not residential AS4134)",
    },
    "china_unicom_cloud": {
        "region": "cn",
        "asns": ["AS9929"],
        "description": "China Unicom CNCNET Premium / Cloud (AS9929 only, not residential AS4837)",
    },
    "china_mobile_cloud": {
        "region": "cn",
        "asns": ["AS58453"],
        "description": "China Mobile International / Cloud (AS58453 only)",
    },
    "naver_cloud": {
        "region": "kr",
        "asns": ["AS23576"],
        "description": "Naver Cloud (Korea)",
    },
    "sakura_internet": {
        "region": "jp",
        "asns": ["AS9371"],
        "description": "Sakura Internet (Japan)",
    },
    "kt_cloud": {
        "region": "kr",
        "asns": ["AS9318"],
        "description": "KT Corporation (Korea)",
    },
}


def fetch_prefixes(asn: str) -> tuple[list[str], list[str]]:
    """Fetch announced prefixes for an ASN from RIPE Stat.
    Returns (ipv4_prefixes, ipv6_prefixes).
    """
    url = f"{API_BASE}?resource={asn}&family=0"
    print(f"  Fetching {asn}...", end=" ", flush=True)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cloud-prefix-fetcher/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"FAILED: {e}")
        return [], []

    prefixes = data.get("data", {}).get("prefixes", [])
    v4 = []
    v6 = []

    for p in prefixes:
        prefix = p.get("prefix", "")
        if not prefix:
            continue
        try:
            net = ipaddress.ip_network(prefix, strict=False)
            if net.version == 4:
                v4.append(str(net))
            else:
                v6.append(str(net))
        except ValueError:
            continue

    print(f"{len(v4)} IPv4, {len(v6)} IPv6")
    return v4, v6


def merge_prefixes(existing: list[str], new: list[str]) -> list[str]:
    """Merge two prefix lists, deduplicate, and sort."""
    combined = list(set(existing + new))
    combined.sort()
    return combined


def estimate_hosts(prefixes: list[str]) -> int:
    """Estimate total IPv4 hosts from prefix list."""
    total = 0
    for p in prefixes:
        try:
            net = ipaddress.ip_network(p, strict=False)
            if net.version == 4:
                total += net.num_addresses
        except ValueError:
            continue
    return total


def main():
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloud_providers.json")

    print("Loading existing cloud_providers.json...")
    with open(json_path) as f:
        providers = json.load(f)

    meta = providers.pop("$meta", {})
    # Update meta
    meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta["updated_description"] = "Added 10 Asian cloud providers (UCloud, Kingsoft, Volcengine, JD Cloud, China Telecom/Unicom/Mobile Cloud, Naver, Sakura, KT)"

    print(f"Existing providers: {len(providers)}")

    for name, info in PROVIDERS.items():
        print(f"\nProcessing: {name} ({info['description']})")

        all_v4 = []
        all_v6 = []

        for asn in info["asns"]:
            v4, v6 = fetch_prefixes(asn)
            all_v4.extend(v4)
            all_v6.extend(v6)
            time.sleep(0.5)  # Be nice to RIPE Stat

        # Deduplicate
        all_v4 = list(set(all_v4))
        all_v6 = list(set(all_v6))
        all_v4.sort()
        all_v6.sort()

        est_hosts = estimate_hosts(all_v4)

        if name in providers:
            # Merge with existing
            existing_v4 = providers[name].get("ipv4_prefixes", [])
            existing_v6 = providers[name].get("ipv6_prefixes", [])
            all_v4 = merge_prefixes(existing_v4, all_v4)
            all_v6 = merge_prefixes(existing_v6, all_v6)
            est_hosts = estimate_hosts(all_v4)
            print(f"  Merged with existing: {len(all_v4)} IPv4, {len(all_v6)} IPv6")
        else:
            print(f"  Total: {len(all_v4)} IPv4, {len(all_v6)} IPv6, ~{est_hosts:,} hosts")

        providers[name] = {
            "region": info["region"],
            "asns": info["asns"],
            "prefix_count": len(all_v4),
            "estimated_ipv4_hosts": est_hosts,
            "ipv4_prefixes": all_v4,
            "ipv6_prefixes": all_v6,
        }

    # Write back
    result = {"$meta": meta}
    result.update(providers)

    with open(json_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Done! Wrote {len(providers)} providers to {json_path}")
    print(f"\nProvider summary:")
    for name, info in sorted(providers.items()):
        v4 = len(info.get("ipv4_prefixes", []))
        v6 = len(info.get("ipv6_prefixes", []))
        hosts = info.get("estimated_ipv4_hosts", 0)
        region = info.get("region", "?")
        print(f"  {name:30s}  region={region:3s}  v4={v4:5d}  v6={v6:5d}  hosts~{hosts:>12,}")


if __name__ == "__main__":
    main()
