from datetime import datetime
from typing import List
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.app.auth import get_current_active_user
from backend.app.database import get_db
from backend.app.models import User, ProxyConfig
from backend.app.crypto import encrypt, decrypt
from backend.app.schemas import ProxyConfigCreate, ProxyConfigUpdate, ProxyConfigOut

router = APIRouter(prefix="/proxies", tags=["proxies"])

VALID_SCHEMES = ("http", "https", "socks5", "socks5h")


def build_proxy_url(p: ProxyConfig) -> str:
    """Build a full proxy URL string from a ProxyConfig, embedding credentials."""
    user = p.username or ""
    pwd = decrypt(p.encrypted_password) if p.encrypted_password else ""
    auth = ""
    if user:
        # URL-encode credentials so special chars don't break the URL
        auth = f"{quote(user, safe='')}:{quote(pwd, safe='')}@"
    return f"{p.scheme}://{auth}{p.host}:{p.port}"


def _to_out(p: ProxyConfig) -> ProxyConfigOut:
    return ProxyConfigOut(
        id=p.id, name=p.name, scheme=p.scheme, host=p.host, port=p.port,
        username=p.username,
        last_tested_at=p.last_tested_at, last_test_ok=p.last_test_ok,
        last_test_message=p.last_test_message, created_at=p.created_at,
    )


@router.get("", response_model=List[ProxyConfigOut])
@router.get("/", response_model=List[ProxyConfigOut], include_in_schema=False)
def list_proxies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    rows = (
        db.query(ProxyConfig)
        .filter(ProxyConfig.user_id == current_user.id)
        .order_by(ProxyConfig.created_at.desc())
        .all()
    )
    return [_to_out(p) for p in rows]


