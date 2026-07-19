from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.auth import get_current_active_user
from backend.app.database import get_db
from backend.app.models import User, ScanMachine
from backend.app.crypto import encrypt, decrypt
from backend.app.schemas import ScanMachineCreate, ScanMachineUpdate, ScanMachineOut

router = APIRouter(prefix="/machines", tags=["machines"])


def _machine_to_out(m: ScanMachine) -> ScanMachineOut:
    return ScanMachineOut(
        id=m.id,
        name=m.name,
        host=m.host,
        port=m.port,
        username=m.username,
        auth_type=m.auth_type,
        use_sudo=m.use_sudo,
        last_tested_at=m.last_tested_at,
        last_test_ok=m.last_test_ok,
        last_test_message=m.last_test_message,
        created_at=m.created_at,
    )


@router.get("", response_model=List[ScanMachineOut])
@router.get("/", response_model=List[ScanMachineOut], include_in_schema=False)
def list_machines(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    rows = (
        db.query(ScanMachine)
        .filter(ScanMachine.user_id == current_user.id)
        .order_by(ScanMachine.created_at.desc())
        .all()
    )
    return [_machine_to_out(m) for m in rows]


@router.post("", response_model=ScanMachineOut)
@router.post("/", response_model=ScanMachineOut, include_in_schema=False)
def create_machine(
    data: ScanMachineCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if data.auth_type not in ("key", "password"):
        raise HTTPException(status_code=400, detail="auth_type must be 'key' or 'password'")
    if not data.secret.strip():
        raise HTTPException(status_code=400, detail="secret (private key or password) is required")

    m = ScanMachine(
        user_id=current_user.id,
        name=data.name.strip(),
        host=data.host.strip(),
        port=data.port,
        username=data.username.strip(),
        auth_type=data.auth_type,
        encrypted_secret=encrypt(data.secret),
        use_sudo=data.use_sudo,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return _machine_to_out(m)


@router.put("/{machine_id}", response_model=ScanMachineOut)
def update_machine(
    machine_id: int,
    data: ScanMachineUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    m = (
        db.query(ScanMachine)
        .filter(ScanMachine.id == machine_id, ScanMachine.user_id == current_user.id)
        .first()
    )
    if not m:
        raise HTTPException(status_code=404, detail="Machine not found")

    if data.name is not None:
        m.name = data.name.strip()
    if data.host is not None:
        m.host = data.host.strip()
    if data.port is not None:
        m.port = data.port
    if data.username is not None:
        m.username = data.username.strip()
    if data.auth_type is not None:
        if data.auth_type not in ("key", "password"):
            raise HTTPException(status_code=400, detail="auth_type must be 'key' or 'password'")
        m.auth_type = data.auth_type
    if data.use_sudo is not None:
        m.use_sudo = data.use_sudo
    if data.secret is not None and data.secret.strip():
        m.encrypted_secret = encrypt(data.secret)

    db.commit()
    db.refresh(m)
    return _machine_to_out(m)


@router.delete("/{machine_id}")
def delete_machine(
    machine_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    m = (
        db.query(ScanMachine)
        .filter(ScanMachine.id == machine_id, ScanMachine.user_id == current_user.id)
        .first()
    )
    if not m:
        raise HTTPException(status_code=404, detail="Machine not found")
    db.delete(m)
    db.commit()
    return {"ok": True}


@router.post("/{machine_id}/test")
def test_machine(
    machine_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Test SSH connectivity + masscan presence on the remote machine."""
    from backend.app.ssh_runner import SSHRunner, SSHError

    m = (
        db.query(ScanMachine)
        .filter(ScanMachine.id == machine_id, ScanMachine.user_id == current_user.id)
        .first()
    )
    if not m:
        raise HTTPException(status_code=404, detail="Machine not found")

    secret = decrypt(m.encrypted_secret)
    runner = SSHRunner(m.host, m.port, m.username, m.auth_type, secret)

    ok = False
    message = ""
    try:
        runner.connect(timeout=15)
        rc, uname = runner._exec_rc("uname -a")
        masscan_ok, masscan_path = runner.check_masscan()
        if not masscan_ok:
            ok = False
            message = f"Connected, but masscan not found on remote. Install it: apt install masscan"
        else:
            ok = True
            message = f"OK · {uname.strip().split()[0]} · masscan: {masscan_path}"
    except SSHError as e:
        ok = False
        message = str(e)
    except Exception as e:
        ok = False
        message = f"Unexpected error: {e}"
    finally:
        runner.close()

    m.last_tested_at = datetime.utcnow()
    m.last_test_ok = ok
    m.last_test_message = message[:256]
    db.commit()

    return {"ok": ok, "message": message}
