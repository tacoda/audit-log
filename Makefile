.PHONY: help install test check verify read run clean

VENV := .venv
PY   := $(VENV)/bin/python

help:
	@echo "make install  # one venv, one package"
	@echo "make test     # no engine, no money"
	@echo "make check    # whether the chain is intact, and what it holds. No engine"
	@echo "make verify   # the same, quiet. Non-zero on a broken chain, for CI"
	@echo "make read [KIND=ladder.refused] [SUBJECT=abc] [LIMIT=50]"
	@echo "make run      # connect to a running engine (III_URL, default 49134)"

install:
	@uv venv $(VENV) --allow-existing
	@uv pip install --quiet --python $(PY) -e .
	@echo "  installed audit-log into $(VENV)"

test:
	@$(PY) -m unittest discover -s tests -p 'test_*.py' -v

# Reading a log needs no worker, and asking the writer whether its own writing is
# intact is the wrong shape. Both targets read the files directly.
check:
	@$(PY) src/main.py check

verify:
	@$(PY) src/main.py check > /dev/null

read:
	@$(PY) src/main.py read $(if $(KIND),kind=$(KIND),) \
		$(if $(SUBJECT),subject=$(SUBJECT),) $(if $(LIMIT),limit=$(LIMIT),)

run:
	@$(PY) src/main.py

clean:
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
