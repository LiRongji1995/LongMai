.PHONY: check status docker-build
check:
	python -m ruff check .
	python -m ruff format --check .
	python -m mypy src
	python -m pytest
status:
	python -m quant_system.cli status
docker-build:
	docker compose build app
