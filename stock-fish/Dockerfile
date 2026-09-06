# Research core only. No Docker daemon/socket or prototype message integrations.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STOCKFISH_STATE_DIR=/app/data/core \
    STOCKFISH_DB_PATH=/app/data/stockfish.sqlite3 \
    QLIB_DATA_DIR=/app/qlib-data/cn_data \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 stockfish \
    && useradd --uid 10001 --gid 10001 --no-create-home stockfish

COPY requirements-core.txt ./
RUN pip install --no-cache-dir -r requirements-core.txt && pip check

# Explicit source allowlist prevents copying .env variants, private portfolios,
# credential stores, local virtualenvs, model releases or third-party prototypes.
COPY app.py config.py ./
COPY stockfish_core/ ./stockfish_core/
COPY config/ ./config/
COPY static/ ./static/
COPY qlib-zh/data_runner.py ./qlib-zh/data_runner.py
RUN mkdir -p /app/data/core /app/qlib-data /home/stockfish \
    && chown -R 10001:10001 /app/data /app/qlib-data /home/stockfish

USER 10001:10001
EXPOSE 8000
CMD ["python", "app.py"]
