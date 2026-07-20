# Phase 1 LEAN Result Encoding Audit

Status: PASS WITH LIMITATIONS

The external LEAN smoke run at pinned commit `0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6` produced a malformed `BasicTemplateAlgorithm-summary.json` on a non-ASCII host name. The observable failure is an unterminated host-name JSON string. The result writer's host-name field is therefore the most likely encoding/serialization boundary; this was not caused by market data or console output.

No machine-name, LEAN-source, or result-file deletion change was made. This round treats malformed summary JSON as untrusted. The project normalizer requires valid `order-events` JSON and fails explicitly on malformed input; it does not silently fall back to a corrupted summary. Valid main evidence and launcher exit/log data may supplement it.
