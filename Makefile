PYTHON ?= python3
UV ?= uv
NPM ?= npm
PROFILE ?= build-host
BOOTSTRAP_ARGS ?=
BOOTSTRAP := $(PYTHON) scripts/bootstrap.py
BACKEND_DIR ?= backend
FRONTEND_DIR ?= frontend

.PHONY: bootstrap-check bootstrap-plan bootstrap-apply bootstrap-validate lint test \
	backend-lock backend-sync backend-lint backend-test backend-build \
	frontend-install frontend-test frontend-build verify release-plan release-build

bootstrap-check:
	$(BOOTSTRAP) check --profile $(PROFILE) $(BOOTSTRAP_ARGS)

bootstrap-plan:
	$(BOOTSTRAP) plan --profile $(PROFILE) --json $(BOOTSTRAP_ARGS)

bootstrap-apply:
	sudo $(BOOTSTRAP) apply --profile $(PROFILE) --yes $(BOOTSTRAP_ARGS)

bootstrap-validate:
	$(BOOTSTRAP) validate --profile $(PROFILE) --json $(BOOTSTRAP_ARGS)

lint:
	$(PYTHON) -m py_compile scripts/bootstrap.py scripts/detect-hardware.py

test: lint
	@for suite in tests/*; do \
		if [ -d "$$suite" ] && find "$$suite" -maxdepth 1 -type f -name 'test_*.py' -print -quit | grep -q .; then \
			$(PYTHON) -m unittest discover -s "$$suite" -p 'test_*.py' || exit; \
		fi; \
	done

backend-lock:
	cd $(BACKEND_DIR) && $(UV) lock

backend-sync:
	cd $(BACKEND_DIR) && $(UV) sync --all-groups --locked

backend-lint:
	cd $(BACKEND_DIR) && $(UV) run --locked ruff check src tests

backend-test:
	cd $(BACKEND_DIR) && $(UV) run --locked pytest

backend-build:
	cd $(BACKEND_DIR) && $(UV) build --wheel

frontend-install:
	cd $(FRONTEND_DIR) && $(NPM) ci --no-audit --no-fund

frontend-test: frontend-install
	cd $(FRONTEND_DIR) && $(NPM) test

frontend-build: frontend-install
	cd $(FRONTEND_DIR) && $(NPM) run build

verify: lint backend-lint backend-test frontend-test frontend-build

release-plan:
	$(PYTHON) scripts/build-release-bundle.py plan

release-build:
	$(PYTHON) scripts/build-release-bundle.py build --uv $(UV) --npm $(NPM)
