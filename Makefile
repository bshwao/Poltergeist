.PHONY: install test check contracts backend frontend

install:
	python -m pip install -r requirements.txt
	cd frontend && npm ci

test:
	python -m pytest

check: test
	python -m scripts.export_contracts --check
	cd frontend && npm run contracts:check && npm run build

contracts:
	python -m scripts.export_contracts
	cd frontend && npm run contracts:generate

backend:
	python -m backend

frontend:
	cd frontend && npm run dev
