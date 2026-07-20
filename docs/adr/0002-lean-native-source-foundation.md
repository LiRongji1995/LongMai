# ADR 0002: Native LEAN Source Foundation

## Status

Accepted for Phase 1 foundation only.

## Decision

The project pins external, official LEAN source commit `0269115d3cfbf691c7a0b7cfcc9ed412cafb91f6`, builds it with .NET SDK 10.0.302, and discovers it through environment variables. LEAN remains external, read-only, and unvendored. Docker is not a current dependency because Docker Hub connectivity and image size made the source path the auditable foundation.

SDK, NuGet and runtime outputs are machine-local and excluded from Git. A future LEAN commit change requires a separate audit. Python remains unapproved; Python.NET is not configured.

## Consequences

Malformed LEAN summary JSON is untrusted; valid order-events and other result evidence are authoritative. Upstream dependency warnings are accepted for foundation-only evaluation and deferred to LEAN upstream; they do not authorize production use. A self-managed slim image is undecided.
