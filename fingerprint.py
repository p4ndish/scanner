import asyncio
import json
import logging
import ssl
from typing import Optional

import aiohttp
from aiohttp import ClientTimeout, TCPConnector

logger = logging.getLogger(__name__)


DETECTION_METHODS = {
    "health": 5,
    "doc": 4,
    "path": 4,
    "doc_title": 3,
    "auth_realm": 2,
    "error_shape": 2,
    "port_hint": 1,
}

# Known opencode serving ports (weighted hints)
OPENCODE_PORTS = {4096: 1, 3000: 1, 8080: 0}


async def probe_host(
    ip: str,
    port: int,
    session: aiohttp.ClientSession,
    timeout: float = 3.0,
    methods: Optional[list[str]] = None,
    score_threshold: int = 5,
) -> Optional[dict]:
    """
    Probe a single IP:port and run all fingerprint checks.
    Returns None if no match, or a dict with match details.
    """
    if methods is None:
        methods = list(DETECTION_METHODS.keys())

    scheme = "https" if port in (443, 8443) else "http"
    base_url = f"{scheme}://{ip}:{port}"

    results = {"ip": ip, "port": port, "scheme": scheme, "methods_hit": [], "details": {}, "score": 0}

    # Port hint
    if "port_hint" in methods:
        hint = OPENCODE_PORTS.get(port, 0)
        if hint:
            results["details"]["port_hint"] = f"known_opencode_port_{port}"
            results["score"] += hint
            results["methods_hit"].append("port_hint")

    tls_context = ssl.create_default_context()
    tls_context.check_hostname = False
    tls_context.verify_mode = ssl.CERT_NONE

    connector = TCPConnector(ssl=tls_context)

    async with aiohttp.ClientSession(
        connector=connector,
        timeout=ClientTimeout(total=timeout, connect=2.0),
        headers={"User-Agent": "opencode-scanner/1.0"},
    ) as probe_session:

        # Check 1: /global/health
        if "health" in methods:
            try:
                async with probe_session.get(f"{base_url}/global/health", allow_redirects=False) as resp:
                    if resp.status == 200:
                        try:
                            body = await resp.json()
                            if isinstance(body, dict) and body.get("healthy") is True and "version" in body:
                                results["details"]["health"] = body
                                results["score"] += DETECTION_METHODS["health"]
                                results["methods_hit"].append("health")
                        except (json.JSONDecodeError, aiohttp.ContentTypeError):
                            pass
                    elif resp.status in (401, 403):
                        # Check for auth realm
                        if "auth_realm" in methods:
                            realm = resp.headers.get("WWW-Authenticate", "")
                            if "opencode" in realm.lower():
                                results["details"]["auth_realm"] = realm
                                results["score"] += DETECTION_METHODS["auth_realm"]
                                results["methods_hit"].append("auth_realm")
                        # Check error shape
                        if "error_shape" in methods:
                            try:
                                body = await resp.json()
                                if isinstance(body, dict) and ("error" in body or "message" in body):
                                    results["details"]["error_shape"] = body
                                    results["score"] += DETECTION_METHODS["error_shape"]
                                    results["methods_hit"].append("error_shape")
                            except:
                                pass
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
                pass

        # Check 2: /doc endpoint
        if "doc" in methods or "doc_title" in methods:
            try:
                async with probe_session.get(f"{base_url}/doc", allow_redirects=False) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        text_lower = text.lower()

                        # Check for OpenAPI spec markers
                        openapi_markers = [
                            '"openapi"',
                            "openapi 3.",
                            "openapi",
                            "swagger",
                            "operationId",
                        ]
                        has_openapi = any(m in text_lower for m in openapi_markers)
                        has_opencode_ops = any(
                            op in text for op in [
                                "health.get",
                                "health_retrieve",
                                "project.list",
                                "config.get",
                                "path.get",
                            ]
                        )

                        if "doc" in methods and (has_openapi or has_opencode_ops):
                            results["details"]["doc"] = {
                                "has_openapi": has_openapi,
                                "has_opencode_ops": has_opencode_ops,
                            }
                            results["score"] += DETECTION_METHODS["doc"]
                            results["methods_hit"].append("doc")

                        if "doc_title" in methods:
                            # Check for "OpenCode" in title/info
                            title_markers = ["opencode", '"title"', "open code"]
                            if any(m in text_lower for m in title_markers):
                                results["details"]["doc_title"] = True
                                results["score"] += DETECTION_METHODS["doc_title"]
                                results["methods_hit"].append("doc_title")
                    elif resp.status in (401, 403):
                        if "auth_realm" in methods and "auth_realm" not in results["methods_hit"]:
                            realm = resp.headers.get("WWW-Authenticate", "")
                            if "opencode" in realm.lower():
                                results["details"]["auth_realm"] = realm
                                results["score"] += DETECTION_METHODS["auth_realm"]
                                results["methods_hit"].append("auth_realm")
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
                pass

        # Check 3: /path endpoint
        if "path" in methods:
            try:
                async with probe_session.get(f"{base_url}/path", allow_redirects=False) as resp:
                    if resp.status == 200:
                        try:
                            body = await resp.json()
                            # opencode /path returns: home, state, config, worktree, directory
                            expected_keys = {"home", "state", "config", "worktree", "directory"}
                            if isinstance(body, dict) and len(body) > 0:
                                key_overlap = len(expected_keys & set(body.keys()))
                                if key_overlap >= 3:
                                    results["details"]["path"] = body
                                    results["score"] += DETECTION_METHODS["path"]
                                    results["methods_hit"].append("path")
                        except (json.JSONDecodeError, aiohttp.ContentTypeError):
                            pass
                    elif resp.status in (401, 403):
                        if "auth_realm" in methods and "auth_realm" not in results["methods_hit"]:
                            realm = resp.headers.get("WWW-Authenticate", "")
                            if "opencode" in realm.lower():
                                results["details"]["auth_realm"] = realm
                                results["score"] += DETECTION_METHODS["auth_realm"]
                                results["methods_hit"].append("auth_realm")
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
                pass

        # Check 4: /config endpoint
        if results["score"] < score_threshold:
            try:
                async with probe_session.get(f"{base_url}/config", allow_redirects=False) as resp:
                    if resp.status == 200:
                        try:
                            body = await resp.json()
                            if isinstance(body, dict):
                                # opencode config has specific shape
                                config_keys = {"theme", "model", "provider", "mode", "tools", "permissions", "providers", "mcp"}
                                overlap = len(config_keys & set(str(k).lower() for k in body.keys()))
                                if overlap >= 1:
                                    results["details"]["config"] = {"key_overlap": overlap}
                                    results["score"] += 1
                                    results["methods_hit"].append("config")
                        except (json.JSONDecodeError, aiohttp.ContentTypeError):
                            pass
                    elif resp.status in (401, 403) and "auth_realm" not in results["methods_hit"]:
                        realm = resp.headers.get("WWW-Authenticate", "")
                        if "opencode" in realm.lower():
                            results["details"]["auth_realm"] = realm
                            results["score"] += DETECTION_METHODS["auth_realm"]
                            results["methods_hit"].append("auth_realm")
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
                pass

    # Final check: if we have auth_realm AND error_shape but no health/doc/path/config
    # this might still be opencode behind auth
    if results["score"] < score_threshold:
        return None

    results["confidence"] = _calculate_confidence(results["score"])
    return results


