from zoneinfo import ZoneInfo

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text, func

from database import Base

ACTION_TYPES = ["Transfer Stok", "Program Diskon Khusus", "Retur Terjadwal"]
WIB = ZoneInfo("Asia/Jakarta")


class Mitigasi(Base):
    __tablename__ = "mitigasi"

    id = Column(Integer, primary_key=True, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=False)
    sku_code = Column(String(50), unique=True, nullable=False, index=True)  # 1 mitigasi aktif per SKU
    action_type = Column(String(50), nullable=False)
    notes = Column(Text, nullable=False)
    skor_saat_mitigasi = Column(Numeric(5, 4))  # skor risiko pada saat pertama dicatat

    created_by_id = Column(Integer, nullable=False)
    created_by = Column(String(150), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    updated_by_id = Column(Integer)
    updated_by = Column(String(150))
    updated_at = Column(DateTime(timezone=True))


def fmt_waktu(dt) -> str:
    """Format tampilan waktu dalam WIB."""
    if dt is None:
        return "-"
    if dt.tzinfo is None:  # beberapa DB mengembalikan waktu tanpa zona (UTC)
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(WIB).strftime("%Y-%m-%d %H:%M")


def mitigasi_info(m: "Mitigasi") -> dict:
    """Bentuk yang dipakai frontend (mitigation_info)."""
    last_user = m.updated_by or m.created_by
    last_time = m.updated_at or m.created_at
    return {
        "sku_code": m.sku_code,
        "action_type": m.action_type,
        "notes": m.notes,
        "user_pic": last_user,
        "created_at": fmt_waktu(last_time),
    }