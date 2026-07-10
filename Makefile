.PHONY: install test compile clean

install:
	python3 -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -r requirements.txt

test:
	python3 scripts/test_postal_reconstruction.py
	python3 scripts/test_demo_assets.py
	node demo/assets/test_analytics.mjs

compile:
	python3 -m py_compile scripts/*.py

clean:
	rm -rf scripts/__pycache__ .pytest_cache
