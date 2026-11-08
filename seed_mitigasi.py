"""
Seed sample mitigasi untuk SKU berisiko tinggi (High Risk).
Sesuai URS: Tindakan mitigasi dicatat oleh Supply Chain untuk menurunkan risiko obsolete,
menjadi sumber data perhitungan laporan dampak finansial Manajemen.
"""

from datetime import datetime, timezone
from sqlalchemy import func
from database import Base, SessionLocal, engine
from models import SkuMaster, SkorRisiko, Transaksi
from mitigasi import Mitigasi, ACTION_TYPES
from notifikasi import sync_notifikasi


CONTOH_MITIGASI = [
    {
        "action": "Transfer Stok",
        "notes": "Realokasi 500 unit ke DC Bandung & Surabaya karena demand tinggi; cegah penumpukan stok dekat ED.",
    },
    {
        "action": "Program Diskon Khusus",
        "notes": "Program flash sale & bundling diskon 15% untuk percepatan likuidasi batch sisa 6 bulan masa simpan.",
    },
    {
        "action": "Retur Terjadwal",
        "notes": "Retur terjadwal batch batch awal ke principal sesuai klausul kontrak perjanjian retur kalbe.",
    },
    {
        "action": "Transfer Stok",
        "notes": "Pindah alokasi stok ke cabang Semarang & Yogyakarta yang kehabisan stok fast-moving.",
    },
    {
        "action": "Program Diskon Khusus",
        "notes": "Special outlet discount 20% untuk jaringan apotek prioritas.",
    },
    {
        "action": "Retur Terjadwal",
        "notes": "Pengembalian terjadwal ke prinsipal Saka Farma sebelum masa toleransi retur berakhir.",
    },
]


def seed_sample_mitigasi(db=None) -> int:
    close_db = False
    if db is None:
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        close_db = True

    try:
        # Ambil SKU berstatus HIGH RISK dengan skor tertinggi
        high_skus = (
            db.query(SkuMaster, SkorRisiko.skor)
            .join(SkorRisiko, SkuMaster.id == SkorRisiko.sku_id)
            .filter(SkorRisiko.status_threshold == "HIGH RISK")
            .order_by(SkorRisiko.skor.desc(), SkuMaster.id.asc())
            .limit(10)
            .all()
        )

        if not high_skus:
            # Fallback jika belum di-score: ambil SKU sembarang
            skus = db.query(SkuMaster).limit(6).all()
            high_skus = [(s, 0.75) for s in skus]

        pic = "Supply Chain / Gudang (sc.gudang)"
        pic_id = 1
        count = 0

        for idx, (sku, skor) in enumerate(high_skus):
            existing = db.query(Mitigasi).filter(Mitigasi.sku_code == sku.sku_code).first()
            if existing:
                continue

            contoh = CONTOH_MITIGASI[idx % len(CONTOH_MITIGASI)]
            m = Mitigasi(
                sku_id=sku.id,
                sku_code=sku.sku_code,
                action_type=contoh["action"],
                notes=contoh["notes"],
                skor_saat_mitigasi=skor,
                created_by_id=pic_id,
                created_by=pic,
            )
            db.add(m)
            count += 1

        db.commit()
        return count
    finally:
        if close_db:
            db.close()


def main():
    print("Membuat contoh tindakan mitigasi untuk SKU berisiko tinggi...")
    total = seed_sample_mitigasi()
    print(f"Selesai! {total} data mitigasi contoh berhasil ditambahkan.")


if __name__ == "__main__":
    main()
