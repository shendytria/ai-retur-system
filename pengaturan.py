from sqlalchemy import Column, DateTime, String, func
from sqlalchemy.orm import Session

from database import Base
from models import SkorRisiko

HIGH_LABEL = "HIGH RISK"
DEFAULT_THRESHOLD = 0.5
DEFAULT_NORMAL_LABEL = "NORMAL"

KEY_THRESHOLD = "risk_threshold"
KEY_NORMAL_LABEL = "label_normal"


class Pengaturan(Base):
    __tablename__ = "pengaturan"

    key = Column(String(50), primary_key=True)
    value = Column(String(100), nullable=False)
    updated_by = Column(String(150))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def _get(db: Session, key: str):
    row = db.get(Pengaturan, key)
    return row.value if row else None


def _set(db: Session, key: str, value: str, by: str = "system"):
    row = db.get(Pengaturan, key)
    if row is None:
        db.add(Pengaturan(key=key, value=value, updated_by=by))
    else:
        row.value = value
        row.updated_by = by
        row.updated_at = func.now()


def _infer_initial(db: Session) -> tuple[float, str]:
    """Tebak nilai awal dari data yang sudah ada, supaya tampilan tidak berubah saat pertama dipasang."""
    high_min = (
        db.query(func.min(SkorRisiko.skor)).filter(SkorRisiko.status_threshold == HIGH_LABEL).scalar()
    )
    threshold = float(high_min) if high_min is not None else DEFAULT_THRESHOLD

    # label status non-HIGH yang paling sering dipakai di data
    other = (
        db.query(SkorRisiko.status_threshold, func.count())
        .filter(SkorRisiko.status_threshold != HIGH_LABEL, SkorRisiko.status_threshold.isnot(None))
        .group_by(SkorRisiko.status_threshold)
        .order_by(func.count().desc())
        .first()
    )
    normal = other[0] if other else DEFAULT_NORMAL_LABEL
    return threshold, normal


def ensure_defaults(db: Session) -> None:
    """Dipanggil saat backend start: isi nilai awal bila belum ada."""
    if _get(db, KEY_THRESHOLD) is None:
        threshold, normal = _infer_initial(db)
        _set(db, KEY_THRESHOLD, f"{threshold:.4f}")
        _set(db, KEY_NORMAL_LABEL, normal)
        db.commit()


def get_threshold(db: Session) -> float:
    v = _get(db, KEY_THRESHOLD)
    return float(v) if v is not None else DEFAULT_THRESHOLD


def get_normal_label(db: Session) -> str:
    return _get(db, KEY_NORMAL_LABEL) or DEFAULT_NORMAL_LABEL


def count_high(db: Session, threshold: float) -> int:
    """Berapa baris skor yang akan berstatus HIGH RISK pada threshold tertentu."""
    return db.query(SkorRisiko).filter(SkorRisiko.skor >= threshold).count()


def apply_threshold(db: Session, threshold: float, by: str) -> dict:
    """Simpan threshold baru lalu hitung ulang status semua skor. Mengembalikan ringkasan perubahan."""
    old = get_threshold(db)
    normal = get_normal_label(db)
    high_before = db.query(SkorRisiko).filter(SkorRisiko.status_threshold == HIGH_LABEL).count()

    from sqlalchemy import case

    new_status = case((SkorRisiko.skor >= threshold, HIGH_LABEL), else_=normal)
    changed = (
        db.query(SkorRisiko)
        .filter(SkorRisiko.status_threshold.is_distinct_from(new_status))
        .update({SkorRisiko.status_threshold: new_status}, synchronize_session=False)
    )
    _set(db, KEY_THRESHOLD, f"{threshold:.4f}", by=by)
    db.commit()

    high_after = db.query(SkorRisiko).filter(SkorRisiko.status_threshold == HIGH_LABEL).count()
    return {
        "old_threshold": old,
        "new_threshold": threshold,
        "rows_changed": changed,
        "high_risk_before": high_before,
        "high_risk_after": high_after,
    }