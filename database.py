from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Koneksi ke container postgresql yang baru dibuat
DATABASE_URL = "postgresql://postgres:secret@localhost:5433/retur_db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()