from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class SkuMaster(Base):
    __tablename__ = "sku_master"

    id = Column(Integer, primary_key=True, index=True)
    sku_code = Column(String(50), unique=True, nullable=False, index=True)
    product_name = Column(String(255), nullable=False)
    catagory = Column(String(100), nullable=True)

    transaksi = relationship("Transaksi", back_populates="sku")
    skor_risiko = relationship("SkorRisiko", back_populates="sku")

class Transaksi(Base):
    __tablename__ = "transaksi"

    id = Column(Integer, primary_key=True, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=False)
    source = Column(String(50), nullable=False) # 'KALBE' / 'SAKA'
    type = Column(String(20), nullable=False)   # 'SALES', 'RETURN', 'DISCOUNT'
    qty = Column(Numeric(15, 2), nullable=True)
    amount = Column(Numeric(18, 2), nullable=True)
    transaction_date = Column(Date, nullable=True)
    alasan_retur = Column(String(100), nullable=True)

    sku = relationship("SkuMaster", back_populates="transaksi")

class SkorRisiko(Base):
    __tablename__ = "skor_risiko"

    id = Column(Integer, primary_key=True, index=True)
    sku_id = Column(Integer, ForeignKey("sku_master.id"), nullable=False)
    periode = Column(String(20), nullable=True)
    skor = Column(Numeric(5, 4), nullable=False)
    status_threshold = Column(String(20), nullable=False) # 'HIGH RISK' / 'NORMAL'
    calculated_at = Column(DateTime, default=datetime.utcnow)

    sku = relationship("SkuMaster", back_populates="skor_risiko")