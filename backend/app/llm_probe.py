"""Robust LLM endpoint probing with multi-path fallback and model discovery.

v3 improvements:
- max_tokens 50 → 200 (reasoning models need more tokens)
- Math prompt: "What is 7+5? Reply with only the number."
- Also checks "twelve" (word form) in math response
- Model-type-specific verification:
  - chat: 3-check (canary + math + consistency)
  - embeddings: POST /v1/embeddings, check for vector response
  - image: POST /v1/images/generations, check for image data
  - audio: POST /v1/audio/speech, check for audio content-type
"""
import re
import socket
import threading
import requests


MAX_TOKENS = 200

# ── Proxy pool (round-robin, thread-safe) ──
# Set by verify_matches_task when "use proxy" is enabled. Each request picks the
# next proxy in rotation, distributing load across the pool.
_PROXY_POOL = None  # list[str] of proxy URLs, or None
_proxy_idx = 0
_proxy_lock = threading.Lock()


def set_proxy_pool(proxy_urls):
    """Activate a proxy pool. proxy_urls: list of full proxy URL strings, or None/[] to disable."""
    global _PROXY_POOL, _proxy_idx
    _PROXY_POOL = list(proxy_urls) if proxy_urls else None
    _proxy_idx = 0


def clear_proxy_pool():
    global _PROXY_POOL
    _PROXY_POOL = None


def proxy_pool_active():
    return bool(_PROXY_POOL)


def _next_proxies():
    """Return a requests-style proxies dict, round-robining across the pool.
    Returns None when no pool is active (requests then connects directly)."""
    global _proxy_idx
    if not _PROXY_POOL:
        return None
    with _proxy_lock:
        url = _PROXY_POOL[_proxy_idx % len(_PROXY_POOL)]
        _proxy_idx += 1
    return {"http": url, "https": url}


# Bound originals so wrapper call sites can be swapped wholesale below.
_GET = requests.get
_POST = requests.post


def _rget(url, **kwargs):
    kwargs.setdefault("proxies", _next_proxies())
    return _GET(url, **kwargs)


def _rpost(url, **kwargs):
    kwargs.setdefault("proxies", _next_proxies())
    return _POST(url, **kwargs)

