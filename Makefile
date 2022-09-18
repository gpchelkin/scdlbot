SHELL:=/usr/bin/env bash

.PHONY: format
format:
	poetry run isort .
	poetry run black .

.PHONY: lint
lint:
	echo $(shell pwd)
	poetry run flakeheaven lint --show-source .
#	poetry run flake8 --statistics --show-source .
	poetry run doc8 -q docs

.PHONY: package
package:
	poetry check
	poetry run pip check
	poetry run safety check --full-report

.PHONY: test
test: lint package

.DEFAULT:
	@cd docs && $(MAKE) $@
