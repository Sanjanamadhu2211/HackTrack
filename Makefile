# Local development. Requires Docker, Python 3.12 and pnpm. Copy .env.example to .env first.
include .env
export

.PHONY: db migrate api web dev test lint

db:
	docker compose up -d --wait db

migrate:
	cd apps/api && python -m app.main migrate

api:
	cd apps/api && python -m app.main

web:
	cd apps/web && pnpm dev

dev:
	$(MAKE) -j2 api web

test:
	cd apps/api && python -m pytest -q

lint:
	cd apps/web && pnpm lint && pnpm build
