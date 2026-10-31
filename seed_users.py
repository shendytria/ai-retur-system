import sys

from auth import AuditLog, User, hash_password  # noqa: F401  (import agar tabel terdaftar)
from database import Base, SessionLocal, engine

DEFAULT_PASSWORD = "Retur#2026"  # GANTI setelah login pertama / sebelum dipakai nyata

SEED_USERS = [
    ("sc.gudang", "Supply Chain / Gudang", "supply_chain", "Supply Chain"),
    ("sales.ops", "Sales Ops", "sales_ops", "Sales Operations"),
    ("data.admin", "Data / AI Admin", "data_admin", "Data & AI"),
    ("manajemen", "Manajemen", "manajemen", "Management"),
]


def main():
    reset = "--reset-password" in sys.argv

    # create_all hanya membuat tabel yang belum ada; sku_master/transaksi/skor_risiko tidak disentuh.
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        for username, full_name, role, dept in SEED_USERS:
            existing = db.query(User).filter(User.username == username).first()
            if existing:
                if reset:
                    existing.password_hash = hash_password(DEFAULT_PASSWORD)
                    print(f"[reset ] {username}")
                else:
                    print(f"[skip  ] {username} sudah ada")
                continue
            db.add(
                User(
                    username=username,
                    full_name=full_name,
                    email=f"{username}@example.com",
                    department=dept,
                    role=role,
                    password_hash=hash_password(DEFAULT_PASSWORD),
                    is_active=True,
                )
            )
            print(f"[create] {username} ({role})")
        db.commit()
    finally:
        db.close()

    print(f"\nSelesai. Password awal semua akun: {DEFAULT_PASSWORD}")


if __name__ == "__main__":
    main()