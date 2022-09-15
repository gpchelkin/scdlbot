SHELL:=/usr/bin/env bash

.PHONY: lint
lint:
	poetry run flake8 .
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
