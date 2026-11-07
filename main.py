from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional

from database import get_db, engine, Base
from models import SkuMaster, Transaksi, SkorRisiko
# [AUTH] modul login + role
from auth import router as auth_router, get_current_user, require_permission, write_audit
# [MITIGASI] tabel mitigasi permanen (menggantikan list in-memory)
from mitigasi import Mitigasi, ACTION_TYPES, mitigasi_info, fmt_waktu
# [THRESHOLD] pengaturan ambang batas risiko (bisa diubah Data/AI Admin)
from pengaturan import ensure_defaults, get_threshold, count_high, apply_threshold, Pengaturan
# [NOTIF] notifikasi SKU melewati threshold untuk Supply Chain
from notifikasi import Notifikasi, sync_notifikasi, STATUS_BARU, STATUS_DITERIMA

# Buat tabel yang belum ada (users, audit_log, mitigasi). Tabel lama tidak diubah.
Base.metadata.create_all(bind=engine)
# [THRESHOLD] isi nilai awal threshold (diambil dari data skor yang sudah ada) bila belum ada
with Session(engine) as _db:
    ensure_defaults(_db)
    sync_notifikasi(_db)  # [NOTIF] samakan notifikasi dengan skor/threshold saat ini

app = FastAPI(title="Sistem AI Prediksi Risiko Retur Obsolete")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [AUTH] endpoint /api/auth/login, /api/auth/me, /api/auth/logout, /api/audit-trail
app.include_router(auth_router)

# Model Request Mitigasi
class ThresholdUpdate(BaseModel):
    threshold: float  # skala 0-1, mis. 0.6 = 60%

class MitigasiCreate(BaseModel):
    sku_code: str
    action_type: str  # 'Transfer Stok', 'Program Diskon Khusus', 'Retur Terjadwal'
    user_pic: Optional[str] = None  # [AUTH] diabaikan; PIC diambil dari user yang login
    notes: Optional[str] = None

@app.get("/api/dashboard/summary")
def get_dashboard_summary(db: Session = Depends(get_db), user=Depends(get_current_user)):
    total_sku = db.query(SkuMaster).count()
    high_risk_sku = db.query(SkorRisiko).filter(SkorRisiko.status_threshold == "HIGH RISK").count()
    total_retur_val = db.query(func.sum(Transaksi.amount)).filter(Transaksi.type == "RETURN").scalar() or 0
    total_sales_val = db.query(func.sum(Transaksi.amount)).filter(Transaksi.type == "SALES").scalar() or 0

    return {
        "total_sku": total_sku,
        "high_risk_sku_count": high_risk_sku,
        "total_sales_rupiah": abs(float(total_sales_val)),
        "total_return_rupiah": abs(float(total_retur_val)),
        "mitigasi_count": db.query(Mitigasi).count()  # [MITIGASI] dari database
    }

@app.get("/api/dashboard/high-risk-skus")
def get_high_risk_skus(status_filter: str = "ALL", db: Session = Depends(get_db), user=Depends(get_current_user)):
    query = (
        db.query(
            SkuMaster.sku_code,
            SkuMaster.product_name,
            SkuMaster.catagory,
            SkorRisiko.skor,
            SkorRisiko.status_threshold
        )
        .join(SkorRisiko, SkuMaster.id == SkorRisiko.sku_id)
    )
    if status_filter == "HIGH_RISK":
        query = query.filter(SkorRisiko.status_threshold == "HIGH RISK")
    
    results = query.order_by(SkorRisiko.skor.desc()).all()
    # [MITIGASI] baca dari database
    mitigated_codes = {m.sku_code: mitigasi_info(m) for m in db.query(Mitigasi).all()}

    return [
        {
            "sku_code": r.sku_code,
            "product_name": r.product_name,
            "category": r.catagory or "OTC",
            "risk_score": float(r.skor),
            "status": r.status_threshold,
            "is_mitigated": r.sku_code in mitigated_codes,
            "mitigation_info": mitigated_codes.get(r.sku_code)
        }
        for r in results
    ]

