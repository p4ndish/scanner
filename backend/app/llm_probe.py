"""Robust LLM endpoint probing with multi-path fallback.

Different LLM services expose different API paths. This module tries the
most common ones in order and returns the first successful response.
"""
import requests


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}


def probe_chat_completions(base_url: str, prompt: str, timeout: float = 10, model: str = ""):
    """Try OpenAI-compatible /v1/chat/completions"""
    try:
        r = requests.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": model or "",
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
            return str(msg)
    except Exception:
        pass
    return None


def probe_ollama_chat(base_url: str, prompt: str, timeout: float = 10, model: str = ""):
    """Try Ollama /api/chat"""
    try:
        r = requests.post(
            f"{base_url}/api/chat",
            json={
                "model": model or "",
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
            return str(msg)
    except Exception:
        pass
    return None


def probe_ollama_generate(base_url: str, prompt: str, timeout: float = 10, model: str = ""):
    """Try Ollama /api/generate"""
    try:
        r = requests.post(
            f"{base_url}/api/generate",
            json={"model": model or "", "prompt": prompt, "stream": False},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = _safe_json(r)
            return data.get("response", "")
    except Exception:
        pass
    return None


def probe_kobold_generate(base_url: str, prompt: str, timeout: float = 10):
    """Try Kobold /api/v1/generate"""
    try:
        r = requests.post(
            f"{base_url}/api/v1/generate",
            json={"prompt": prompt, "max_length": 50},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = _safe_json(r)
            results = data.get("results", [{}])
            return results[0].get("text", "")
    except Exception:
        pass
    return None


def probe_openai_completions(base_url: str, prompt: str, timeout: float = 10, model: str = ""):
    """Try OpenAI legacy /v1/completions"""
    try:
        r = requests.post(
            f"{base_url}/v1/completions",
            json={"model": model or "", "prompt": prompt, "max_tokens": 50},
            timeout=timeout,
        )
        if r.status_code == 200:
            data = _safe_json(r)
            choices = data.get("choices", [{}])
            return choices[0].get("text", "")
    except Exception:
        pass
    return None


# ─── Main probe function ───

PROBERS = [
    probe_chat_completions,
    probe_ollama_chat,
    probe_ollama_generate,
    probe_kobold_generate,
    probe_openai_completions,
]


def probe_prompt(base_url: str, prompt: str, timeout: float = 10, model: str = ""):
    """Try all known prompt endpoints and return the first successful response.

    Args:
        base_url: e.g. "http://1.2.3.4:11434"
        prompt: the text prompt to send
        timeout: HTTP timeout in seconds
        model: optional model name (empty string works for most servers)

    Returns:
        Response text string, or None if all probes failed.
    """
    for prober in PROBERS:
        result = prober(base_url, prompt, timeout, model)
        if result is not None:
            return result
    return None


# ─── Model listing probes ───

def list_models_openai(base_url: str, timeout: float = 5):
    """Try OpenAI /v1/models"""
    try:
        r = requests.get(f"{base_url}/v1/models", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            models = []
            for m in data.get("data", []):
                models.append({
                    "id": m.get("id", "unknown"),
                    "name": m.get("id", "unknown"),
                })
            return models
    except Exception:
        pass
    return None


def list_models_ollama(base_url: str, timeout: float = 5):
    """Try Ollama /api/tags"""
    try:
        r = requests.get(f"{base_url}/api/tags", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            models = []
            for m in data.get("models", []):
                models.append({
                    "id": m.get("name", m.get("model", "unknown")),
                    "name": m.get("name", m.get("model", "unknown")),
                    "size": m.get("size"),
                    "parameter_size": m.get("parameter_size"),
                    "quantization_level": m.get("details", {}).get("quantization_level"),
                })
            return models
    except Exception:
        pass
    return None


def list_models_kobold(base_url: str, timeout: float = 5):
    """Try Kobold /api/v1/model"""
    try:
        r = requests.get(f"{base_url}/api/v1/model", timeout=timeout)
        if r.status_code == 200:
            data = _safe_json(r)
            model_name = data.get("result", "unknown")
            return [{"id": model_name, "name": model_name}]
    except Exception:
        pass
    return None


MODEL_LISTERS = [
    list_models_openai,
    list_models_ollama,
    list_models_kobold,
]


def probe_models(base_url: str, timeout: float = 5):
    """Try all known model-list endpoints and return the first successful list.

    Returns:
        List of model dicts, or empty list if all probes failed.
    """
    for lister in MODEL_LISTERS:
        result = lister(base_url, timeout)
        if result is not None:
            return result
    return []
