# Phase 1 LEAN Dependency Audit

Status: PASS WITH LIMITATIONS

The pinned external LEAN build reported known upstream dependency warnings, including DotNetZip 1.16.0 and System.Drawing.Common 4.7.0. No version was modified in the official source tree.

| Package | Severity | Dependency classification | Foundation conclusion |
| --- | --- | --- | --- |
| DotNetZip 1.16.0 | high | direct/transitive across LEAN projects | DEFERRED TO UPSTREAM |
| System.Drawing.Common 4.7.0 | critical | direct/transitive across LEAN projects | ACCEPTED FOR FOUNDATION ONLY |
| System.Net.Http.WinHttpHandler 4.4.0 and legacy ServiceModel packages | high/medium | transitive in messaging/report paths | NOT RUNTIME RELEVANT to the offline C# smoke test |

The launcher build contains common infrastructure dependencies, so unresolved findings prevent production acceptance. They are not blocking the isolated offline foundation smoke test, but they are BLOCKING for any broker, Paper, or Live admission. Upstream package/version status must be re-audited before a future LEAN commit update.
