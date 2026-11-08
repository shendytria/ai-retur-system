import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Session

from database import Base, get_db

# ---------------------------------------------------------------------------
# Konfigurasi
# ---------------------------------------------------------------------------
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-ganti-di-production-minimal-32-karakter")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))  # 8 jam

ROLE_LABELS = {
    "supply_chain": "Supply Chain / Gudang",
    "sales_ops": "Sales Ops",
    "data_admin": "Data / AI Admin",
    "manajemen": "Manajemen",
}

# Matriks hak akses sesuai URS
PERMISSIONS = {
    "supply_chain": {"dashboard_view", "notifikasi_accept", "mitigasi_edit", "audit_view"},
    "sales_ops": {"dashboard_view"},
    "data_admin": {"dashboard_view", "threshold_edit", "laporan_export", "audit_view", "user_manage"},
    "manajemen": {"dashboard_view", "laporan_export"},
}


# ---------------------------------------------------------------------------
# Model tabel
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    full_name = Column(String(150), nullable=False)
    email = Column(String(150))
    department = Column(String(100))
    role = Column(String(30), nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    last_login_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)


class AuditLog(Base):
    """Audit trail (ALCOA): siapa, kapan, apa. Hanya di-insert, tidak pernah di-update."""

    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer)
    username = Column(String(100))
    role = Column(String(30))
    action = Column(String(50), nullable=False)   # LOGIN, LOGIN_FAILED, CREATE, UPDATE, DELETE, ...
    entity = Column(String(100))                  # mis. "auth", "mitigasi", "threshold"
    detail = Column(Text)
    ip_address = Column(String(64))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ---------------------------------------------------------------------------
# Helper password, token, audit
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def write_audit(
    db: Session,
    action: str,
    entity: str = "",
    detail: str = "",
    user: Optional[User] = None,
    username: Optional[str] = None,
    request: Optional[Request] = None,
) -> None:
    """Catat ke audit trail. Panggil dari endpoint mitigasi/threshold juga."""
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            username=user.username if user else username,
            role=user.role if user else None,
            action=action,
            entity=entity,
            detail=detail,
            ip_address=request.client.host if request and request.client else None,
        )
    )
    db.commit()


def authenticate(db: Session, username: str, password: str) -> Optional[User]:
    """
    Titik integrasi ONEKalbe: ganti fungsi ini bila login sudah lewat ONEKalbe.
    Sekarang: cek username/password lokal.
    """
    user = db.query(User).filter(func.lower(User.username) == username.strip().lower()).first()
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


# ---------------------------------------------------------------------------
# Dependency untuk melindungi endpoint
# ---------------------------------------------------------------------------
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sesi tidak valid atau sudah berakhir. Silakan login kembali.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None:
        raise unauthorized
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized

    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise unauthorized
    return user


def require_permission(permission: str):
    """Pakai: Depends(require_permission("mitigasi_edit"))"""

    def checker(user: User = Depends(get_current_user)) -> User:
        if permission not in PERMISSIONS.get(user.role, set()):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{ROLE_LABELS.get(user.role, user.role)}' tidak memiliki akses ke fitur ini.",
            )
        return user

    return checker


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


def user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "department": user.department,
        "role": user.role,
        "role_label": ROLE_LABELS.get(user.role, user.role),
        "permissions": sorted(PERMISSIONS.get(user.role, set())),
    }


@router.post("/api/auth/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = authenticate(db, body.username, body.password)
    if not user:
        write_audit(db, "LOGIN_FAILED", "auth", f"Percobaan login gagal untuk '{body.username}'",
                    username=body.username, request=request)
        raise HTTPException(status_code=401, detail="Username atau password salah.")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    write_audit(db, "LOGIN", "auth", "Login berhasil", user=user, request=request)

    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "expires_in": JWT_EXPIRE_MINUTES * 60,
        "user": user_payload(user),
    }


@router.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return user_payload(user)


@router.post("/api/auth/logout")
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # JWT bersifat stateless; logout sebenarnya dilakukan dengan membuang token di frontend.
    write_audit(db, "LOGOUT", "auth", "Logout", user=user, request=request)
    return {"ok": True}


@router.get("/api/audit-trail")
def audit_trail(
    limit: int = 100,
    action: Optional[str] = None,   # mis. LOGIN, LOGIN_FAILED, LOGOUT, CREATE, UPDATE
    entity: Optional[str] = None,   # mis. auth, mitigasi, threshold
    q: Optional[str] = None,        # cari di username atau detail
    user: User = Depends(require_permission("audit_view")),
    db: Session = Depends(get_db),
):
    """Audit trail: hanya-baca (tidak ada endpoint ubah/hapus), terbaru di atas."""
    query = db.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action == action.upper())
    if entity:
        query = query.filter(AuditLog.entity == entity)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(AuditLog.username.ilike(like) | AuditLog.detail.ilike(like))
    rows = query.order_by(AuditLog.id.desc()).limit(max(1, min(limit, 500))).all()
    return [
        {
            "id": r.id,
            "username": r.username,
            "role": r.role,
            "action": r.action,
            "entity": r.entity,
            "detail": r.detail,
            "ip_address": r.ip_address,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]