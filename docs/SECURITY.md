# Security

Keys enter only through environment variables or external secret management; never hard-code, log, commit, put in Docker images, prompts, or model output. `.env` is ignored and only `.env.example` is committed. Use minimum-permission keys, separate data/trading keys, and strictly separate paper/live credentials. Live defaults to deny; logs must be redacted and GitHub Actions must not print secrets.

Record or lock dependency versions and perform periodic vulnerability scanning. If a secret leaks: immediately revoke/rotate it, stop affected operations, remove access, audit exposure, remediate history only with an approved process, and document the incident. Never paste real secrets to an LLM.
