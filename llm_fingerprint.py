import asyncio
import json
import logging
import ssl
from typing import Optional

import aiohttp
from aiohttp import ClientTimeout, TCPConnector

logger = logging.getLogger(__name__)

# Known LLM service ports — weak signal only
LLM_PORTS = {
    11434: ("ollama", 1),
    11435: ("ollama", 1),
    1234:  ("lm_studio", 1),
    5000:  ("generic", 1),
    5001:  ("generic", 1),
    7860:  ("generic", 1),
    8000:  ("generic", 1),
    8080:  ("generic", 1),
    8888:  ("generic", 1),
    3001:  ("generic", 1),
}

DETECTION_METHODS = {
    "ollama_tags":    6,
    "ollama_version": 3,
    "openai_models":  5,
    "openai_model_id": 2,
    "llamacpp_props": 4,
    "kobold_model":   4,
    "anythingllm":    3,
    "openwebui":      3,
    "port_hint":      1,
}

MAX_SCORE = sum(DETECTION_METHODS.values())


def _classify_service(methods_hit: list[str], details: dict) -> str:
    """Determine the most likely service from matched methods."""
    if "ollama_tags" in methods_hit or "ollama_version" in methods_hit:
        return "ollama"
    if "llamacpp_props" in methods_hit:
        return "llamacpp"
    if "kobold_model" in methods_hit:
        return "kobold"
    if "anythingllm" in methods_hit:
        return "anythingllm"
    if "openwebui" in methods_hit:
        return "openwebui"
    if "openai_models" in methods_hit:
        # Could be vLLM, LM Studio, LocalAI — check port hint
        hint = details.get("port_hint", "")
        if "lm_studio" in hint:
            return "lm_studio"
        return "vllm_compat"
    return "unknown"


