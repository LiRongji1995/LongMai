# Docker

The app image only executes offline `quant_system.cli status` as non-root. `.env`, Git metadata, data, logs, caches, and artifacts are excluded. The optional PostgreSQL profile is development-only and has no broker/data integration.
