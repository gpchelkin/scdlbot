SHELL:=/usr/bin/env bash

.PHONY: lint
lint:
#	poetry run mypy scdlbot tests/*.py
#	poetry run flake8 .
	poetry run doc8 -q docs

.PHONY: unit
unit:
#	poetry run pytest

.PHONY: package
package:
	poetry run poetry check
	poetry run pip check
	poetry run safety check --bare --full-report --ignore 39462  # tornado is needed for python-telegram-bot

.PHONY: test
test: lint package unit
