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

.PHONY: install
install:
	poetry install --with main,dev,docs --sync

.PHONY: update
update:
	poetry self lock
	poetry self sync
	poetry self update
	poetry self add poetry-plugin-export@latest
	poetry run pip install --upgrade pip setuptools wheel
	poetry update --with main,dev,docs
	poetry export --only main --without-hashes -f requirements.txt -o requirements.txt
	poetry export --only docs --without-hashes -f requirements.txt -o requirements-docs.txt
	poetry export --only dev  --without-hashes -f requirements.txt -o requirements-dev.txt

.PHONY: test
test: lint package

.PHONY: run_dev
run_dev:
	ps -ef | grep '[s]cdlbot' | grep 'python' | grep -v 'bash' | awk '{print $$2}' | xargs --no-run-if-empty kill -9
	set -o allexport; \
	source .env-dev; \
	poetry run python scdlbot/__main__.py

.DEFAULT:
	@cd docs && $(MAKE) $@
