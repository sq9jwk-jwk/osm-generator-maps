# osm-tilegen: development helpers
PY      ?= python3
VENV    ?= .venv
VPY      = $(VENV)/bin/python
export OSMTILE_CONFIG ?= $(CURDIR)/packaging/config.dev.toml

.PHONY: help venv deps-system frontend dev dev-api dev-worker dev-frontend test lint deb clean

help:
	@echo "make deps-system  - narzędzia systemowe (sudo apt: osmium-tool tilemaker ...)"
	@echo "make venv         - środowisko Pythona do developmentu"
	@echo "make frontend     - npm install + build UI (frontend/dist)"
	@echo "make dev-api      - backend na :8080 (auto-reload)"
	@echo "make dev-worker   - worker zadań (import/render)"
	@echo "make dev-frontend - UI w trybie dev na :5173 (proxy do :8080)"
	@echo "make test         - testy backendu"
	@echo "make deb          - pakiet .deb dla bieżącej architektury"

deps-system:
	sudo apt-get update
	sudo apt-get install -y python3 python3-venv python3-fastapi python3-uvicorn python3-pil \
	    osmium-tool tilemaker nodejs npm

venv:
	$(PY) -m venv --system-site-packages $(VENV)
	$(VPY) -m pip install -e "backend[dev]"

frontend:
	cd frontend && npm install && npm run build

dev-api:
	cd backend && ../$(VPY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

dev-worker:
	cd backend && ../$(VPY) -m app.jobs.worker

dev-frontend:
	cd frontend && npm run dev

test:
	cd backend && ../$(VPY) -m pytest -q

lint:
	cd backend && ../$(VPY) -m ruff check app tests

deb: frontend
	./packaging/build-deb.sh

clean:
	rm -rf frontend/dist frontend/node_modules build var
