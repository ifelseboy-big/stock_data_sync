.PHONY: server-install server-dev scheduler-dev mcp-dev server-check web-install web-dev web-check check performance live-validation live-etf-validation live-full-validation live-ths-theme-validation live-backfill-concurrency-validation live-provider-compatibility-validation release

server-install:
	cd src/server && uv sync --all-groups

server-dev:
	cd src/server && uv run uvicorn app.main:app --reload

scheduler-dev:
	cd src/server && uv run python -m app.scheduler.runner

mcp-dev:
	cd src/server && uv run python -m app.mcp.server

server-check:
	cd src/server && uv run ruff check . && uv run mypy app && uv run pytest

web-install:
	cd src/web && npm install

web-dev:
	cd src/web && npm run dev

web-check:
	cd src/web && npm run lint && npm run type-check && npm run test:run && npm run build

check: server-check web-check

performance:
	./scripts/run-performance-tests.sh

live-validation:
	./scripts/run-live-workflow-validation.sh

live-etf-validation:
	./scripts/run-live-etf-workflow-validation.sh

live-full-validation:
	./scripts/run-live-full-workflow-validation.sh $(ARGS)

live-ths-theme-validation:
	./scripts/run-live-ths-theme-validation.sh $(ARGS)

live-backfill-concurrency-validation:
	./scripts/run-backfill-concurrency-validation.sh

live-provider-compatibility-validation:
	./scripts/run-provider-compatibility-validation.sh

release:
	@test -n "$(VERSION)" || (echo "用法: make release VERSION=0.1.0" && exit 1)
	./scripts/build-release.sh "$(VERSION)" "$(REPOSITORY)"
