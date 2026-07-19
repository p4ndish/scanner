from datetime import datetime
from typing import List
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.auth import get_current_active_user
from backend.app.database import get_db
from backend.app.models import User, ProxyConfig
from backend.app.crypto import encrypt, decrypt
from backend.app.schemas import ProxyConfigCreate, ProxyConfigOut

router = APIRouter(prefix="/proxies", tags=["proxies"])


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
