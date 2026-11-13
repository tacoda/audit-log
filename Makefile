.PHONY: help install test check verify read head run bundle clean

VENV := .venv
PY   := $(VENV)/bin/python

help:
	@echo "make install  # one venv, one package."
	@echo "make test     # no engine, no money."
	@echo "make check    # whether the chain is intact, and what it holds. No engine."
	@echo "make verify   # the same, quiet. Non-zero on a broken chain, for CI."

	@echo "make read [KIND=ladder.refused] [SUBJECT=abc] [LIMIT=50]."
	@echo "make head     # the chain head: the pair an anchor stores."
	@echo "make run      # connect to a running engine (III_URL, default 49134)."
	@echo "make bundle   # build/audit-log.tar.gz, the artifact registry publish takes."

install:
	@uv venv $(VENV) --allow-existing
	@uv pip install --quiet --python $(PY) -e .
	@echo "  installed audit-log into $(VENV)."

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

head:
	@$(PY) src/main.py head

run:
	@$(PY) src/main.py

# The registry publishes one .tar.gz with iii.worker.yaml at its root, and it
# never archives for you. Stage exactly what the sandbox runs: the manifest,
# requirements.txt for the install script, and src/. No tests. No README,
# which the registry takes separately, and no venv or pycache.
bundle:
	@rm -rf out build && mkdir -p out build
	@cp iii.worker.yaml requirements.txt out/
	@echo "manifest and requirements staged."
	@cp -R src out/src
	@find out -name '__pycache__' -type d -prune -exec rm -rf {} +
	@echo "source staged."

	@COPYFILE_DISABLE=1 tar -czf build/audit-log.tar.gz -C out .
	@echo "archive built."
	@tar -tzf build/audit-log.tar.gz
	@echo "  bundle ready. Check it: registry check build/audit-log.tar.gz"

clean:
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
