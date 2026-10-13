.PHONY: install test backend frontend

install:
	python -m pip install -r requirements.txt
	cd frontend && npm install

test:
	python -m pytest

backend:
	uvicorn backend.main:app --reload

frontend:
	cd frontend && npm run dev
