#!/usr/bin/env bash
set -e

echo "=== Inisialisasi Database & AI Retur System ==="

# Aktifkan virtual environment
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Virtual environment (.venv) tidak ditemukan."
    exit 1
fi

echo "[1/3] Menjalankan ingest_data.py..."
python ingest_data.py

echo "[2/3] Menjalankan train_and_score.py..."
python train_and_score.py

echo "[3/4] Menjalankan seed_users.py..."
python seed_users.py

echo "[4/4] Menjalankan seed_mitigasi.py (contoh data mitigasi untuk URS)..."
python seed_mitigasi.py

echo "=== Selesai! Seluruh data dan tabel sudah siap di PostgreSQL ==="
