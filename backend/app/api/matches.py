from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.auth import get_current_active_user
from backend.app.database import get_db
from backend.app.models import User, Match, ScanJob
from backend.app.schemas import MatchOut
from sqlalchemy.orm import joinedload

router = APIRouter(prefix="/matches", tags=["matches"])


@router.get("")
def list_matches(
    scan_id: Optional[int] = Query(None),
    provider: Optional[str] = Query(None),
    service: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None),
    max_score: Optional[int] = Query(None),
    llm_mode: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    q = db.query(Match).options(joinedload(Match.scan_job)).join(ScanJob).filter(ScanJob.user_id == current_user.id)

    if scan_id:
        q = q.filter(Match.scan_job_id == scan_id)
    if provider:
        q = q.filter(Match.provider == provider)
    if service:
        q = q.filter(Match.service == service)
    if min_score is not None:
        q = q.filter(Match.score >= min_score)
    if max_score is not None:
        q = q.filter(Match.score <= max_score)
    if llm_mode is not None:
        q = q.filter(ScanJob.llm_mode == llm_mode)

    total = q.count()
    items = (
        q.order_by(Match.score.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "items": [MatchOut.model_validate(m) for m in items],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


@router.get("/stats")
def match_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    from sqlalchemy import func

    # Provider breakdown
    provider_q = (
        db.query(Match.provider, func.count(Match.id).label("count"))
        .join(ScanJob)
        .filter(ScanJob.user_id == current_user.id)
        .group_by(Match.provider)
        .all()
    )

    # Mode breakdown (opencode vs LLM)
    mode_q = (
        db.query(ScanJob.llm_mode, func.count(Match.id).label("count"))
        .join(ScanJob)
        .filter(ScanJob.user_id == current_user.id)
        .group_by(ScanJob.llm_mode)
        .all()
    )

    return {
        "by_provider": [{"provider": p or "unknown", "count": c} for p, c in provider_q],
        "by_mode": {
            "opencode": sum(c for mode, c in mode_q if not mode),
            "llm": sum(c for mode, c in mode_q if mode),
        },
    }


@router.post("/import")
def import_cli_results(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Import results from a CLI scanner results.json file into the web database."""
    import json
    from datetime import datetime
    from backend.app.models import ScanJob, Match

    if not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="Only JSON files are accepted")

    try:
        content = file.file.read()
        data = json.loads(content)
    except json.JSONDecodeError as e:
        # Give a more helpful error message
        msg = str(e)
        if "Unterminated" in msg or "unexpected end" in msg.lower():
            raise HTTPException(
                status_code=400,
                detail=f"JSON file is incomplete or corrupted (truncated). The scanner may have been interrupted before writing the full file. Error: {msg[:100]}"
            )
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {msg[:100]}")

    # Validate structure
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="JSON root must be an object (dict), not a list or scalar")
    if "matches" not in data:
        raise HTTPException(status_code=400, detail="Missing 'matches' key in JSON. Expected structure: { '$meta': {...}, 'stats': {...}, 'matches': [...] }")

    matches = data.get("matches", [])
    if not isinstance(matches, list):
        raise HTTPException(status_code=400, detail="'matches' must be a list")
    if not matches:
        raise HTTPException(status_code=400, detail="No matches found in uploaded file (matches array is empty)")

    # Determine if LLM mode from file contents
    is_llm = any(m.get("service") in ("ollama", "vllm", "llamacpp", "kobold", "textgen") for m in matches)

    # Create a synthetic scan job
    job = ScanJob(
        user_id=current_user.id,
        name=f"CLI Import {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
        status="completed",
        providers=["cli_import"],
        ports=list(set(str(m.get("port", 0)) for m in matches)),
        llm_mode=is_llm,
        score_threshold=5,
        stats_json=data.get("stats", {}),
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    imported = 0
    for m in matches:
        match = Match(
            scan_job_id=job.id,
            ip=m.get("ip", "unknown"),
            port=m.get("port", 0),
            scheme=m.get("scheme", "http"),
            score=m.get("score", 0),
            service=m.get("service", "unknown"),
            provider=m.get("provider"),
            region=m.get("region"),
            methods_hit=m.get("methods_hit", []),
            details_json=m.get("details", {}),
        )
        db.add(match)
        imported += 1

    db.commit()
    return {"imported": imported, "scan_job_id": job.id}


@router.get("/{match_id}/models")
def list_models_for_match(
    match_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Probe the LLM endpoint to discover available models."""
    import requests

    match = (
        db.query(Match)
        .join(ScanJob)
        .filter(Match.id == match_id, ScanJob.user_id == current_user.id)
        .first()
    )
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    base_url = f"{match.scheme}://{match.ip}:{match.port}"
    service = match.service or "unknown"
    models = []

    try:
        if service in ("ollama",):
            # Ollama: GET /api/tags
            r = requests.get(f"{base_url}/api/tags", timeout=5)
            if r.status_code == 200:
                data = r.json()
                for m in data.get("models", []):
                    models.append({
                        "id": m.get("name", m.get("model", "unknown")),
                        "name": m.get("name", m.get("model", "unknown")),
                        "size": m.get("size"),
                        "parameter_size": m.get("parameter_size"),
                        "quantization_level": m.get("details", {}).get("quantization_level"),
                    })

        elif service in ("vllm", "textgen", "llamacpp"):
            # OpenAI-compatible: GET /v1/models
            r = requests.get(f"{base_url}/v1/models", timeout=5)
            if r.status_code == 200:
                data = r.json()
                for m in data.get("data", []):
                    models.append({
                        "id": m.get("id", "unknown"),
                        "name": m.get("id", "unknown"),
                    })

        elif service == "kobold":
            # Kobold: GET /api/v1/model
            r = requests.get(f"{base_url}/api/v1/model", timeout=5)
            if r.status_code == 200:
                data = r.json()
                model_name = data.get("result", "unknown")
                models.append({"id": model_name, "name": model_name})

        # Fallback: try OpenAI-compatible for any service
        if not models:
            r = requests.get(f"{base_url}/v1/models", timeout=5)
            if r.status_code == 200:
                data = r.json()
                for m in data.get("data", []):
                    models.append({"id": m.get("id", "unknown"), "name": m.get("id", "unknown")})

    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Could not reach endpoint: {e}")

    return {"models": models, "service": service, "url": base_url}


class TestPromptPayload(BaseModel):
    model: str
    prompt: str = "hi"
    max_tokens: int = 100


@router.post("/{match_id}/test")
def test_prompt(
    match_id: int,
    payload: TestPromptPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Send a test prompt to the LLM endpoint and return the response."""
    import requests

    match = (
        db.query(Match)
        .join(ScanJob)
        .filter(Match.id == match_id, ScanJob.user_id == current_user.id)
        .first()
    )
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    base_url = f"{match.scheme}://{match.ip}:{match.port}"
    service = match.service or "unknown"
    model = payload.model
    prompt = payload.prompt

    try:
        if service in ("ollama",):
            # Ollama: POST /api/generate
            r = requests.post(
                f"{base_url}/api/generate",
                json={"model": model, "prompt": prompt, "stream": False},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "response": data.get("response", ""),
                    "done": data.get("done", True),
                    "total_duration_ms": data.get("total_duration", 0) / 1e6,
                    "prompt_eval_count": data.get("prompt_eval_count", 0),
                    "eval_count": data.get("eval_count", 0),
                }
            else:
                raise HTTPException(status_code=502, detail=f"Ollama returned {r.status_code}: {r.text[:200]}")

        elif service in ("vllm", "textgen", "llamacpp"):
            # OpenAI-compatible chat: POST /v1/chat/completions
            r = requests.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": payload.max_tokens,
                    "stream": False,
                },
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                choice = data.get("choices", [{}])[0]
                msg = choice.get("message", {})
                return {
                    "response": msg.get("content", ""),
                    "finish_reason": choice.get("finish_reason"),
                    "prompt_tokens": data.get("usage", {}).get("prompt_tokens"),
                    "completion_tokens": data.get("usage", {}).get("completion_tokens"),
                }
            else:
                raise HTTPException(status_code=502, detail=f"Endpoint returned {r.status_code}: {r.text[:200]}")

        elif service == "kobold":
            # Kobold: POST /api/v1/generate
            r = requests.post(
                f"{base_url}/api/v1/generate",
                json={"prompt": prompt, "max_length": payload.max_tokens},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", [{}])
                return {"response": results[0].get("text", "")}
            else:
                raise HTTPException(status_code=502, detail=f"Kobold returned {r.status_code}: {r.text[:200]}")

        else:
            # Fallback: try OpenAI-compatible
            r = requests.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": payload.max_tokens,
                    "stream": False,
                },
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                choice = data.get("choices", [{}])[0]
                msg = choice.get("message", {})
                return {
                    "response": msg.get("content", ""),
                    "finish_reason": choice.get("finish_reason"),
                }
            else:
                raise HTTPException(status_code=502, detail=f"Fallback returned {r.status_code}: {r.text[:200]}")

    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Request failed: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
