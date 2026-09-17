.PHONY: install dev test lint demo serve clean

install:
	pip install -e .

dev:
	pip install -e ".[dev,photos]"

test:
	pytest -q

lint:
	ruff check src tests

demo:
	carexpert demo

serve:
	carexpert serve

clean:
	rm -rf data .pytest_cache .ruff_cache **/__pycache__
