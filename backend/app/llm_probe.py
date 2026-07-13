"""Robust LLM endpoint probing with multi-path fallback and model discovery.

Key improvements over v1:
- Model discovery first: tries to GET /v1/models or /api/tags before prompting
- Uses discovered model names instead of empty string
- Canary check accepts any non-empty response (not just exact "H3llo")
- Consistency check skipped for single-model endpoints (deterministic)
- Better timeout handling: 2s TCP + 5s HTTP per probe
- Detects embeddings, image, audio models from model ID patterns
"""
import re
import socket
import requests


# ── Model type patterns ──
EMBEDDING_PATTERNS = [
    r"text-embedding", r"embed", r"bge-", r"e5-", r"gte-", r"jina-embed",
    r"nomic-embed", r"mxbai-embed", r"instructor",
]
IMAGE_PATTERNS = [
    r"stable-diffusion", r"sd-", r"flux", r"dall-", r"xl-", r"realvis",
    r"photon-", r"animagine", r"meina", r"anything-", r"sdxl",
]
AUDIO_PATTERNS = [
    r"whisper", r"tts", r"bark", r"piper", r"coqui", r"xtts",
    r"speech", r"voice",
]
VIDEO_PATTERNS = [
    r"video", r"svd", r"animatediff", r"zeroscope",
]


def _classify_model_type(model_id: str) -> str:
    """Classify a model ID by its type based on name patterns."""
    if not model_id:
        return "chat"
    mid = str(model_id).lower()
    for p in EMBEDDING_PATTERNS:
        if re.search(p, mid):
            return "embeddings"
    for p in IMAGE_PATTERNS:
        if re.search(p, mid):
            return "image"
    for p in AUDIO_PATTERNS:
        if re.search(p, mid):
            return "audio"
    for p in VIDEO_PATTERNS:
        if re.search(p, mid):
            return "video"
    return "chat"


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}


