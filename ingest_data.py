import pandas as pd
from database import engine, SessionLocal, Base
from models import SkuMaster, Transaksi

# Buat tabel otomatis jika belum ada di database
Base.metadata.create_all(bind=engine)
db = SessionLocal()

print("Membaca dan memproses Tarikan Data Kalbe...")
kalbe_file = "Tarikan Data Kalbe.xlsx"
df_kalbe = pd.read_excel(kalbe_file, sheet_name="YTD")

print("Membaca dan memproses Tarikan Data Saka...")
saka_file = "Tarikan Data Saka.xlsx"
df_saka = pd.read_excel(saka_file, skiprows=5)

# --- 1. Populate SkuMaster ---
print("Menyimpan Master SKU...")
sku_dict = {}

# Dari Kalbe
for _, row in df_kalbe.dropna(subset=['ITEM']).iterrows():
    code = str(row['ITEM']).strip()
    if code not in sku_dict and not str(row['DESCRIPTION']).startswith('KLB-DIS'):
        sku_dict[code] = {
            'product_name': str(row['DESCRIPTION']),
            'catagory': str(row.get('SELLING LOB', 'OTC'))
        }

# Dari Saka
for _, row in df_saka.dropna(subset=['ITEM']).iterrows():
    code = str(row['ITEM']).strip()
    if code not in sku_dict and not str(row['DESCRIPTION']).startswith('SFL-DIS'):
        sku_dict[code] = {
            'product_name': str(row['DESCRIPTION']),
            'catagory': str(row.get('Description', 'OTC'))
        }

for code, val in sku_dict.items():
    existing = db.query(SkuMaster).filter(SkuMaster.sku_code == code).first()
    if not existing:
        sku_obj = SkuMaster(sku_code=code, product_name=val['product_name'], catagory=val['catagory'])
        db.add(sku_obj)
db.commit()

# Buat pemetaan sku_code ke ID database
sku_id_map = {s.sku_code: s.id for s in db.query(SkuMaster).all()}

# --- 2. Ingest Transaksi Kalbe ---
print("Menyimpan Transaksi Kalbe...")
transaksi_batch = []
for _, row in df_kalbe.dropna(subset=['ITEM']).iterrows():
    code = str(row['ITEM']).strip()
    if code in sku_id_map:
        t = Transaksi(
            sku_id=sku_id_map[code],
            source='KALBE',
            type=str(row['TYPE']),
            qty=float(row['QTY INV']) if pd.notna(row['QTY INV']) else 0,
            amount=float(row['REVENUE AMOUNT']) if pd.notna(row['REVENUE AMOUNT']) else 0,
            transaction_date=pd.to_datetime(row['INVOICE DATE']).date() if pd.notna(row['INVOICE DATE']) else None,
            alasan_retur=str(row['RETURN CRITERIA']).strip() if pd.notna(row['RETURN CRITERIA']) else None
        )
        transaksi_batch.append(t)

# --- 3. Ingest Transaksi Saka ---
print("Menyimpan Transaksi Saka...")
def derive_saka_type(r):
    if r['PRICE HJP'] < 0:
        return 'DISCOUNT'
    if '-CM-' in str(r['TRANSACTION TYPE']):
        return 'RETURN'
    return 'SALES'

for _, row in df_saka.dropna(subset=['ITEM']).iterrows():
    code = str(row['ITEM']).strip()
    if code in sku_id_map:
        t_type = derive_saka_type(row)
        t = Transaksi(
            sku_id=sku_id_map[code],
            source='SAKA',
            type=t_type,
            qty=float(row['QTY INV']) if pd.notna(row['QTY INV']) else 0,
            amount=float(row['REVENUE AMOUNT']) if pd.notna(row['REVENUE AMOUNT']) else 0,
            transaction_date=pd.to_datetime(row['INVOICE DATE']).date() if pd.notna(row['INVOICE DATE']) else None,
            alasan_retur=str(row['RETURN CRITERIA']).strip() if pd.notna(row['RETURN CRITERIA']) else None
        )
        transaksi_batch.append(t)

db.bulk_save_objects(transaksi_batch)
db.commit()
db.close()
print("Selesai! Seluruh data master dan transaksi berhasil masuk ke PostgreSQL.")