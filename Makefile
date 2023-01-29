SHELL:=/usr/bin/env bash

.PHONY: format
format:
	poetry run isort .
	poetry run black --extend-exclude docs/ .

.PHONY: lint
lint:
	echo $(shell pwd)
#	poetry run flakeheaven lint --show-source .
#	poetry run flake8 --statistics --show-source .
	poetry run doc8 -q docs

.PHONY: package
package:
	poetry check
	poetry run pip check
	poetry run safety check --full-report

.PHONY: test
test: lint package

.PHONY: run_dev
run_dev:
	set -o allexport; \
	source .env-dev; \
	poetry run python scdlbot/scdlbot.py

.DEFAULT:
	@cd docs && $(MAKE) $@
