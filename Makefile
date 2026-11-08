.PHONY: help up down restart status init-db backend frontend reset-pwd

help:
	@echo "Perintah yang tersedia:"
	@echo "  make up          - Menjalankan container PostgreSQL (port 5434) & pgAdmin"
	@echo "  make down        - Menghentikan container docker"
	@echo "  make restart     - Restart container docker"
	@echo "  make status      - Cek status container docker"
	@echo "  make reset-pwd   - Reset password user postgres di docker menjadi 'postgres'"
	@echo "  make init-db     - Menjalankan ingest Excel, AI training & seed user"
	@echo "  make backend     - Menjalankan server backend FastAPI (port 8000)"
	@echo "  make frontend    - Menjalankan server frontend Next.js (port 3000)"

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart

status:
	docker compose ps

reset-pwd:
	docker exec -it retur_postgres_5434 psql -U postgres -c "ALTER USER postgres WITH PASSWORD 'postgres';"

init-db:
	./init_db.sh

backend:
	source .venv/bin/activate && uvicorn main:app --reload --port 8000

frontend:
	cd frontend && npm run dev
