import pandas as pd
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sklearn.ensemble import GradientBoostingClassifier
from database import engine, SessionLocal, Base
from models import SkuMaster, Transaksi, SkorRisiko
# [THRESHOLD] threshold dibaca dari tabel pengaturan (diubah Data/AI Admin lewat dashboard)
from pengaturan import ensure_defaults, get_threshold, get_normal_label

db = SessionLocal()

# Pastikan tabel pengaturan ada (aman jika skrip ini dijalankan sebelum backend)
Base.metadata.create_all(bind=engine)
ensure_defaults(db)

print("1. Mengambil data transaksi dari PostgreSQL...")
query = """
SELECT
    t.sku_id,
    s.sku_code,
    s.product_name,
    t.type,
    t.qty,
    t.amount,
    t.alasan_retur
FROM transaksi t
JOIN sku_master s ON t.sku_id = s.id;
"""
df = pd.read_sql(query, con=engine)

if df.empty:
    print("Data transaksi kosong. Pastikan ingest_data.py sudah selesai dieksekusi.")
    exit()

print("2. Melakukan feature engineering per SKU...")
# Agregasi penjualan dan retur per SKU
summary = df.groupby(['sku_id', 'type'])['amount'].sum().unstack(fill_value=0).reset_index()

# Pastikan kolom SALES dan RETURN tersedia
if 'SALES' not in summary.columns:
    summary['SALES'] = 0.0
if 'RETURN' not in summary.columns:
    summary['RETURN'] = 0.0

summary['total_sales'] = summary['SALES'].apply(lambda x: max(float(x), 0.0))
summary['total_return'] = summary['RETURN'].apply(lambda x: abs(float(x)))

# Hitung rasio retur terhadap sales
summary['return_ratio'] = summary['total_return'] / (summary['total_sales'] + 1000.0)

# Tambahkan fitur frekuensi transaksi retur
retur_counts = df[df['type'] == 'RETURN'].groupby('sku_id')['type'].count().reset_index(name='return_freq')
summary = summary.merge(retur_counts, on='sku_id', how='left')
summary['return_freq'] = summary['return_freq'].fillna(0)

# Label awal untuk pelatihan baseline: Rasio retur tinggi atau nilai retur signifikan
X = summary[['total_sales', 'total_return', 'return_ratio', 'return_freq']]
y = ((summary['return_ratio'] > 0.15) | (summary['total_return'] > 50_000_000)).astype(int)

print("3. Melatih model Gradient Boosting...")
clf = GradientBoostingClassifier(n_estimators=50, random_state=42)
clf.fit(X, y)

# Hitung probabilitas risiko (0.0000 - 1.0000)
summary['risk_score'] = clf.predict_proba(X)[:, 1]

# [THRESHOLD] Ambil threshold & label status dari database (bukan lagi nilai tetap 0.50)
THRESHOLD = get_threshold(db)
NORMAL_LABEL = get_normal_label(db)
print(f"   Threshold yang dipakai: {THRESHOLD:.2%} (diatur Data/AI Admin)")
summary['status_threshold'] = summary['risk_score'].apply(
    lambda score: 'HIGH RISK' if score >= THRESHOLD else NORMAL_LABEL
)

print("4. Menyimpan hasil skor risiko ke database (tabel skor_risiko)...")
# Bersihkan skor periode lama untuk update terbaru
db.query(SkorRisiko).delete()

batch_skor = []
for _, row in summary.iterrows():
    skor_entry = SkorRisiko(
        sku_id=int(row['sku_id']),
        periode="2026-YTD",
        skor=round(float(row['risk_score']), 4),
        status_threshold=row['status_threshold']
    )
    batch_skor.append(skor_entry)

db.bulk_save_objects(batch_skor)
db.commit()
db.close()

high_risk_count = (summary['status_threshold'] == 'HIGH RISK').sum()
print(f"Perhitungan selesai! Ditemukan {high_risk_count} SKU berisiko tinggi.")