async def probe_host(
    ip: str,
    port: int,
    timeout: float = 3.0,
    score_threshold: int = 5,
) -> Optional[dict]:
    scheme = "https" if port in (443, 8443) else "http"
    base = f"{scheme}://{ip}:{port}"

    result = {
        "ip": ip,
        "port": port,
        "score": 0,
        "methods_hit": [],
        "details": {},
        "models": [],
        "version": None,
        "service": "unknown",
    }

    # Port hint
    if port in LLM_PORTS:
        svc, weight = LLM_PORTS[port]
        result["score"] += weight
        result["methods_hit"].append("port_hint")
        result["details"]["port_hint"] = f"{svc}_{port}"

    tls_ctx = ssl.create_default_context()
    tls_ctx.check_hostname = False
    tls_ctx.verify_mode = ssl.CERT_NONE

    connector = TCPConnector(ssl=tls_ctx)
    session_timeout = ClientTimeout(total=timeout, connect=2.0)

    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=session_timeout,
            headers={"User-Agent": "llm-scanner/1.0"},
        ) as s:

            # ── Ollama: GET /api/tags ─────────────────────────────────────
            try:
                async with s.get(f"{base}/api/tags", allow_redirects=False) as r:
                    if r.status == 200:
                        body = await r.json(content_type=None)
                        if isinstance(body, dict) and "models" in body:
                            result["score"] += DETECTION_METHODS["ollama_tags"]
                            result["methods_hit"].append("ollama_tags")
                            models = [m.get("name", m.get("model", "")) for m in body["models"] if isinstance(m, dict)]
                            result["models"] = [m for m in models if m]
                            result["details"]["ollama_tags"] = {"model_count": len(body["models"])}
            except (asyncio.TimeoutError, aiohttp.ClientError, json.JSONDecodeError, OSError):
                pass

            # ── Ollama: GET /api/version ──────────────────────────────────
            try:
                async with s.get(f"{base}/api/version", allow_redirects=False) as r:
                    if r.status == 200:
                        body = await r.json(content_type=None)
                        if isinstance(body, dict) and "version" in body:
                            result["score"] += DETECTION_METHODS["ollama_version"]
                            result["methods_hit"].append("ollama_version")
                            result["version"] = body["version"]
                            result["details"]["ollama_version"] = body["version"]
            except (asyncio.TimeoutError, aiohttp.ClientError, json.JSONDecodeError, OSError):
                pass

            # ── OpenAI-compat: GET /v1/models ─────────────────────────────
            try:
                async with s.get(f"{base}/v1/models", allow_redirects=False) as r:
                    if r.status == 200:
                        body = await r.json(content_type=None)
                        if isinstance(body, dict) and body.get("object") == "list" and "data" in body:
                            result["score"] += DETECTION_METHODS["openai_models"]
                            result["methods_hit"].append("openai_models")
                            data = body["data"]
                            result["details"]["openai_models"] = {"model_count": len(data)}
                            # Extract model ids
                            ids = [m.get("id", "") for m in data if isinstance(m, dict)]
                            ids = [i for i in ids if i]
                            if ids:
                                result["score"] += DETECTION_METHODS["openai_model_id"]
                                result["methods_hit"].append("openai_model_id")
                                result["details"]["openai_model_id"] = ids[:5]
                                if not result["models"]:
                                    result["models"] = ids
            except (asyncio.TimeoutError, aiohttp.ClientError, json.JSONDecodeError, OSError):
                pass

            # ── llama.cpp: GET /props ─────────────────────────────────────
            try:
                async with s.get(f"{base}/props", allow_redirects=False) as r:
                    if r.status == 200:
                        body = await r.json(content_type=None)
                        if isinstance(body, dict) and "total_slots" in body:
                            result["score"] += DETECTION_METHODS["llamacpp_props"]
                            result["methods_hit"].append("llamacpp_props")
                            result["details"]["llamacpp_props"] = {
                                "total_slots": body.get("total_slots"),
                                "model_path": body.get("model_path", ""),
                            }
                            if not result["version"]:
                                result["version"] = body.get("version", None)
            except (asyncio.TimeoutError, aiohttp.ClientError, json.JSONDecodeError, OSError):
                pass

            # ── Kobold.cpp: GET /api/v1/model ─────────────────────────────
            try:
                async with s.get(f"{base}/api/v1/model", allow_redirects=False) as r:
                    if r.status == 200:
                        body = await r.json(content_type=None)
                        if isinstance(body, dict) and "result" in body:
                            result["score"] += DETECTION_METHODS["kobold_model"]
                            result["methods_hit"].append("kobold_model")
                            result["details"]["kobold_model"] = body["result"]
                            if not result["models"]:
                                result["models"] = [body["result"]]
            except (asyncio.TimeoutError, aiohttp.ClientError, json.JSONDecodeError, OSError):
                pass

            # ── AnythingLLM: GET /api/ping ────────────────────────────────
            try:
                async with s.get(f"{base}/api/ping", allow_redirects=False) as r:
                    if r.status == 200:
                        body = await r.json(content_type=None)
                        if isinstance(body, dict) and body.get("online") is True:
                            result["score"] += DETECTION_METHODS["anythingllm"]
                            result["methods_hit"].append("anythingllm")
                            result["details"]["anythingllm"] = True
            except (asyncio.TimeoutError, aiohttp.ClientError, json.JSONDecodeError, OSError):
                pass

            # ── Open WebUI: GET /api/version ──────────────────────────────
            # Only check if ollama_version didn't already fire (same endpoint shape)
            if "ollama_version" not in result["methods_hit"]:
                try:
                    async with s.get(f"{base}/api/version", allow_redirects=False) as r:
                        if r.status == 200:
                            body = await r.json(content_type=None)
                            # Open WebUI returns {"version":"x.y.z"} but NOT ollama model list
                            if isinstance(body, dict) and "version" in body and "ollama_tags" not in result["methods_hit"]:
                                result["score"] += DETECTION_METHODS["openwebui"]
                                result["methods_hit"].append("openwebui")
                                result["version"] = body["version"]
                                result["details"]["openwebui_version"] = body["version"]
                except (asyncio.TimeoutError, aiohttp.ClientError, json.JSONDecodeError, OSError):
                    pass

    except Exception:
        pass

    if result["score"] < score_threshold:
        return None

    result["service"] = _classify_service(result["methods_hit"], result["details"])
    result["confidence"] = min(100, int((result["score"] / (MAX_SCORE * 0.55)) * 100))
    return result


class LLMFingerprintEngine:
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
        matches = []
        scanned = 0

        async def _probe_one(ip: str, port: int):
            nonlocal scanned
            async with self.semaphore:
                result = await probe_host(ip, port, timeout=self.timeout, score_threshold=self.score_threshold)
                scanned += 1
                if scanned % 1000 == 0:
                    logger.info(f"  LLM fingerprint: {scanned}/{len(candidates)} ({len(matches)} matches)")
                if result:
                    matches.append(result)
                    logger.info(f"  MATCH: {ip}:{port} service={result['service']} score={result['score']} models={result['models'][:3]}")
                    if reporter:
                        reporter.add_match(result)
                if scanned % 5000 == 0 and reporter:
                    reporter.save_matches()

        tasks = [asyncio.create_task(_probe_one(ip, port)) for ip, port in candidates]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(f"LLM fingerprint complete: {len(matches)} matches from {len(candidates):,} candidates")
        return matches