# ── Model type patterns (checked in order: embeddings, image, audio, video,
#    then vision → treated as chat/multimodal; else chat) ──
EMBEDDING_PATTERNS = [
    r"text-embedding", r"embed", r"bge-", r"e5-", r"gte-", r"jina-embed",
    r"nomic-embed", r"mxbai-embed", r"instructor", r"reranker", r"rerank",
]
IMAGE_PATTERNS = [
    r"stable-diffusion", r"stable.?diffusion", r"\bsd[-_]?[0-9x]", r"sdxl", r"sd3",
    r"flux", r"dall-?e", r"realvis", r"photon-", r"animagine", r"meina",
    r"anything-", r"juggernaut", r"dreamshaper", r"playground-?v", r"kandinsky",
    r"pixart", r"midjourney", r"\bimagen\b",
]
AUDIO_PATTERNS = [
    r"whisper", r"\btts\b", r"bark", r"piper", r"coqui", r"xtts", r"kokoro",
    r"speecht5", r"vits", r"melo", r"styletts", r"parler", r"musicgen",
    r"audiogen", r"speech", r"voice", r"\basr\b",
]
VIDEO_PATTERNS = [
    r"\bvideo\b", r"\bsvd\b", r"animatediff", r"zeroscope", r"cogvideo",
    r"mochi", r"hunyuan-?video", r"ltx-?video", r"wan2",
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
        if val is None:
            return ""
        if isinstance(val, (dict, list)):
            return ""
        return str(val).strip()

    try:
        r = _rget(f"{base_url}/v1/models", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            if isinstance(data, dict) and "data" in data:
                ids = [_clean(m.get("id", "")) for m in data["data"] if isinstance(m, dict)]
                ids = [i for i in ids if i]
                if ids:
                    return ids, "/v1/models"
    except Exception:
        pass

    try:
        r = _rget(f"{base_url}/api/tags", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            if isinstance(data, dict) and "models" in data:
                ids = [_clean(m.get("name", m.get("model", ""))) for m in data["models"] if isinstance(m, dict)]
                ids = [i for i in ids if i]
                if ids:
                    return ids, "/api/tags"
    except Exception:
        pass

    try:
        r = _rget(f"{base_url}/api/v1/model", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            if isinstance(data, dict) and "result" in data:
                mid = _clean(data["result"])
                if mid:
                    return [mid], "/api/v1/model"
    except Exception:
        pass

    return [], ""


# ── Chat prompt probing ──

def _probe_chat_completions(base_url: str, prompt: str, model: str, timeout: float):
    try:
        r = _rpost(
            f"{base_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": MAX_TOKENS,
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
        r = _rpost(
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
        r = _rpost(
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
        r = _rpost(
            f"{base_url}/api/v1/generate",
            json={"prompt": prompt, "max_length": MAX_TOKENS},
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
        r = _rpost(
            f"{base_url}/v1/completions",
            json={"model": model, "prompt": prompt, "max_tokens": MAX_TOKENS},
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
    """Send a chat prompt using a specific model name. Tries multiple endpoint formats."""
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


# ── Embeddings verification ──

def verify_embeddings(base_url: str, model: str, timeout: float = 10):
    """Test an embeddings endpoint by sending a simple embedding request."""
    try:
        r = _rpost(
            f"{base_url}/v1/embeddings",
            json={"input": "test", "model": model},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = _safe_json(r)
            embeddings = data.get("data", [])
            if embeddings and isinstance(embeddings[0], dict):
                vec = embeddings[0].get("embedding", [])
                if isinstance(vec, list) and len(vec) > 0:
                    return True, {"response_length": len(vec)}
        return False, {"status": r.status_code, "body": r.text[:200]}
    except Exception as e:
        return False, {"error": str(e)}


# ── Image generation verification ──

def verify_image(base_url: str, model: str, timeout: float = 30):
    """Test an image generation endpoint."""
    try:
        r = _rpost(
            f"{base_url}/v1/images/generations",
            json={"prompt": "a circle", "model": model, "n": 1, "size": "256x256", "response_format": "b64_json"},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = _safe_json(r)
            images = data.get("data", [])
            if images and isinstance(images[0], dict):
                if images[0].get("b64_json") or images[0].get("url"):
                    return True, {"image_generated": True}
        return False, {"status": r.status_code, "body": r.text[:200]}
    except Exception as e:
        return False, {"error": str(e)}


# ── Audio/TTS verification ──

def verify_audio(base_url: str, model: str, timeout: float = 15):
    """Test a TTS endpoint."""
    try:
        r = _rpost(
            f"{base_url}/v1/audio/speech",
            json={"input": "hello", "model": model, "voice": "alloy"},
            timeout=timeout,
        )
        if r.status_code == 200:
            content_type = r.headers.get("content-type", "")
            if "audio" in content_type or len(r.content) > 100:
                return True, {"content_type": content_type, "size_bytes": len(r.content)}
        return False, {"status": r.status_code, "content_type": r.headers.get("content-type", "")}
    except Exception as e:
        return False, {"error": str(e)}


# ── Shared verification logic ──

MATH_PROMPT = "What is 7+5? Reply with only the number."
CANARY_PROMPT = "reply only H3llo"


def _check_math_answer(resp):
    """Check if a math response contains the correct answer (12 or twelve)."""
    if resp is None:
        return False
    text = str(resp).lower()
    return "12" in text or "twelve" in text


def _verify_chat(base_url: str, model_ids: list, source: str, test_model: str, timeout: float):
    """Run 3-check honeypot detection on a chat model."""
    resp1 = probe_with_model(base_url, CANARY_PROMPT, test_model, timeout=timeout)
    if resp1 is None:
        return "unreachable", {
            "error": "prompt_probe_failed",
            "models_found": model_ids,
            "model_type": "chat",
            "model_used": test_model,
            "responses": [],
        }

    canary_pass = bool(str(resp1).strip())

    resp2 = probe_with_model(base_url, MATH_PROMPT, test_model, timeout=timeout)
    math_pass = _check_math_answer(resp2)

    if len(model_ids) > 1:
        resp3 = probe_with_model(base_url, CANARY_PROMPT, test_model, timeout=timeout)
        consistency_pass = resp3 is not None and resp1 != resp3
    else:
        resp3 = None
        consistency_pass = True

    if canary_pass and math_pass:
        status = "legitimate"
    else:
        status = "honeypot"

    return status, {
        "canary_pass": canary_pass,
        "math_pass": math_pass,
        "consistency_pass": consistency_pass,
        "models_found": model_ids,
        "model_type": "chat",
        "model_used": test_model,
        "model_discovery_source": source,
        "responses": [
            {"check": "canary", "prompt": CANARY_PROMPT, "response": resp1},
            {"check": "math", "prompt": MATH_PROMPT, "response": resp2},
            {"check": "consistency", "prompt": CANARY_PROMPT, "response": resp3},
        ],
    }


def verify_endpoint(ip: str, port: int, scheme: str = "http", timeout: float = 5):
    """Run honeypot detection on an LLM endpoint.

    Steps:
      1. TCP connect check
      2. Discover models
      3. Classify models by type
      4. Route to type-specific verifier (chat/embeddings/image/audio)

    Returns:
        (status, details) where status is "legitimate" | "honeypot" | "unreachable"
    """
    base_url = f"{scheme}://{ip}:{port}"

    # 1. TCP pre-check (skipped when proxying — the proxy does the connecting,
    #    and the worker may not be able to reach the target directly)
    if not proxy_pool_active() and not _tcp_connect(ip, port, timeout=2.0):
        return "unreachable", {
            "error": "tcp_connect_failed",
            "models_found": [],
            "model_type": None,
            "responses": [],
        }

    # 2. Discover models
    model_ids, source = discover_models(base_url, timeout=timeout)
    model_ids = [mid for mid in model_ids if mid]

    if not model_ids:
        # No models discovered — try generic chat prompt anyway
        resp1 = probe_with_model(base_url, CANARY_PROMPT, "", timeout=timeout)
        if resp1 is None:
            return "unreachable", {
                "error": "no_models_and_no_response",
                "models_found": [],
                "model_type": None,
                "responses": [],
            }
        canary_pass = bool(str(resp1).strip())
        resp2 = probe_with_model(base_url, MATH_PROMPT, "", timeout=timeout)
        math_pass = _check_math_answer(resp2)
        resp3 = probe_with_model(base_url, CANARY_PROMPT, "", timeout=timeout)
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
                {"check": "canary", "prompt": CANARY_PROMPT, "response": resp1},
                {"check": "math", "prompt": MATH_PROMPT, "response": resp2},
                {"check": "consistency", "prompt": CANARY_PROMPT, "response": resp3},
            ],
        }

    # 3. Classify models and pick the best model for verification
    model_types = {mid: _classify_model_type(mid) for mid in model_ids}
    chat_models = [mid for mid, t in model_types.items() if t == "chat"]
    embed_models = [mid for mid, t in model_types.items() if t == "embeddings"]
    image_models = [mid for mid, t in model_types.items() if t == "image"]
    audio_models = [mid for mid, t in model_types.items() if t == "audio"]

    # 4. Route to type-specific verifier
    if chat_models:
        return _verify_chat(base_url, model_ids, source, chat_models[0], timeout)

    if embed_models:
        ok, info = verify_embeddings(base_url, embed_models[0], timeout=timeout)
        return ("legitimate" if ok else "honeypot"), {
            "models_found": model_ids,
            "model_type": "embeddings",
            "model_used": embed_models[0],
            "embeddings_verified": ok,
            "verification_info": info,
            "responses": [{"check": "embeddings", "model": embed_models[0], "result": info}],
        }

    if image_models:
        ok, info = verify_image(base_url, image_models[0], timeout=timeout)
        return ("legitimate" if ok else "honeypot"), {
            "models_found": model_ids,
            "model_type": "image",
            "model_used": image_models[0],
            "image_verified": ok,
            "verification_info": info,
            "responses": [{"check": "image", "model": image_models[0], "result": info}],
        }

    if audio_models:
        ok, info = verify_audio(base_url, audio_models[0], timeout=timeout)
        return ("legitimate" if ok else "honeypot"), {
            "models_found": model_ids,
            "model_type": "audio",
            "model_used": audio_models[0],
            "audio_verified": ok,
            "verification_info": info,
            "responses": [{"check": "audio", "model": audio_models[0], "result": info}],
        }

    # Unknown model types — try as chat
    return _verify_chat(base_url, model_ids, source, model_ids[0], timeout)


# ── Model listing (for /matches/{id}/models) ──

def list_models_openai(base_url: str, timeout: float = 5):
    try:
        r = _rget(f"{base_url}/v1/models", timeout=timeout)
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
        r = _rget(f"{base_url}/api/tags", timeout=timeout)
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
        r = _rget(f"{base_url}/api/v1/model", timeout=timeout)
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
