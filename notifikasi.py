from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Session

from database import Base
from models import SkuMaster, SkorRisiko, Transaksi
from pengaturan import HIGH_LABEL, get_threshold

STATUS_BARU = "BARU"
STATUS_DITERIMA = "DITERIMA"


class Notifikasi(Base):
    __tablename__ = "notifikasi"

    id = Column(Integer, primary_key=True, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=False, index=True)
    sku_code = Column(String(50), nullable=False, index=True)
    product_name = Column(String(255))
    sumber = Column(String(100))                      # Kalbe / Saka (pengganti "area" sementara)
    skor = Column(Numeric(5, 4), nullable=False)
    estimasi_nilai_risiko = Column(Numeric(18, 2), nullable=False, default=0)
    threshold_saat_itu = Column(Numeric(5, 4))

    status = Column(String(20), nullable=False, default=STATUS_BARU)  # BARU / DITERIMA
    aktif = Column(Boolean, nullable=False, default=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    accepted_by_id = Column(Integer)
    accepted_by = Column(String(150))
    accepted_at = Column(DateTime(timezone=True))
    closed_at = Column(DateTime(timezone=True))


def _nilai_retur_dan_sumber(db: Session):
    """Total nilai retur (absolut) dan daftar sumber per SKU."""
    retur = {
        sid: float(total or 0)
        for sid, total in db.query(Transaksi.sku_id, func.sum(func.abs(Transaksi.amount)))
        .filter(Transaksi.type == "RETURN")
        .group_by(Transaksi.sku_id)
        .all()
    }
    sumber: dict[int, set] = {}
    for sid, src in db.query(Transaksi.sku_id, Transaksi.source).distinct().all():
        if src:
            sumber.setdefault(sid, set()).add(src.strip())
    return retur, sumber


def sync_notifikasi(db: Session) -> dict:
    """
    Samakan notifikasi dengan status skor terbaru. Aman dipanggil berulang (idempotent).
    Panggil setelah: backend start, ubah threshold, dan train_and_score.py selesai.
    """
    threshold = get_threshold(db)

    # skor terbaru per SKU (baris terakhir menang)
    latest = {r.sku_id: r for r in db.query(SkorRisiko).order_by(SkorRisiko.id).all()}
    high = {sid: r for sid, r in latest.items() if r.status_threshold == HIGH_LABEL}
    aktif = {n.sku_id: n for n in db.query(Notifikasi).filter(Notifikasi.aktif.is_(True)).all()}

    retur, sumber = _nilai_retur_dan_sumber(db)
    sku_info = {s.id: s for s in db.query(SkuMaster).filter(SkuMaster.id.in_(list(high))).all()} if high else {}

    created = closed = updated = 0

    for sid, r in high.items():
        est = round(float(r.skor) * retur.get(sid, 0.0), 2)
        n = aktif.get(sid)
        if n is None:
            sku = sku_info.get(sid)
            if sku is None:
                continue
            db.add(
                Notifikasi(
                    sku_id=sid,
                    sku_code=sku.sku_code,
                    product_name=sku.product_name,
                    sumber=", ".join(sorted(sumber.get(sid, []))) or None,
                    skor=r.skor,
                    estimasi_nilai_risiko=est,
                    threshold_saat_itu=threshold,
                    status=STATUS_BARU,
                    aktif=True,
                )
            )
            created += 1
        else:
            # perbarui angka bila skor dihitung ulang; status BARU/DITERIMA tidak diubah
            if float(n.skor) != float(r.skor) or float(n.estimasi_nilai_risiko) != est:
                n.skor = r.skor
                n.estimasi_nilai_risiko = est
                n.threshold_saat_itu = threshold
                updated += 1

    for sid, n in aktif.items():
        if sid not in high:
            n.aktif = False
            n.closed_at = func.now()
            closed += 1

    db.commit()
    return {"created": created, "closed": closed, "updated": updated}