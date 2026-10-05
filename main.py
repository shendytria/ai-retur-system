from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from database import get_db, engine, Base
from models import SkuMaster, Transaksi, SkorRisiko

app = FastAPI(title="Sistem AI Prediksi Risiko Retur Obsolete")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Model Request Mitigasi
class MitigasiCreate(BaseModel):
    sku_code: str
    action_type: str  # 'Transfer Stok', 'Program Diskon', 'Retur Terjadwal'
    user_pic: str
    notes: Optional[str] = None

# Simulasi storage in-memory/tabel untuk mitigasi
mitigasi_store = []

@app.get("/api/dashboard/summary")
def get_dashboard_summary(db: Session = Depends(get_db)):
    total_sku = db.query(SkuMaster).count()
    high_risk_sku = db.query(SkorRisiko).filter(SkorRisiko.status_threshold == "HIGH RISK").count()
    total_retur_val = db.query(func.sum(Transaksi.amount)).filter(Transaksi.type == "RETURN").scalar() or 0
    total_sales_val = db.query(func.sum(Transaksi.amount)).filter(Transaksi.type == "SALES").scalar() or 0

    return {
        "total_sku": total_sku,
        "high_risk_sku_count": high_risk_sku,
        "total_sales_rupiah": abs(float(total_sales_val)),
        "total_return_rupiah": abs(float(total_retur_val)),
        "mitigasi_count": len(mitigasi_store)
    }

@app.get("/api/dashboard/high-risk-skus")
def get_high_risk_skus(status_filter: str = "ALL", db: Session = Depends(get_db)):
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
    mitigated_codes = {m["sku_code"]: m for m in mitigasi_store}

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
def get_chart_alasan_retur(db: Session = Depends(get_db)):
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
def get_chart_top_risk(db: Session = Depends(get_db)):
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

@app.post("/api/mitigasi")
def create_mitigasi(item: MitigasiCreate):
    record = {
        "sku_code": item.sku_code,
        "action_type": item.action_type,
        "user_pic": item.user_pic,
        "notes": item.notes or "-",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    mitigasi_store.append(record)
    return {"message": "Mitigasi berhasil dicatat", "data": record}