@router.post("", response_model=ProxyConfigOut)
@router.post("/", response_model=ProxyConfigOut, include_in_schema=False)
def create_proxy(
    data: ProxyConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if data.scheme not in ("http", "https", "socks5", "socks5h"):
        raise HTTPException(status_code=400, detail="scheme must be http, https, socks5, or socks5h")

    p = ProxyConfig(
        user_id=current_user.id,
        name=data.name.strip(),
        scheme=data.scheme,
        host=data.host.strip(),
        port=data.port,
        username=data.username.strip() if data.username else None,
        encrypted_password=encrypt(data.password) if data.password else None,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_out(p)


@router.get("/export")
def export_proxies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Export all of the user's proxies as JSON, including decrypted passwords
    so the file can be re-imported on another instance."""
    from datetime import timezone
    rows = (
        db.query(ProxyConfig)
        .filter(ProxyConfig.user_id == current_user.id)
        .order_by(ProxyConfig.created_at.asc())
        .all()
    )
    proxies = []
    for p in rows:
        proxies.append({
            "name": p.name,
            "scheme": p.scheme,
            "host": p.host,
            "port": p.port,
            "username": p.username,
            "password": decrypt(p.encrypted_password) if p.encrypted_password else None,
        })
    return {
        "$meta": {
            "tool": "opencode-scanner",
            "type": "proxies",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "count": len(proxies),
        },
        "proxies": proxies,
    }


class ProxyImportItem(ProxyConfigCreate):
    pass


class ProxyImportPayload(BaseModel):
    proxies: list[ProxyImportItem]
    replace: bool = False  # if true, delete existing proxies first


@router.post("/import")
def import_proxies(
    payload: ProxyImportPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Import proxies from an exported JSON. Skips exact duplicates
    (same host:port:username) unless replace=true."""
    if payload.replace:
        db.query(ProxyConfig).filter(ProxyConfig.user_id == current_user.id).delete()
        db.commit()

    existing = {
        (p.host, p.port, p.username or "")
        for p in db.query(ProxyConfig).filter(ProxyConfig.user_id == current_user.id).all()
    }
    imported = 0
    skipped = 0
    for item in payload.proxies:
        scheme = item.scheme if item.scheme in VALID_SCHEMES else "http"
        key = (item.host.strip(), item.port, (item.username or "").strip())
        if key in existing:
            skipped += 1
            continue
        p = ProxyConfig(
            user_id=current_user.id,
            name=item.name.strip(),
            scheme=scheme,
            host=item.host.strip(),
            port=item.port,
            username=item.username.strip() if item.username else None,
            encrypted_password=encrypt(item.password) if item.password else None,
        )
        db.add(p)
        existing.add(key)
        imported += 1
    db.commit()
    return {"imported": imported, "skipped": skipped}


@router.put("/{proxy_id}", response_model=ProxyConfigOut)
def update_proxy(
    proxy_id: int,
    data: ProxyConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Edit an existing proxy. Any omitted field is left unchanged. Leave
    password empty/null to keep the current password."""
    p = (
        db.query(ProxyConfig)
        .filter(ProxyConfig.id == proxy_id, ProxyConfig.user_id == current_user.id)
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Proxy not found")

    if data.name is not None:
        p.name = data.name.strip()
    if data.scheme is not None:
        if data.scheme not in VALID_SCHEMES:
            raise HTTPException(status_code=400, detail="scheme must be http, https, socks5, or socks5h")
        p.scheme = data.scheme
    if data.host is not None:
        p.host = data.host.strip()
    if data.port is not None:
        p.port = data.port
    if data.username is not None:
        p.username = data.username.strip() or None
    if data.password:  # only change password if a non-empty one is provided
        p.encrypted_password = encrypt(data.password)

    db.commit()
    db.refresh(p)
    return _to_out(p)


@router.delete("/{proxy_id}")
def delete_proxy(
    proxy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    p = (
        db.query(ProxyConfig)
        .filter(ProxyConfig.id == proxy_id, ProxyConfig.user_id == current_user.id)
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Proxy not found")
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/{proxy_id}/test")
def test_proxy(
    proxy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Test a proxy by fetching an echo service through it."""
    import requests as _requests

    p = (
        db.query(ProxyConfig)
        .filter(ProxyConfig.id == proxy_id, ProxyConfig.user_id == current_user.id)
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Proxy not found")

    import time
    url = build_proxy_url(p)
    proxies = {"http": url, "https": url}
    ok = False
    message = ""
    try:
        # Primary connectivity check: a reliable endpoint that always returns 200
        # and does NOT rate-limit (unlike ip-api.com, which caps at 45 req/min and
        # would break the test with a JSON-parse error once exceeded).
        t0 = time.time()
        r = _requests.get("http://example.com", proxies=proxies, timeout=10)
        latency = time.time() - t0
        if 200 <= r.status_code < 400:
            ok = True
            speed = "fast" if latency < 1.5 else ("ok" if latency < 4 else "SLOW")
            # Best-effort exit-IP lookup — never fail the test if this is rate-limited
            exit_info = ""
            try:
                ir = _requests.get("http://ip-api.com/json", proxies=proxies, timeout=8)
                d = ir.json()
                if isinstance(d, dict) and d.get("query"):
                    exit_info = f" · exit {d['query']} ({d.get('country', '?')})"
            except Exception:
                pass
            message = f"OK{exit_info} · {latency:.1f}s [{speed}]"
            if latency >= 4:
                message += " — too slow for verification, hosts may show unreachable"
        else:
            message = f"Proxy returned HTTP {r.status_code}"
    except _requests.exceptions.ProxyError as e:
        message = f"Proxy rejected or unreachable: {str(e)[:120]}"
    except _requests.exceptions.ReadTimeout:
        message = "Read timeout (>10s) — proxy is dead or too slow for verification"
    except Exception as e:
        message = f"Proxy test failed: {str(e)[:150]}"
    finally:
        p.last_tested_at = datetime.utcnow()
        p.last_test_ok = ok
        p.last_test_message = message[:256]
        db.commit()

    return {"ok": ok, "message": message, "exit_url": url.split("@")[-1] if "@" in url else f"{p.host}:{p.port}"}