def _tcp_connect(ip: str, port: int, timeout: float = 2.0) -> bool:
    """Fast TCP connect check. Returns True if port is open."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False


# ── Model discovery ──

def discover_models(base_url: str, timeout: float = 5) -> tuple[list[str], str]:
    """Discover available models from an endpoint.

    Returns: (model_ids, source) where source is the endpoint path that worked.
    """
    def _clean(val):
        """Ensure model ID is a non-empty string."""
        if val is None:
            return ""
        if isinstance(val, (dict, list)):
            return ""
        return str(val).strip()

    # Try 1: OpenAI /v1/models
    try:
        r = requests.get(f"{base_url}/v1/models", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            if isinstance(data, dict) and "data" in data:
                ids = [_clean(m.get("id", "")) for m in data["data"] if isinstance(m, dict)]
                ids = [i for i in ids if i]
                if ids:
                    return ids, "/v1/models"
    except Exception:
        pass

    # Try 2: Ollama /api/tags
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            if isinstance(data, dict) and "models" in data:
                ids = [_clean(m.get("name", m.get("model", ""))) for m in data["models"] if isinstance(m, dict)]
                ids = [i for i in ids if i]
                if ids:
                    return ids, "/api/tags"
    except Exception:
        pass

    # Try 3: Kobold /api/v1/model
    try:
        r = requests.get(f"{base_url}/api/v1/model", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            if isinstance(data, dict) and "result" in data:
                mid = _clean(data["result"])
                if mid:
                    return [mid], "/api/v1/model"
    except Exception:
        pass

    return [], ""


# ── Prompt probing with real model name ──

def _probe_chat_completions(base_url: str, prompt: str, model: str, timeout: float):
    try:
        r = requests.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 50,
                "stream": False,
            },
            timeout=timeout,
        )
        if r.status_code == 200:
            data = _safe_json(r)
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", choice.get("text", ""))
            if isinstance(msg, dict):
                return msg.get("content", "")
            return str(msg) if msg else None
    except Exception:
        pass
    return None


def _probe_ollama_chat(base_url: str, prompt: str, model: str, timeout: float):
    try:
        r = requests.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=timeout,
        )
        if r.status_code == 200:
            data = _safe_json(r)
            msg = data.get("message", {})
            if isinstance(msg, dict):
                return msg.get("content", "")
            return str(msg) if msg else None
    except Exception:
        pass
    return None


def _probe_ollama_generate(base_url: str, prompt: str, model: str, timeout: float):
    try:
        r = requests.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = _safe_json(r)
            return data.get("response") or None
    except Exception:
        pass
    return None


def _probe_kobold_generate(base_url: str, prompt: str, timeout: float):
    try:
        r = requests.post(
            f"{base_url}/api/v1/generate",
            json={"prompt": prompt, "max_length": 50},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = _safe_json(r)
            results = data.get("results", [{}])
            return results[0].get("text") or None
    except Exception:
        pass
    return None


def _probe_openai_completions(base_url: str, prompt: str, model: str, timeout: float):
    try:
        r = requests.post(
            f"{base_url}/v1/completions",
            json={"model": model, "prompt": prompt, "max_tokens": 50},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = _safe_json(r)
            choices = data.get("choices", [{}])
            return choices[0].get("text") or None
    except Exception:
        pass
    return None


def probe_with_model(base_url: str, prompt: str, model: str, timeout: float = 5):
    """Send a prompt using a specific model name. Tries multiple endpoint formats."""
    for probe_fn in [
        lambda: _probe_chat_completions(base_url, prompt, model, timeout),
        lambda: _probe_ollama_chat(base_url, prompt, model, timeout),
        lambda: _probe_ollama_generate(base_url, prompt, model, timeout),
        lambda: _probe_openai_completions(base_url, prompt, model, timeout),
        lambda: _probe_kobold_generate(base_url, prompt, timeout),
    ]:
        result = probe_fn()
        if result is not None:
            return result
    return None


# ── Shared verification logic ──

def verify_endpoint(ip: str, port: int, scheme: str = "http", timeout: float = 5):
    """Run improved honeypot detection on an LLM endpoint.

    Steps:
      1. TCP connect check (2s timeout)
      2. Discover models (GET /v1/models, /api/tags, /api/v1/model)
      3. If models found, pick first chat model (skip embeddings/image/audio)
      4. Send canary prompt with real model name
      5. Send math prompt with real model name
      6. Consistency check (same canary again) — skipped if only 1 model

    Returns:
        (status, details) where status is "legitimate" | "honeypot" | "unreachable"
    """
    base_url = f"{scheme}://{ip}:{port}"

    # 1. TCP pre-check
    if not _tcp_connect(ip, port, timeout=2.0):
        return "unreachable", {
            "error": "tcp_connect_failed",
            "models_found": [],
            "model_type": None,
            "responses": [],
        }

    # 2. Discover models
    model_ids, source = discover_models(base_url, timeout=timeout)

    if not model_ids:
        # No models discovered — try generic prompt anyway
        resp1 = probe_with_model(base_url, "reply only H3llo", "", timeout=timeout)
        if resp1 is None:
            return "unreachable", {
                "error": "no_models_and_no_response",
                "models_found": [],
                "model_type": None,
                "responses": [],
            }
        # Got a response without model discovery — likely a simple/non-OpenAI server
        canary_pass = bool(resp1.strip())
        resp2 = probe_with_model(base_url, "What is 7 + 5?", "", timeout=timeout)
        math_pass = resp2 is not None and "12" in str(resp2)
        resp3 = probe_with_model(base_url, "reply only H3llo", "", timeout=timeout)
        consistency_pass = resp3 is not None and resp1 != resp3

        if canary_pass and math_pass:
            status = "legitimate"
        else:
            status = "honeypot"

        return status, {
            "canary_pass": canary_pass,
            "math_pass": math_pass,
            "consistency_pass": consistency_pass,
            "models_found": [],
            "model_type": None,
            "model_used": "",
            "responses": [
                {"check": "canary", "prompt": "reply only H3llo", "response": resp1},
                {"check": "math", "prompt": "What is 7 + 5?", "response": resp2},
                {"check": "consistency", "prompt": "reply only H3llo", "response": resp3},
            ],
        }

    # 3. Classify models and pick a chat model
    model_ids = [mid for mid in model_ids if mid]
    if not model_ids:
        # Discovery returned only empty/null IDs — treat as no models
        resp1 = probe_with_model(base_url, "reply only H3llo", "", timeout=timeout)
        if resp1 is None:
            return "unreachable", {
                "error": "no_valid_models_and_no_response",
                "models_found": [],
                "model_type": None,
                "responses": [],
            }
        canary_pass = bool(str(resp1).strip())
        resp2 = probe_with_model(base_url, "What is 7 + 5?", "", timeout=timeout)
        math_pass = resp2 is not None and "12" in str(resp2)
        resp3 = probe_with_model(base_url, "reply only H3llo", "", timeout=timeout)
        consistency_pass = resp3 is not None and resp1 != resp3
        status = "legitimate" if canary_pass and math_pass else "honeypot"
        return status, {
            "canary_pass": canary_pass,
            "math_pass": math_pass,
            "consistency_pass": consistency_pass,
            "models_found": [],
            "model_type": None,
            "model_used": "",
            "responses": [
                {"check": "canary", "prompt": "reply only H3llo", "response": resp1},
                {"check": "math", "prompt": "What is 7 + 5?", "response": resp2},
                {"check": "consistency", "prompt": "reply only H3llo", "response": resp3},
            ],
        }

    model_types = {mid: _classify_model_type(mid) for mid in model_ids}
    chat_models = [mid for mid, t in model_types.items() if t == "chat"]

    if chat_models:
        test_model = chat_models[0]
        model_type = "chat"
    else:
        # No chat models — pick first available (embeddings/image/audio)
        test_model = model_ids[0]
        model_type = model_types[test_model]

    # 4. Run checks with real model name
    resp1 = probe_with_model(base_url, "reply only H3llo", test_model, timeout=timeout)
    if resp1 is None:
        return "unreachable", {
            "error": "prompt_probe_failed",
            "models_found": model_ids,
            "model_type": model_type,
            "model_used": test_model,
            "responses": [],
        }

    # Canary: any non-empty response is fine
    canary_pass = bool(str(resp1).strip())

    # Math check
    resp2 = probe_with_model(base_url, "What is 7 + 5?", test_model, timeout=timeout)
    math_pass = resp2 is not None and "12" in str(resp2)

    # Consistency: only check if we have >1 model (avoids deterministic false positives)
    if len(model_ids) > 1:
        resp3 = probe_with_model(base_url, "reply only H3llo", test_model, timeout=timeout)
        consistency_pass = resp3 is not None and resp1 != resp3
    else:
        resp3 = None
        consistency_pass = True  # Single model — can't test consistency fairly

    # Score: need canary + math to pass; consistency is a bonus
    if canary_pass and math_pass and consistency_pass:
        status = "legitimate"
    elif canary_pass and math_pass:
        # Passed 2/3 — mark as legitimate but note inconsistency
        status = "legitimate"
    else:
        status = "honeypot"

    return status, {
        "canary_pass": canary_pass,
        "math_pass": math_pass,
        "consistency_pass": consistency_pass,
        "models_found": model_ids,
        "model_type": model_type,
        "model_used": test_model,
        "model_discovery_source": source,
        "responses": [
            {"check": "canary", "prompt": "reply only H3llo", "response": resp1},
            {"check": "math", "prompt": "What is 7 + 5?", "response": resp2},
            {"check": "consistency", "prompt": "reply only H3llo", "response": resp3},
        ],
    }


# ── Model listing (for /matches/{id}/models) ──

def list_models_openai(base_url: str, timeout: float = 5):
    try:
        r = requests.get(f"{base_url}/v1/models", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            models = []
            for m in data.get("data", []):
                mid = m.get("id", "unknown")
                models.append({
                    "id": mid,
                    "name": mid,
                    "type": _classify_model_type(mid),
                })
            return models
    except Exception:
        pass
    return None


def list_models_ollama(base_url: str, timeout: float = 5):
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            models = []
            for m in data.get("models", []):
                mid = m.get("name", m.get("model", "unknown"))
                models.append({
                    "id": mid,
                    "name": mid,
                    "size": m.get("size"),
                    "parameter_size": m.get("parameter_size"),
                    "quantization_level": m.get("details", {}).get("quantization_level"),
                    "type": _classify_model_type(mid),
                })
            return models
    except Exception:
        pass
    return None


def list_models_kobold(base_url: str, timeout: float = 5):
    try:
        r = requests.get(f"{base_url}/api/v1/model", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            model_name = data.get("result", "unknown")
            return [{"id": model_name, "name": model_name, "type": "chat"}]
    except Exception:
        pass
    return None


MODEL_LISTERS = [
    list_models_openai,
    list_models_ollama,
    list_models_kobold,
]


def probe_models(base_url: str, timeout: float = 5):
    for lister in MODEL_LISTERS:
        result = lister(base_url, timeout)
        if result is not None:
            return result
    return []
