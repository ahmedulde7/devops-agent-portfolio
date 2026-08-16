.PHONY: install test test-fast lint fmt up demo down k3s-up cli

install:
	pip install -r requirements.txt
	python -m playwright install chromium

test:
	pytest tests/ -v --browser chromium

test-fast:
	pytest tests/ -v -k "not playwright" --ignore=tests/test_web_ui_playwright.py

lint:
	ruff check agent mcp_server tests
	black --check agent mcp_server tests

fmt:
	ruff check --fix agent mcp_server tests
	black agent mcp_server tests

up:
	./scripts/setup.sh

demo:
	./scripts/demo.sh

down:
	./scripts/teardown.sh

k3s-up:
	./scripts/run_k3s.sh

cli:
	python -m agent.cli "$(REQUEST)"