@app.get("/api/dashboard/chart-alasan-retur")
def get_chart_alasan_retur(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Data agregat untuk Pie/Donut Chart breakdown alasan retur"""
    results = (
        db.query(Transaksi.alasan_retur, func.sum(func.abs(Transaksi.amount)))
        .filter(Transaksi.type == "RETURN", Transaksi.alasan_retur.isnot(None))
        .group_by(Transaksi.alasan_retur)
        .all()
    )
    colors = ["#EF4444", "#F59E0B", "#3B82F6", "#8B5CF6", "#10B981"]
    return [
        {"name": r[0].strip(), "value": float(r[1]), "color": colors[i % len(colors)]}
        for i, r in enumerate(results)
    ]

@app.get("/api/dashboard/chart-top-risk")
def get_chart_top_risk(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Data 6 SKU dengan risiko tertinggi untuk Bar Chart"""
    results = (
        db.query(SkuMaster.product_name, SkorRisiko.skor)
        .join(SkorRisiko, SkuMaster.id == SkorRisiko.sku_id)
        .order_by(SkorRisiko.skor.desc())
        .limit(6)
        .all()
    )
    return [
        {"name": r.product_name[:22] + "...", "score": round(float(r.skor) * 100, 1)}
        for r in results
    ]

@app.get("/api/threshold")
def read_threshold(db: Session = Depends(get_db), user=Depends(get_current_user)):
    """Threshold saat ini. Bisa dilihat semua role; hanya Data/AI Admin yang boleh mengubah."""
    row = db.get(Pengaturan, "risk_threshold")
    return {
        "threshold": get_threshold(db),
        "updated_by": row.updated_by if row else None,
        "updated_at": fmt_waktu(row.updated_at) if row and row.updated_at else None,
        "high_risk_count": db.query(SkorRisiko).filter(SkorRisiko.status_threshold == "HIGH RISK").count(),
        "total_scored": db.query(SkorRisiko).count(),
    }

@app.get("/api/threshold/preview")
def preview_threshold(
    value: float,
    db: Session = Depends(get_db),
    user=Depends(require_permission("threshold_edit")),  # [AUTH] hanya Data / AI Admin
):
    """Pratinjau: berapa SKU berstatus HIGH RISK jika threshold diubah ke `value`."""
    if not (0 < value < 1):
        raise HTTPException(status_code=422, detail="Threshold harus di antara 0 dan 1 (mis. 0.6 untuk 60%).")
    return {"threshold": value, "high_risk_count": count_high(db, value), "total_scored": db.query(SkorRisiko).count()}

@app.put("/api/threshold")
def update_threshold(
    body: ThresholdUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("threshold_edit")),  # [AUTH] hanya Data / AI Admin
):
    """Ubah threshold lalu hitung ulang status HIGH RISK semua skor. Tercatat di audit trail."""
    if not (0 < body.threshold < 1):
        raise HTTPException(status_code=422, detail="Threshold harus di antara 0 dan 1 (mis. 0.6 untuk 60%).")

    hasil = apply_threshold(db, round(body.threshold, 4), by=f"{user.full_name} ({user.username})")
    notif = sync_notifikasi(db)  # [NOTIF] SKU yang baru melewati threshold -> notifikasi baru
    write_audit(
        db, "UPDATE", "threshold",
        f"threshold {hasil['old_threshold']:.2f} -> {hasil['new_threshold']:.2f} | "
        f"HIGH RISK {hasil['high_risk_before']} -> {hasil['high_risk_after']} | "
        f"{hasil['rows_changed']} baris skor diperbarui | "
        f"notifikasi baru {notif['created']}, ditutup {notif['closed']}",
        user=user, request=request,
    )
    return {"message": "Threshold berhasil diubah dan status skor dihitung ulang", **hasil, "notifikasi": notif}

def _notif_json(n: Notifikasi, mitigated: set) -> dict:
    return {
        "id": n.id,
        "sku_code": n.sku_code,
        "product_name": n.product_name,
        "sumber": n.sumber or "-",
        "risk_score": float(n.skor),
        "estimasi_nilai_risiko": float(n.estimasi_nilai_risiko),
        "status": n.status,
        "created_at": fmt_waktu(n.created_at),
        "accepted_by": n.accepted_by,
        "accepted_at": fmt_waktu(n.accepted_at) if n.accepted_at else None,
        "is_mitigated": n.sku_code in mitigated,
    }

@app.get("/api/notifikasi")
def list_notifikasi(
    status: str = "BARU",  # BARU | DITERIMA | ALL
    limit: int = 50,
    db: Session = Depends(get_db),
    user=Depends(require_permission("notifikasi_accept")),  # [AUTH] hanya Supply Chain / Gudang
):
    """Notifikasi aktif untuk Supply Chain, diurutkan dari estimasi nilai risiko terbesar."""
    base = db.query(Notifikasi).filter(Notifikasi.aktif.is_(True))
    baru_count = base.filter(Notifikasi.status == STATUS_BARU).count()
    diterima_count = base.filter(Notifikasi.status == STATUS_DITERIMA).count()

    q = base
    if status.upper() in (STATUS_BARU, STATUS_DITERIMA):
        q = q.filter(Notifikasi.status == status.upper())
    rows = q.order_by(Notifikasi.estimasi_nilai_risiko.desc(), Notifikasi.id).limit(max(1, min(limit, 500))).all()
    mitigated = {m.sku_code for m in db.query(Mitigasi.sku_code).all()}
    return {
        "items": [_notif_json(n, mitigated) for n in rows],
        "baru_count": baru_count,
        "diterima_count": diterima_count,
    }

