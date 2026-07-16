#!/usr/bin/env python3
"""
Fetch China Unicom residential/broadband IP prefixes (AS4837) from RIPE Stat
and merge into cloud_providers.json as a separate `china_unicom_residential`
provider so it can be scanned independently from `china_unicom_cloud` (AS9929).

Usage:
    python3 fetch_china_unicom_residential.py

This intentionally targets the consumer/broadband backbone (AS4837), NOT the
premium cloud ASN (AS9929) which is already covered by `china_unicom_cloud`.
"""

import json
import os
import time
import urllib.request
import ipaddress

API_BASE = "https://stat.ripe.net/data/announced-prefixes/data.json"

# China Unicom residential/broadband ASNs.
# AS4837 = CHINA169 Backbone (main consumer/broadband range, where residential IPs live).
# AS10099 = China Unicom Global (international transit/consumer).
# NOTE: AS9929 (CNCNET premium) is deliberately EXCLUDED here — it is already
# tracked under `china_unicom_cloud`.
PROVIDER_NAME = "china_unicom_residential"
PROVIDER = {
    "region": "cn",
    "asns": ["AS4837", "AS10099"],
    "description": "China Unicom CHINA169 residential/broadband backbone (AS4837 + AS10099). Excludes AS9929 cloud ranges already in china_unicom_cloud.",
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
    meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta["updated_description"] = (
        "Added china_unicom_residential (AS4837 + AS10099 consumer/broadband ranges) "
        "as a separate scannable provider distinct from china_unicom_cloud (AS9929)."
    )

    print(f"Existing providers: {len(providers)}")
    print(f"\nProcessing: {PROVIDER_NAME}")
    print(f"  {PROVIDER['description']}")

    all_v4: list[str] = []
    all_v6: list[str] = []

    for asn in PROVIDER["asns"]:
        v4, v6 = fetch_prefixes(asn)
        all_v4.extend(v4)
        all_v6.extend(v6)
        time.sleep(0.5)  # Be nice to RIPE Stat

    # Deduplicate
    all_v4 = sorted(set(all_v4))
    all_v6 = sorted(set(all_v6))

    est_hosts = estimate_hosts(all_v4)

    if PROVIDER_NAME in providers:
        existing_v4 = providers[PROVIDER_NAME].get("ipv4_prefixes", [])
        existing_v6 = providers[PROVIDER_NAME].get("ipv6_prefixes", [])
        all_v4 = merge_prefixes(existing_v4, all_v4)
        all_v6 = merge_prefixes(existing_v6, all_v6)
        est_hosts = estimate_hosts(all_v4)
        print(f"  Merged with existing: {len(all_v4)} IPv4, {len(all_v6)} IPv6")
    else:
        print(f"  Total: {len(all_v4)} IPv4, {len(all_v6)} IPv6, ~{est_hosts:,} hosts")

    providers[PROVIDER_NAME] = {
        "region": PROVIDER["region"],
        "asns": PROVIDER["asns"],
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
    print(f"Done! Wrote {PROVIDER_NAME} to {json_path}")
    v4 = len(all_v4)
    hosts = est_hosts
    print(f"  {PROVIDER_NAME:30s}  region=cn  v4={v4:5d}  hosts~{hosts:>12,}")


if __name__ == "__main__":
    main()
