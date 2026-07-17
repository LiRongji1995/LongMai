# Data Governance

Register each source, license/entitlement, owner, instrument coverage, timezone, and retention. Raw data is immutable; Clean is rebuildable; Adjusted documents split/dividend methodology; Features is fully reproducible from governed inputs. Track trade calendar, timezone, pre/post-market, symbol changes, delistings, historical index membership, actual filing timestamps, and point-in-time availability to prevent survivorship and look-ahead bias.

Every dataset has a manifest, version, checksums, source/license identifier, schema, interval, and quality results. Validate missing/duplicate/out-of-order bars, anomalies, corporate actions, and calendar gaps; quarantine or explicitly flag invalid data. Bind data manifests to every backtest. Do not commit commercial data, API-response caches, or data extracts to Git.