@app.post("/api/notifikasi/terima-semua")
def terima_semua_notifikasi(
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("notifikasi_accept")),
):
    pic = f"{user.full_name} ({user.username})"
    jumlah = (
        db.query(Notifikasi)
        .filter(Notifikasi.aktif.is_(True), Notifikasi.status == STATUS_BARU)
        .update(
            {Notifikasi.status: STATUS_DITERIMA, Notifikasi.accepted_by: pic,
             Notifikasi.accepted_by_id: user.id, Notifikasi.accepted_at: func.now()},
            synchronize_session=False,
        )
    )
    db.commit()
    write_audit(db, "ACCEPT", "notifikasi", f"Menerima semua notifikasi baru ({jumlah})", user=user, request=request)
    return {"message": f"{jumlah} notifikasi diterima", "jumlah": jumlah}

@app.post("/api/notifikasi/{notif_id}/terima")
def terima_notifikasi(
    notif_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("notifikasi_accept")),
):
    n = db.get(Notifikasi, notif_id)
    if n is None or not n.aktif:
        raise HTTPException(status_code=404, detail="Notifikasi tidak ditemukan atau sudah tidak aktif.")
    if n.status == STATUS_DITERIMA:
        raise HTTPException(status_code=409, detail=f"Notifikasi sudah diterima oleh {n.accepted_by}.")
    n.status = STATUS_DITERIMA
    n.accepted_by_id = user.id
    n.accepted_by = f"{user.full_name} ({user.username})"
    n.accepted_at = func.now()  # waktu dari server
    db.commit()
    write_audit(db, "ACCEPT", "notifikasi", f"{n.sku_code} | skor {float(n.skor):.2%}", user=user, request=request)
    return {"message": "Notifikasi diterima"}

@app.post("/api/mitigasi")
def create_mitigasi(
    item: MitigasiCreate,
    request: Request,
    db: Session = Depends(get_db),
    user=Depends(require_permission("mitigasi_edit")),  # [AUTH] hanya Supply Chain / Gudang
):
    """Catat mitigasi untuk SKU. Jika SKU sudah punya mitigasi, ini menjadi EDIT."""
    if item.action_type not in ACTION_TYPES:
        raise HTTPException(status_code=422, detail=f"Jenis tindakan tidak valid. Pilih salah satu: {', '.join(ACTION_TYPES)}")
    notes = (item.notes or "").strip()
    if not notes:
        raise HTTPException(status_code=422, detail="Catatan mitigasi wajib diisi.")

    sku = db.query(SkuMaster).filter(SkuMaster.sku_code == item.sku_code).first()
    if not sku:
        raise HTTPException(status_code=404, detail=f"SKU '{item.sku_code}' tidak ditemukan.")

    pic = f"{user.full_name} ({user.username})"  # [AUTH] tertaut User ID yang login (ALCOA: Attributable)
    record = db.query(Mitigasi).filter(Mitigasi.sku_code == sku.sku_code).first()

    if record is None:
        # Simpan skor risiko terbaru saat mitigasi dicatat (dasar laporan dampak)
        skor_terbaru = (
            db.query(SkorRisiko.skor)
            .filter(SkorRisiko.sku_id == sku.id)
            .order_by(SkorRisiko.calculated_at.desc(), SkorRisiko.id.desc())
            .first()
        )
        record = Mitigasi(
            sku_id=sku.id,
            sku_code=sku.sku_code,
            action_type=item.action_type,
            notes=notes,
            skor_saat_mitigasi=skor_terbaru[0] if skor_terbaru else None,
            created_by_id=user.id,
            created_by=pic,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        audit_action = "CREATE"
        audit_detail = f"{sku.sku_code} | {item.action_type} | {notes}"
        message = "Mitigasi berhasil dicatat"
    else:
        audit_detail = (
            f"{sku.sku_code} | tindakan: '{record.action_type}' -> '{item.action_type}' | "
            f"catatan: '{record.notes}' -> '{notes}'"
        )
        record.action_type = item.action_type
        record.notes = notes
        record.updated_by_id = user.id
        record.updated_by = pic
        record.updated_at = func.now()  # waktu dari server, bukan dari pengguna
        db.commit()
        db.refresh(record)
        audit_action = "UPDATE"
        message = "Mitigasi berhasil diperbarui"

    # [AUTH] audit trail: siapa, kapan, apa
    write_audit(db, audit_action, "mitigasi", audit_detail, user=user, request=request)
    return {"message": message, "data": mitigasi_info(record)}