def _calculate_confidence(score: int) -> int:
    """Convert raw score to confidence percentage."""
    max_score = sum(DETECTION_METHODS.values())
    return min(100, int((score / (max_score * 0.6)) * 100))


class FingerprintEngine:
    def __init__(self, concurrency: int = 500, timeout: float = 3.0, score_threshold: int = 5):
        self.concurrency = concurrency
        self.timeout = timeout
        self.score_threshold = score_threshold
        self.semaphore = asyncio.Semaphore(concurrency)

    async def probe_candidates(
        self,
        candidates: list[tuple[str, int]],
        reporter=None,
    ) -> list[dict]:
        """
        Given a list of (ip, port) candidates, run fingerprint checks concurrently.
        Returns list of confirmed opencode matches.
        """
        logger.info(
            f"Fingerprint: probing {len(candidates):,} candidates with "
            f"{self.concurrency} concurrent workers"
        )

        matches = []
        scanned = 0

        async def _probe_one(ip: str, port: int):
            nonlocal scanned
            async with self.semaphore:
                result = await probe_host(
                    ip, port, None,
                    timeout=self.timeout,
                    score_threshold=self.score_threshold,
                )
                scanned += 1
                if scanned % 1000 == 0:
                    logger.info(f"  Fingerprint progress: {scanned}/{len(candidates)} ({len(matches)} matches)")
                if result:
                    matches.append(result)
                    logger.info(f"  MATCH: {ip}:{port} score={result['score']} methods={result['methods_hit']}")
                    if reporter:
                        reporter.add_match(result)
                if scanned % 5000 == 0 and reporter:
                    reporter.save_matches()

        tasks = [asyncio.create_task(_probe_one(ip, port)) for ip, port in candidates]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(f"Fingerprint complete: {len(matches)} matches from {len(candidates):,} candidates")
        return matches
