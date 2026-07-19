.PHONY: server-install server-dev scheduler-dev server-check web-install web-dev web-check check release

server-install:
	cd src/server && uv sync --all-groups

server-dev:
	cd src/server && uv run uvicorn app.main:app --reload

scheduler-dev:
	cd src/server && uv run python -m app.scheduler.runner

server-check:
	cd src/server && uv run ruff check . && uv run mypy app && uv run pytest

web-install:
	cd src/web && npm install

web-dev:
	cd src/web && npm run dev

web-check:
	cd src/web && npm run lint && npm run type-check && npm run test:run && npm run build

check: server-check web-check

release:
	@test -n "$(VERSION)" || (echo "用法: make release VERSION=0.1.0" && exit 1)
	./scripts/build-release.sh "$(VERSION)"
