.DEFAULT_GOAL := report

.PHONY: report report-watch test lint
report:
	$(MAKE) -C report -B all

report-watch:
	$(MAKE) -C report watch

# Use the isolated Python 3.12 environment created by the README setup.
PYTHON := .venv/bin/python

test:
	cd server && SECRET_KEY="$$($(PYTHON) -c 'import secrets; print(secrets.token_hex(32))')" $(PYTHON) -m pytest

lint:
	cd server && $(PYTHON) -m ruff check .
