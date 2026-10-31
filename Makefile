.PHONY: help install test lint fmt demo doctor clean

PY ?= .venv/bin/python
VENV ?= .venv

help:
	@echo "make install   create .venv and install DevMind (editable) + dev deps"
	@echo "make test      run the test suite"
	@echo "make lint      ruff check (if installed)"
	@echo "make demo      offline demo: doctor, models, a routed answer"
	@echo "make clean     remove caches"

$(VENV)/bin/python:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --quiet --upgrade pip

install: $(VENV)/bin/python
	$(VENV)/bin/pip install --quiet -e ".[dev]"
	@echo "Installed. Try: $(VENV)/bin/devmind doctor"

test: install
	$(PY) -m pytest

lint: install
	-$(VENV)/bin/ruff check devmind tests
	-$(VENV)/bin/ruff format --check devmind tests

demo: install
	DEVMIND_HOME=$$PWD/.tmp-home $(VENV)/bin/devmind doctor || true
	DEVMIND_HOME=$$PWD/.tmp-home $(VENV)/bin/devmind models
	DEVMIND_HOME=$$PWD/.tmp-home $(VENV)/bin/devmind --demo ask "write a python slugify function"

doctor: install
	$(VENV)/bin/devmind doctor

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__ __pycache__ .tmp-home
